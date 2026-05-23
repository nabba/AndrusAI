"""Tests for app.upgrade_lifecycle.idle_jobs (F6).

PROGRAM §63 follow-up. Covers the three LIGHT idle-job entry points:

  1.  Snapshot job: skipped outside January window
  2.  Snapshot job: skipped when snapshot already exists for the year
  3.  Snapshot job: generates when in window + no snapshot exists
  4.  Snapshot job: status="skipped_disabled" when master switch off
  5.  Capability-adoption job: forwards return value
  6.  Goodhart job: writes state file on first run
  7.  Goodhart job: respects 7-day internal cadence
  8.  get_idle_jobs returns 3 tuples with LIGHT weight
  9.  All entry points are failure-isolated (don't raise on errors)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.upgrade_lifecycle import idle_jobs as ij


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("UPGRADE_LIFECYCLE_DIR", str(tmp_path / "ul"))
    return tmp_path / "ul"


# ── 1-4: Snapshot job ───────────────────────────────────────────────────


def test_snapshot_job_skipped_outside_january_window(isolated_dir, monkeypatch):
    """In June, no snapshot generation attempt."""
    real_dt = datetime
    class _FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return real_dt(2026, 6, 15, tzinfo=tz or timezone.utc)
    monkeypatch.setattr(ij, "datetime", _FakeDT)

    generate_called = []
    monkeypatch.setattr(
        "app.upgrade_lifecycle.ecosystem_snapshot.generate_snapshot",
        lambda **kw: generate_called.append(kw),
    )

    out = ij.run_annual_snapshot()
    assert out["status"] == "skipped_outside_window"
    assert generate_called == []


def test_snapshot_job_skipped_when_exists(isolated_dir, monkeypatch):
    """In January window but snapshot already on disk → skipped_exists."""
    real_dt = datetime
    class _FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return real_dt(2026, 1, 5, tzinfo=tz or timezone.utc)
    monkeypatch.setattr(ij, "datetime", _FakeDT)

    # Seed a snapshot via the real generator
    from app.upgrade_lifecycle import ecosystem_snapshot as eco
    monkeypatch.setattr(eco, "_enabled", lambda: True)
    eco.generate_snapshot(
        year=2026, now=datetime(2026, 1, 5, tzinfo=timezone.utc),
        framework_fetcher=lambda pkg: {"latest_version": "x"},
        cost_fetcher=lambda: {}, capability_iterator=lambda: [],
        dependency_radar_state={},
    )

    out = ij.run_annual_snapshot()
    assert out["status"] == "skipped_exists"


def test_snapshot_job_generates_in_window(isolated_dir, monkeypatch):
    """First January-window tick of a year → snapshot generates."""
    real_dt = datetime
    class _FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return real_dt(2026, 1, 3, tzinfo=tz or timezone.utc)
    monkeypatch.setattr(ij, "datetime", _FakeDT)

    from app.upgrade_lifecycle import ecosystem_snapshot as eco
    monkeypatch.setattr(eco, "_enabled", lambda: True)
    # Patch network-default fetchers
    monkeypatch.setattr(eco, "_default_framework_fetcher",
                       lambda pkg: {"latest_version": "x"})
    monkeypatch.setattr(eco, "_default_cost_by_provider", lambda: {})

    out = ij.run_annual_snapshot()
    assert out["status"] == "ok"
    assert out["year"] == 2026
    # Persisted
    assert eco._snapshot_path_for_year(2026).exists()


def test_snapshot_job_skipped_when_master_switch_off(isolated_dir, monkeypatch):
    real_dt = datetime
    class _FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return real_dt(2026, 1, 3, tzinfo=tz or timezone.utc)
    monkeypatch.setattr(ij, "datetime", _FakeDT)

    from app.upgrade_lifecycle import ecosystem_snapshot as eco
    monkeypatch.setattr(eco, "_enabled", lambda: False)

    out = ij.run_annual_snapshot()
    assert out["status"] == "skipped_disabled"


# ── 5: Capability-adoption job forwards result ──────────────────────────


def test_capability_adoption_job_forwards_result(isolated_dir, monkeypatch):
    canned = {"cr_filed": False, "reason": "no_capabilities",
              "budget_remaining_usd": 20.0, "crs_this_week": 0}
    monkeypatch.setattr(
        "app.upgrade_lifecycle.capability_adoption.run_one_pass",
        lambda: canned,
    )
    out = ij.run_capability_adoption()
    assert out == canned


def test_capability_adoption_job_catches_exception(isolated_dir, monkeypatch):
    def _explode():
        raise RuntimeError("simulated")
    monkeypatch.setattr(
        "app.upgrade_lifecycle.capability_adoption.run_one_pass",
        _explode,
    )
    out = ij.run_capability_adoption()
    assert out == {"reason": "error"}


# ── 6-7: Goodhart job cadence ───────────────────────────────────────────


def test_goodhart_job_writes_state_on_first_run(isolated_dir, monkeypatch):
    # No state file → runs evaluators + persists state
    eval_calls = {"major": 0, "adoption": 0}
    def _major(now=None, audit_path=None):
        eval_calls["major"] += 1
        return 30
    def _adoption(now=None, audit_path=None):
        eval_calls["adoption"] += 1
        return None
    monkeypatch.setattr(
        "app.upgrade_lifecycle.goodhart.evaluate_major_window", _major,
    )
    monkeypatch.setattr(
        "app.upgrade_lifecycle.goodhart.evaluate_adoption_pause", _adoption,
    )

    out = ij.run_goodhart_throttle()
    assert out["status"] == "ok"
    assert out["major_window_days"] == 30
    assert out["adoption_paused_until"] is None
    assert eval_calls == {"major": 1, "adoption": 1}

    # State persisted
    state_path = ij._throttle_state_path()
    assert state_path.exists()
    state = json.loads(state_path.read_text())
    assert state["major_window_days"] == 30


def test_goodhart_job_respects_7day_cadence(isolated_dir, monkeypatch):
    """Second tick within 7 days = skipped_recent."""
    # Seed state file with a recent run.
    state_path = ij._throttle_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "last_run_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        "major_window_days": 30,
        "adoption_paused_until": None,
    }))

    eval_calls = {"major": 0}
    monkeypatch.setattr(
        "app.upgrade_lifecycle.goodhart.evaluate_major_window",
        lambda now=None, audit_path=None: (eval_calls.update({"major": eval_calls["major"] + 1}) or 30),
    )
    out = ij.run_goodhart_throttle()
    assert out["status"] == "skipped_recent"
    assert eval_calls["major"] == 0    # NOT invoked


# ── 8: get_idle_jobs shape ──────────────────────────────────────────────


def test_get_idle_jobs_returns_three_light_tuples(isolated_dir):
    pytest.importorskip("pydantic_settings")
    jobs = ij.get_idle_jobs()
    assert len(jobs) == 3
    names = {j[0] for j in jobs}
    assert names == {
        "upgrade-ecosystem-snapshot",
        "upgrade-capability-adoption",
        "upgrade-lifecycle-goodhart",
    }
    # All LIGHT
    from app.idle_scheduler import JobWeight
    assert all(j[2] == JobWeight.LIGHT for j in jobs)


# ── 9: Failure isolation ────────────────────────────────────────────────


def test_snapshot_job_catches_generate_exception(isolated_dir, monkeypatch):
    real_dt = datetime
    class _FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return real_dt(2026, 1, 3, tzinfo=tz or timezone.utc)
    monkeypatch.setattr(ij, "datetime", _FakeDT)

    def _explode(**kw):
        raise RuntimeError("simulated")
    monkeypatch.setattr(
        "app.upgrade_lifecycle.ecosystem_snapshot.generate_snapshot",
        _explode,
    )

    out = ij.run_annual_snapshot()
    assert out["status"] == "error"


def test_goodhart_job_handles_corrupt_state_file(isolated_dir, monkeypatch):
    state_path = ij._throttle_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("not json{")   # corrupt

    monkeypatch.setattr(
        "app.upgrade_lifecycle.goodhart.evaluate_major_window",
        lambda now=None, audit_path=None: 60,
    )
    monkeypatch.setattr(
        "app.upgrade_lifecycle.goodhart.evaluate_adoption_pause",
        lambda now=None, audit_path=None: None,
    )
    # Should proceed as if first run.
    out = ij.run_goodhart_throttle()
    assert out["status"] == "ok"
    assert out["major_window_days"] == 60
