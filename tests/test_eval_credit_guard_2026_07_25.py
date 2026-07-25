"""The eval harness must refuse to record a baseline during a credit outage.

Finding 0 of ``reports/GATE_DIAGNOSIS_2026-07-25.md``: the 2026-07-24 run
recorded 2/12 delivered and that number was believed. It was measured while
OpenRouter credits were exhausted — 69 HTTP 402s in 38 minutes, every one
failing over to ``ollama/llama3.1:8b``, which cannot call tools. The harness
had no way to notice, which is the real defect: an instrument that can't detect
its own invalid conditions keeps producing confident wrong numbers.

These tests pin the guard: refuse to start during an outage, count credit
errors per question, abort mid-run the moment one appears, and never report
``valid: true`` for a contaminated run.

No gateway needed — ``send_one`` is stubbed.
"""
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_RUN_EVAL = Path(__file__).resolve().parents[1] / "evals" / "run_eval.py"


@pytest.fixture()
def rv():
    """Load evals/run_eval.py by path (evals/ is a script dir, not a package)."""
    if not _RUN_EVAL.exists():  # pragma: no cover
        pytest.skip("evals/run_eval.py not present")
    spec = importlib.util.spec_from_file_location("_run_eval_under_test", _RUN_EVAL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop(spec.name, None)


def _write_log(path: Path, entries):
    with open(path, "w") as handle:
        for ts, message in entries:
            handle.write(json.dumps({"ts": ts, "message": message}) + "\n")


def _iso(seconds_ago: float) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    ).isoformat()


_CREDIT_LINE = (
    "failover: credit error on 'openrouter/anthropic/claude-opus-4.7' "
    "→ retrying once with 'ollama/llama3.1:8b' (max_tokens=4096)"
)


# ── the watcher ─────────────────────────────────────────────────────────────


def test_watcher_sees_a_recent_credit_error(rv, tmp_path):
    log = tmp_path / "errors.jsonl"
    _write_log(log, [(_iso(60), _CREDIT_LINE)])

    watcher = rv.CreditErrorWatcher(log)
    assert watcher.available
    assert watcher.recent() == 1


def test_watcher_ignores_an_old_credit_error(rv, tmp_path):
    log = tmp_path / "errors.jsonl"
    _write_log(log, [(_iso(rv._PREFLIGHT_WINDOW_S + 600), _CREDIT_LINE)])

    assert rv.CreditErrorWatcher(log).recent() == 0


def test_watcher_ignores_unrelated_errors(rv, tmp_path):
    log = tmp_path / "errors.jsonl"
    _write_log(log, [(_iso(30), "neo4j connection refused")])

    assert rv.CreditErrorWatcher(log).recent() == 0


def test_watcher_polls_only_new_lines(rv, tmp_path):
    log = tmp_path / "errors.jsonl"
    _write_log(log, [(_iso(30), _CREDIT_LINE)])

    watcher = rv.CreditErrorWatcher(log)
    assert watcher.poll() == 0, "pre-existing lines must not be attributed"

    with open(log, "a") as handle:
        handle.write(json.dumps({"ts": _iso(0), "message": _CREDIT_LINE}) + "\n")
    assert watcher.poll() == 1
    assert watcher.poll() == 0


def test_watcher_handles_a_rotated_log(rv, tmp_path):
    log = tmp_path / "errors.jsonl"
    _write_log(log, [(_iso(30), _CREDIT_LINE)] * 5)
    watcher = rv.CreditErrorWatcher(log)

    _write_log(log, [(_iso(0), _CREDIT_LINE)])  # truncated + rewritten
    assert watcher.poll() == 1


def test_watcher_reports_unavailability_rather_than_zero(rv, tmp_path):
    watcher = rv.CreditErrorWatcher(tmp_path / "nope.jsonl")

    assert not watcher.available
    assert watcher.unavailable_reason
    assert watcher.recent() == 0  # but `available` is the signal, not this


# ── the pre-flight ──────────────────────────────────────────────────────────


def test_preflight_refuses_to_start_during_an_outage(rv, tmp_path):
    log = tmp_path / "errors.jsonl"
    _write_log(log, [(_iso(120), _CREDIT_LINE)] * 8)

    with pytest.raises(rv.CreditOutage) as excinfo:
        rv.preflight_credit_check(rv.CreditErrorWatcher(log))

    assert "refusing to start" in str(excinfo.value).lower()


def test_preflight_passes_when_credits_are_healthy(rv, tmp_path):
    log = tmp_path / "errors.jsonl"
    _write_log(log, [(_iso(30), "some unrelated warning")])

    assert rv.preflight_credit_check(rv.CreditErrorWatcher(log)) == "ok"


def test_preflight_can_be_overridden_explicitly(rv, tmp_path):
    log = tmp_path / "errors.jsonl"
    _write_log(log, [(_iso(120), _CREDIT_LINE)] * 8)

    status = rv.preflight_credit_check(
        rv.CreditErrorWatcher(log), allow=True,
    )
    assert "outage-at-start" in status


def test_preflight_is_loud_when_it_cannot_look(rv, tmp_path, capsys):
    status = rv.preflight_credit_check(rv.CreditErrorWatcher(tmp_path / "nope.jsonl"))

    assert "unavailable" in status
    err = capsys.readouterr().err
    assert "DISABLED" in err, "a silent skip is how the invalid baseline happened"


# ── the run loop ────────────────────────────────────────────────────────────


def _stub_golden_set(rv, monkeypatch, n=4):
    items = [
        {"id": f"q{i}", "category": "report", "prompt": f"question {i}"}
        for i in range(n)
    ]
    monkeypatch.setattr(rv, "load_golden_set", lambda *a, **k: items)
    return items


def test_run_aborts_when_credit_errors_appear_mid_run(rv, tmp_path, monkeypatch):
    """The 07-24 failure mode: keep spending budget on an already-void run."""
    log = tmp_path / "errors.jsonl"
    _write_log(log, [])
    _stub_golden_set(rv, monkeypatch, n=4)

    asked = []

    def fake_send(base_url, sender, prompt, timeout_s):
        asked.append(prompt)
        if len(asked) == 2:  # credits die during question 2, as they really did
            with open(log, "a") as handle:
                handle.write(
                    json.dumps({"ts": _iso(0), "message": _CREDIT_LINE}) + "\n"
                )
        return "a plausible looking answer of sufficient length", 1.0, None

    monkeypatch.setattr(rv, "send_one", fake_send)

    report = rv.run(
        "http://x", "eval", 10.0, None,
        watcher=rv.CreditErrorWatcher(log),
    )

    assert len(asked) == 2, "must stop instead of burning budget on void rows"
    assert report.valid is False
    assert "q1" in report.invalid_reason
    summary = report.summary()
    assert summary["valid"] is False
    assert summary["credit_errors"] == 1
    assert summary["questions_with_credit_errors"] == 1


def test_run_completes_and_is_valid_when_credits_hold(rv, tmp_path, monkeypatch):
    log = tmp_path / "errors.jsonl"
    _write_log(log, [])
    items = _stub_golden_set(rv, monkeypatch, n=3)

    monkeypatch.setattr(
        rv, "send_one",
        lambda *a, **k: ("a plausible looking answer of sufficient length", 1.0, None),
    )

    report = rv.run(
        "http://x", "eval", 10.0, None,
        watcher=rv.CreditErrorWatcher(log),
    )

    assert len(report.results) == len(items)
    summary = report.summary()
    assert summary["valid"] is True
    assert summary["credit_errors"] == 0
    assert summary["delivered"] == 3


def test_run_continues_past_credit_errors_when_explicitly_allowed(
    rv, tmp_path, monkeypatch,
):
    """--allow-credit-errors still finishes, but the report is marked invalid."""
    log = tmp_path / "errors.jsonl"
    _write_log(log, [])
    _stub_golden_set(rv, monkeypatch, n=3)

    def fake_send(base_url, sender, prompt, timeout_s):
        with open(log, "a") as handle:
            handle.write(json.dumps({"ts": _iso(0), "message": _CREDIT_LINE}) + "\n")
        return "an answer from the local failover model, tool-free", 1.0, None

    monkeypatch.setattr(rv, "send_one", fake_send)

    report = rv.run(
        "http://x", "eval", 10.0, None,
        watcher=rv.CreditErrorWatcher(log),
        allow_credit_errors=True,
    )

    assert len(report.results) == 3
    assert report.summary()["valid"] is False, (
        "credit errors must invalidate the report even when the run was allowed"
    )


def test_rescore_does_not_re_bless_an_invalid_report(rv, tmp_path):
    """Re-scoring is about markers, not about how the run was conducted."""
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({
        "base_url": "http://x",
        "sender": "eval",
        "results": [{
            "id": "q0", "category": "report", "prompt": "p", "ok": True,
            "delivered": True, "latency_s": 1.0, "reply_chars": 500,
            "reply_preview": "a fine answer", "credit_errors": 3,
        }],
        "summary": {"valid": False, "invalid_reason": "credit outage mid-run"},
    }))

    payload = rv.rescore(report_path)

    assert payload["summary"]["valid"] is False
    assert "credit outage" in payload["summary"]["invalid_reason"]


def test_rescore_flags_a_report_predating_the_credit_guard(rv, tmp_path):
    report_path = tmp_path / "old.json"
    report_path.write_text(json.dumps({
        "base_url": "http://x", "sender": "eval",
        "results": [{
            "id": "q0", "category": "report", "prompt": "p", "ok": True,
            "delivered": True, "latency_s": 1.0, "reply_chars": 500,
            "reply_preview": "a fine answer",
        }],
        "summary": {},
    }))

    payload = rv.rescore(report_path)
    assert "pre-dates" in payload["summary"]["credit_watch"]
