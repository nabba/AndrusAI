"""Tests for app.upgrade_lifecycle.orchestrator (U4 wiring).

PROGRAM §62. Covers orchestration of U1+U2+U3-lookup+U4:

  1.  Trial lookup persistence round-trip
  2.  Trial lookup returns None when no file exists
  3.  request_trial appends a marker row
  4.  Orchestrator returns (False, outcome with trial_not_run) when no cached trial
  5.  Orchestrator calls the stage function when the gate passes
  6.  Orchestrator falls through cleanly when capability extraction fails
  7.  Orchestrator falls through when impact has breaking hits
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.upgrade_lifecycle import orchestrator as orc
from app.upgrade_lifecycle.protocol import Capability, ImpactReport, TrialResult


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("UPGRADE_LIFECYCLE_DIR", str(tmp_path / "ul"))
    return tmp_path / "ul"


def _passing_trial(pkg: str = "somelib", to_ver: str = "2.0.0") -> TrialResult:
    return TrialResult(
        package=pkg, from_version="1.0.0", to_version=to_ver,
        status="ok", pass_count=42, fail_count=0,
    )


def _now() -> datetime:
    return datetime(2026, 5, 23, tzinfo=timezone.utc)


def _pypi_md(version: str, days_ago: int = 60) -> dict:
    upload_time = (_now() - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S")
    return {
        "info": {"version": version, "project_urls": {}},
        "releases": {version: [{"upload_time": upload_time}]},
    }


# ── 1-2: Trial lookup persistence ───────────────────────────────────────


def test_trial_lookup_returns_none_when_missing(isolated_dir):
    assert orc.lookup_trial("somelib", "2.0.0") is None


def test_trial_persist_and_lookup_round_trip(isolated_dir):
    trial = _passing_trial()
    orc.persist_trial(trial)
    loaded = orc.lookup_trial("somelib", "2.0.0")
    assert loaded is not None
    assert loaded.status == "ok"
    assert loaded.pass_count == 42


def test_trial_persist_overwrites_same_key(isolated_dir):
    orc.persist_trial(_passing_trial())
    # Persist a failing trial for the same package+version — should
    # overwrite, not create a sibling row.
    failing = TrialResult(
        package="somelib", from_version="1.0.0", to_version="2.0.0",
        status="test_failure", pass_count=40, fail_count=2,
    )
    orc.persist_trial(failing)
    loaded = orc.lookup_trial("somelib", "2.0.0")
    assert loaded is not None
    assert loaded.status == "test_failure"


# ── 3: request_trial appends a pending row ──────────────────────────────


def test_request_trial_appends_marker(isolated_dir):
    orc.request_trial("somelib", "2.0.0")
    orc.request_trial("otherlib", "3.0.0")
    pending = isolated_dir / "trials" / "_pending.jsonl"
    assert pending.exists()
    lines = pending.read_text().strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["package"] == "somelib"
    assert first["to_version"] == "2.0.0"


# ── 4: Orchestrator falls through when no cached trial ──────────────────


def test_orchestrator_falls_through_when_no_trial(isolated_dir):
    """No trial result on disk → gate fails on trial_not_run."""
    fake_md = lambda pkg: _pypi_md("2.0.0")   # passing PyPI metadata
    fake_llm_builder = lambda: _stub_llm_returning(_good_llm_reply())

    staged: list[dict] = []
    requests: list[tuple[str, str]] = []

    filed, outcome = orc.try_auto_cr_for_major(
        package="somelib", from_version="1.0.0", to_version="2.0.0",
        metadata_fetcher=fake_md,
        llm_builder=fake_llm_builder,
        stage_fn=lambda **kw: staged.append(kw),
        request_trial_fn=lambda p, v: requests.append((p, v)),
        impact_repo_root=isolated_dir,  # empty tree — no impact hits possible
        now=_now(),
    )
    assert filed is False
    assert outcome is not None
    assert outcome.reason == "trial_not_run"
    assert staged == []
    assert ("somelib", "2.0.0") in requests   # trial scheduled


# ── 5: Orchestrator stages CR when gate passes ──────────────────────────


def test_orchestrator_stages_cr_when_all_conditions_hold(isolated_dir):
    """All five gate conditions satisfied → CR is staged."""
    # Seed a passing trial result for somelib 2.0.0
    orc.persist_trial(_passing_trial())

    fake_md = lambda pkg: _pypi_md("2.0.0")
    fake_llm_builder = lambda: _stub_llm_returning(_good_llm_reply())

    staged: list[dict] = []

    # Empty impact repo → no breaking hits, no tier_immutable
    filed, outcome = orc.try_auto_cr_for_major(
        package="somelib", from_version="1.0.0", to_version="2.0.0",
        metadata_fetcher=fake_md,
        llm_builder=fake_llm_builder,
        stage_fn=lambda **kw: staged.append(kw),
        impact_repo_root=isolated_dir,
        now=_now(),
    )
    assert filed is True
    assert outcome is not None
    assert outcome.passed is True
    assert len(staged) == 1
    assert staged[0]["source"] == "dependency_radar"
    assert "somelib" in staged[0]["title"]


# ── 6: Orchestrator handles capability-extraction failure ───────────────


def test_orchestrator_falls_through_when_capability_extraction_fails(isolated_dir):
    """If U1 raises, we still attempt the gate (which will fail on
    impact_not_run since impact can't run without capability)."""
    orc.persist_trial(_passing_trial())

    def _exploding_llm_builder():
        raise RuntimeError("simulated LLM crash")

    staged: list[dict] = []
    filed, outcome = orc.try_auto_cr_for_major(
        package="somelib", from_version="1.0.0", to_version="2.0.0",
        metadata_fetcher=lambda pkg: _pypi_md("2.0.0"),
        llm_builder=_exploding_llm_builder,
        stage_fn=lambda **kw: staged.append(kw),
        impact_repo_root=isolated_dir,
        now=_now(),
    )
    assert filed is False
    assert outcome is not None
    assert outcome.reason == "impact_not_run"
    assert staged == []


# ── 7: Gate fails on framework exclusion ────────────────────────────────


def test_orchestrator_refuses_framework_package(isolated_dir):
    orc.persist_trial(TrialResult(
        package="crewai", from_version="0.1", to_version="1.0",
        status="ok", pass_count=10, fail_count=0,
    ))
    staged: list[dict] = []
    filed, outcome = orc.try_auto_cr_for_major(
        package="crewai", from_version="0.1", to_version="1.0",
        metadata_fetcher=lambda pkg: _pypi_md("1.0"),
        llm_builder=lambda: _stub_llm_returning(_good_llm_reply()),
        stage_fn=lambda **kw: staged.append(kw),
        impact_repo_root=isolated_dir,
        now=_now(),
    )
    assert filed is False
    assert outcome is not None
    assert outcome.reason.startswith("framework_exclusion")
    assert staged == []


# ── Helpers ──────────────────────────────────────────────────────────────


def _good_llm_reply() -> str:
    return json.dumps({
        "new_features": ["async support added"],
        "deprecations": [],
        "breaking_changes": [],
        "security_fixes": [],
        "perf_notes": [],
        "notes": "",
    })


class _StubLLM:
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.calls = 0

    def call(self, _messages):
        self.calls += 1
        return self._reply


def _stub_llm_returning(reply: str):
    return _StubLLM(reply)
