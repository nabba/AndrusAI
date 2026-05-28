"""Pins the 2026-05-28 alignment-audit de-noising fix.

Context: a 2026-05-27 "CRITICAL DRIFT" (score 0.50, then 0.70 an hour later)
was a self-referential artifact. The TIER_IMMUTABLE auditor was:
  1. fed the evolution loop's raw variant hypotheses and laundered their
     frozen numbers ("145.5s latency", "50% success") into measured
     "founding-protocol violations";
  2. conflating operational performance (latency / success rate / errors)
     with constitutional VALUES drift;
  3. re-paging hourly because the idle scheduler invokes it every cycle and
     it had no cadence debounce;
  4. reading a DARK benchmark harness (a since-fixed dead-import outage that
     left only errored runs) as "the system fails every task".

The fix is additive + reversible and lives in app/alignment_audit.py.
These tests pin each guarantee so a future edit can't quietly undo them.

Every assertion message starts with "ALIGNMENT DE-NOISE 2026-05-28:" so a
failure lands the next dev directly on this context.
"""
from __future__ import annotations

import json
import time

import pytest

import app.alignment_audit as aa


_P = "ALIGNMENT DE-NOISE 2026-05-28:"


# ── helpers ───────────────────────────────────────────────────────────


def _write_reports(path, rows):
    path.write_text(json.dumps(rows))


def _report_row(drift, ts):
    return {
        "timestamp": ts, "drift_score": drift, "severity": "x",
        "summary": "prior", "concerns": [], "recommendations": [],
        "constitution_hash": "h", "audited_souls": [], "ops_health": {},
    }


class _FakeLLM:
    """Returns a fixed drift score; counts calls so tests can assert the
    LLM was (or was NOT) reached."""

    def __init__(self, drift, calls):
        self._drift = drift
        self._calls = calls

    def call(self, prompt):  # noqa: D401 - mimic the real .call surface
        self._calls.append(prompt)
        return json.dumps({
            "drift_score": self._drift,
            "summary": "values look fine",
            "concerns": [],
            "recommendations": [],
        })


def _present_constitution(tmp_path, monkeypatch):
    const = tmp_path / "const.md"
    const.write_text("# Constitution\nBe honest. Be safe.")
    monkeypatch.setattr(aa, "CONSTITUTION_PATH", const)


# ── 1. counts-only variant input (no number laundering) ──────────────


def test_recent_changes_summary_is_counts_only(monkeypatch):
    leak = "the response time is 145.5s average and the 50% success rate"
    monkeypatch.setattr(
        "app.variant_archive.get_recent_variants",
        lambda n: [{"status": "keep", "delta": 0.0, "hypothesis": leak}],
    )
    text = aa._gather_recent_changes_summary()
    assert leak not in text, f"{_P} raw hypothesis text leaked into auditor input"
    assert "145.5" not in text, f"{_P} a frozen perf number leaked into auditor input"
    assert "keep=1" in text, f"{_P} structural counts must still be passed"
    assert "counts only" in text.lower(), f"{_P} section must be labelled counts-only"


# ── 2. dark / unreliable benchmark is not a quality signal ───────────


def test_ops_snapshot_dark_when_no_recent_runs(monkeypatch):
    monkeypatch.setattr("app.benchmarks.load_all", lambda: [])
    snap = aa._ops_health_snapshot()
    assert snap["benchmark"]["state"] == "dark", (
        f"{_P} an empty benchmark store must read as DARK, never pass_rate=0"
    )


def test_telemetry_text_marks_dark_as_infrastructure(monkeypatch):
    monkeypatch.setattr("app.benchmarks.load_all", lambda: [])
    text = aa._gather_operational_telemetry()
    assert "DARK" in text and "INFRASTRUCTURE" in text, (
        f"{_P} dark harness must be flagged as infra state, not quality"
    )
    assert "Do NOT infer a success rate" in text, (
        f"{_P} auditor must be told not to infer a success rate from a dark suite"
    )


# ── 3. cadence debounce: no hourly re-paging ─────────────────────────


def test_cadence_debounce_returns_cached_without_llm(tmp_path, monkeypatch):
    _present_constitution(tmp_path, monkeypatch)
    reports = tmp_path / "reports.json"
    _write_reports(reports, [_report_row(0.33, time.time())])  # fresh prior
    monkeypatch.setattr(aa, "ALIGNMENT_REPORTS_PATH", reports)
    monkeypatch.setattr(aa, "_load_interval_days", lambda: 7)

    def _boom():
        raise AssertionError(f"{_P} LLM must not be built within the cadence window")

    monkeypatch.setattr("app.llm_factory.create_vetting_llm", _boom, raising=False)
    rep = aa.run_alignment_audit()  # not forced
    assert rep.drift_score == 0.33, (
        f"{_P} within cadence the prior report must be returned verbatim"
    )


def test_force_bypasses_debounce(tmp_path, monkeypatch):
    _present_constitution(tmp_path, monkeypatch)
    reports = tmp_path / "reports.json"
    _write_reports(reports, [_report_row(0.33, time.time())])  # fresh prior
    monkeypatch.setattr(aa, "ALIGNMENT_REPORTS_PATH", reports)
    monkeypatch.setattr(aa, "_load_interval_days", lambda: 7)
    monkeypatch.setattr(aa, "_send_alert", lambda r: None)
    calls: list = []
    monkeypatch.setattr(
        "app.llm_factory.create_vetting_llm",
        lambda: _FakeLLM(0.05, calls), raising=False,
    )
    rep = aa.run_alignment_audit(force=True)
    assert calls, f"{_P} force=True must bypass the debounce and run the audit"
    assert rep.drift_score == 0.05, f"{_P} forced run must use the fresh LLM score"


# ── 4. corroboration gate: a lone spike must not Signal-page ─────────


def test_first_time_critical_does_not_page(tmp_path, monkeypatch):
    _present_constitution(tmp_path, monkeypatch)
    reports = tmp_path / "reports.json"
    _write_reports(reports, [_report_row(0.0, time.time())])  # prior below alert
    monkeypatch.setattr(aa, "ALIGNMENT_REPORTS_PATH", reports)
    sent: list = []
    monkeypatch.setattr(aa, "_send_alert", lambda r: sent.append(r))
    monkeypatch.setattr(
        "app.llm_factory.create_vetting_llm",
        lambda: _FakeLLM(0.9, []), raising=False,
    )
    rep = aa.run_alignment_audit(force=True)
    assert rep.severity == "drift_critical", f"{_P} 0.9 must classify as critical"
    assert sent == [], (
        f"{_P} a first-time critical (prior below alert) must NOT Signal-page"
    )


def test_corroborated_critical_pages(tmp_path, monkeypatch):
    _present_constitution(tmp_path, monkeypatch)
    reports = tmp_path / "reports.json"
    _write_reports(reports, [_report_row(0.5, time.time())])  # prior >= alert
    monkeypatch.setattr(aa, "ALIGNMENT_REPORTS_PATH", reports)
    sent: list = []
    monkeypatch.setattr(aa, "_send_alert", lambda r: sent.append(r))
    monkeypatch.setattr(
        "app.llm_factory.create_vetting_llm",
        lambda: _FakeLLM(0.9, []), raising=False,
    )
    rep = aa.run_alignment_audit(force=True)
    assert rep.severity == "drift_critical"
    assert len(sent) == 1, (
        f"{_P} a critical corroborated by a prior >= alert must page exactly once"
    )


# ── 5. report round-trips through the cadence-skip path ──────────────


def test_report_from_dict_tolerates_missing_ops_health():
    rep = aa._report_from_dict({
        "timestamp": 1.0, "drift_score": 0.2, "severity": "drift_alert",
        "summary": "s",  # no ops_health key (older row)
    })
    assert rep.ops_health == {}, f"{_P} older rows without ops_health must not crash"
    assert rep.drift_score == 0.2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
