"""Cadence-gate invariants + restart-herd control (Phase 3, 2026-06-12).

The 2026-06 wedge loop had an amplifier: cadence gates tracked
``time.monotonic()`` in process memory, so every watchdog restart made all
gated jobs instantly due — the catch-up herd re-wedged the gateway the
restart was meant to save. These tests pin the fix:

  1. Wall-clock last-run persists in the idle_job_state dbm and survives a
     "restart" (state cleared + reloaded from disk).
  2. Deterministic, bounded, cadence-scaled jitter (crc32 — NOT salted
     hash()).
  3. EVERY registered MEDIUM/HEAVY job has a _HEAVY_MIN_CADENCE entry
     (the heaviest holders being absent from the map is exactly how
     alignment-audit ran every cycle instead of weekly).
  4. No stale cadence keys (the 'evolution' key matched no job for weeks).
  5. The 2026-06-08 wedge trio (fiction-ingest / discover-topics /
     self-model-refresh) never returns to the LIGHT pool.
  6. A backwards clock step can never wedge a gate shut.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("pydantic_settings")  # idle_scheduler pulls gateway config

import app.idle_scheduler as sched  # noqa: E402


@pytest.fixture()
def clean_state(monkeypatch, tmp_path):
    """Redirect the persisted job-state dbm to a tmp file + clear memory."""
    monkeypatch.setattr(sched, "_JOB_STATE_PATH", str(tmp_path / "idle_job_state"))
    monkeypatch.setattr(sched, "_job_last_run_wall", {})
    monkeypatch.setattr(sched, "_job_failure_counts", {})
    monkeypatch.setattr(sched, "_job_skip_until", {})
    return tmp_path


# ── 1. persistence across "restart" ───────────────────────────────────────


def test_last_run_survives_restart(clean_state, monkeypatch):
    monkeypatch.setattr(sched, "_LIGHT_CADENCE_GATING_ENABLED", True)
    monkeypatch.setitem(sched._LIGHT_MIN_CADENCE, "phase3-test-job", 3600)

    assert sched._light_job_allowed("phase3-test-job") is True   # first run
    assert sched._light_job_allowed("phase3-test-job") is False  # gated

    # Simulate restart: wipe in-memory state, reload from the dbm.
    sched._job_last_run_wall.clear()
    sched._load_job_state()
    assert "phase3-test-job" in sched._job_last_run_wall, (
        "last-run must reload from the persisted dbm"
    )
    assert sched._light_job_allowed("phase3-test-job") is False, (
        "REGRESSION: restart reset the cadence gate — this is the "
        "post-restart herd amplifier (pre-Phase-3 monotonic behavior)"
    )


def test_due_job_still_runs_after_restart(clean_state, monkeypatch):
    monkeypatch.setattr(sched, "_HEAVY_CADENCE_GATING_ENABLED", True)
    monkeypatch.setitem(sched._HEAVY_MIN_CADENCE, "phase3-due-job", 60)
    # Persist a last-run far in the past.
    old = time.time() - 86400
    sched._job_last_run_wall["phase3-due-job"] = old
    sched._persist_job_last_run("phase3-due-job", old)

    sched._job_last_run_wall.clear()
    sched._load_job_state()
    assert sched._heavy_job_allowed("phase3-due-job") is True, (
        "a genuinely-due job must run after restart"
    )


# ── 2. jitter ─────────────────────────────────────────────────────────────


def test_jitter_deterministic_and_bounded():
    j1 = sched._cadence_jitter("some-job", 86400)
    j2 = sched._cadence_jitter("some-job", 86400)
    assert j1 == j2, "jitter must be stable across calls (and processes)"
    assert 0 <= j1 < 600, "daily-cadence jitter is bounded to <600s"
    # Scales down with cadence.
    assert sched._cadence_jitter("some-job", 3600) == pytest.approx(j1 / 24)
    # Differs across names (spreading).
    names = [f"job-{i}" for i in range(20)]
    values = {sched._cadence_jitter(n, 86400) for n in names}
    assert len(values) > 10, "jitter must spread distinct names"


# ── 3+4. cadence-map coverage invariants ─────────────────────────────────


def _medium_heavy_names():
    jobs = sched._default_jobs()
    return sorted({
        e[0] for e in jobs
        if (e[2] if len(e) >= 3 else sched.JobWeight.MEDIUM)
        in (sched.JobWeight.MEDIUM, sched.JobWeight.HEAVY)
    })


def test_every_medium_heavy_job_has_cadence_entry():
    missing = [n for n in _medium_heavy_names() if n not in sched._HEAVY_MIN_CADENCE]
    assert not missing, (
        f"MEDIUM/HEAVY jobs without a cadence entry run on EVERY round-robin "
        f"rotation (the alignment-audit-every-cycle bug class): {missing}. "
        f"Add them to _HEAVY_MIN_CADENCE."
    )


def test_no_stale_cadence_entries():
    registered = set(_medium_heavy_names())
    stale = [n for n in sched._HEAVY_MIN_CADENCE if n not in registered]
    assert not stale, (
        f"cadence-map keys matching no registered MEDIUM/HEAVY job (renamed "
        f"job? the 'evolution' key silently matched nothing for weeks): {stale}"
    )


# ── 5. wedge-trio classification pin ─────────────────────────────────────


def test_wedge_trio_never_light_again():
    jobs = sched._default_jobs()
    weights = {
        e[0]: (e[2] if len(e) >= 3 else sched.JobWeight.MEDIUM) for e in jobs
    }
    for name in ("fiction-ingest", "discover-topics", "self-model-refresh"):
        assert name in weights, f"{name} job missing from registry"
        assert weights[name] is not sched.JobWeight.LIGHT, (
            f"REGRESSION (2026-06-08 wedge loop): {name} must never return "
            f"to the non-deferrable LIGHT pool"
        )


# ── 6. clock-skew robustness ─────────────────────────────────────────────


def test_clock_backwards_never_wedges_gate(clean_state, monkeypatch):
    monkeypatch.setattr(sched, "_HEAVY_CADENCE_GATING_ENABLED", True)
    monkeypatch.setitem(sched._HEAVY_MIN_CADENCE, "phase3-skew-job", 3600)
    # last-run recorded "in the future" (NTP step backwards after write).
    sched._job_last_run_wall["phase3-skew-job"] = time.time() + 10 * 86400
    # Gate must normalize (treat as just-ran), NOT stay shut for 10 days +
    # cadence. Immediately after normalization the job is gated (just ran)…
    assert sched._heavy_job_allowed("phase3-skew-job") is False
    # …but from "now", not from the bogus future stamp.
    assert sched._job_last_run_wall["phase3-skew-job"] <= time.time() + 1
