"""Tests for app.upgrade_lifecycle.trial_scheduler (F2).

PROGRAM §63 follow-up. Covers the queue consumer + cooldown + idempotency:

  1. Empty queue → no_pending
  2. Single pending row processed; trial persisted; queue drained
  3. Duplicate queue rows collapse to one execution
  4. Cooldown blocks same (pkg, ver) within 7 days
  5. Cooldown allows different (pkg, ver) through
  6. Cooldown record persists across ticks
  7. Master switch OFF returns immediately
  8. Runner exception caught and recorded as infrastructure_error
  9. Pending file rewritten atomically on success
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.upgrade_lifecycle import trial_scheduler as ts
from app.upgrade_lifecycle.protocol import TrialResult


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("UPGRADE_LIFECYCLE_DIR", str(tmp_path / "ul"))
    return tmp_path / "ul"


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(ts, "_enabled", lambda: True)


def _seed_pending(isolated_dir, rows):
    pending = isolated_dir / "trials" / "_pending.jsonl"
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(
        "\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n",
    )


def _passing_trial(pkg, ver) -> TrialResult:
    return TrialResult(
        package=pkg, from_version="1.0.0", to_version=ver,
        status="ok", pass_count=42, fail_count=0,
    )


def _runner_returning(result_or_factory):
    """Build a runner that returns either a fixed result or
    one constructed per-call from kwargs."""
    def _r(*, package, from_version, to_version, repo_root):
        if callable(result_or_factory):
            return result_or_factory(package, to_version)
        return result_or_factory
    return _r


# ── 1: Empty queue ──────────────────────────────────────────────────────


def test_empty_queue_returns_no_pending(isolated_dir, enabled):
    out = ts.run_one_tick(repo_root_override=isolated_dir)
    assert out["processed"] is False
    assert out["reason"] == "no_pending"


# ── 2: Single row processed end-to-end ──────────────────────────────────


def test_single_row_processed(isolated_dir, enabled):
    _seed_pending(isolated_dir, [
        {"package": "alpha", "to_version": "2.0.0",
         "from_version": "1.0.0",
         "requested_at": "2026-05-23T00:00:00+00:00"},
    ])

    runner = _runner_returning(_passing_trial("alpha", "2.0.0"))
    out = ts.run_one_tick(runner=runner, repo_root_override=isolated_dir)
    assert out["processed"] is True
    assert out["reason"] == "ok"
    assert out["status"] == "ok"

    # Pending file drained
    pending_remaining = (isolated_dir / "trials" / "_pending.jsonl").read_text()
    assert pending_remaining.strip() == ""

    # Trial result persisted via orchestrator
    from app.upgrade_lifecycle.orchestrator import lookup_trial
    loaded = lookup_trial("alpha", "2.0.0")
    assert loaded is not None
    assert loaded.status == "ok"


# ── 3: Duplicate rows collapse ──────────────────────────────────────────


def test_duplicate_rows_collapse(isolated_dir, enabled):
    """Two identical requests yield one execution."""
    _seed_pending(isolated_dir, [
        {"package": "alpha", "to_version": "2.0.0",
         "from_version": "1.0.0",
         "requested_at": "2026-05-23T00:00:00+00:00"},
        {"package": "alpha", "to_version": "2.0.0",
         "from_version": "1.0.0",
         "requested_at": "2026-05-23T01:00:00+00:00"},
    ])

    calls: list[tuple[str, str]] = []
    def _runner(*, package, from_version, to_version, repo_root):
        calls.append((package, to_version))
        return _passing_trial(package, to_version)

    ts.run_one_tick(runner=_runner, repo_root_override=isolated_dir)
    assert calls == [("alpha", "2.0.0")]


# ── 4-6: Cooldown ───────────────────────────────────────────────────────


def test_cooldown_blocks_same_pair_within_window(isolated_dir, enabled):
    """Same (pkg, ver) attempted twice within 7 days → second blocked."""
    _seed_pending(isolated_dir, [
        {"package": "alpha", "to_version": "2.0.0",
         "from_version": "1.0.0",
         "requested_at": "2026-05-23T00:00:00+00:00"},
    ])

    runner = _runner_returning(_passing_trial("alpha", "2.0.0"))
    # First tick: processes
    ts.run_one_tick(
        runner=runner, repo_root_override=isolated_dir,
        now=datetime(2026, 5, 23, tzinfo=timezone.utc),
    )

    # Re-queue same pair
    _seed_pending(isolated_dir, [
        {"package": "alpha", "to_version": "2.0.0",
         "from_version": "1.0.0",
         "requested_at": "2026-05-24T00:00:00+00:00"},
    ])

    # Second tick 3 days later — cooldown still active
    out = ts.run_one_tick(
        runner=runner, repo_root_override=isolated_dir,
        now=datetime(2026, 5, 26, tzinfo=timezone.utc),
    )
    assert out["processed"] is False
    assert out["reason"] == "all_in_cooldown"


def test_cooldown_allows_different_pair(isolated_dir, enabled):
    """A different (pkg, ver) in the same queue runs even if the first is in cooldown."""
    runner = _runner_returning(
        lambda pkg, ver: _passing_trial(pkg, ver),
    )
    # Burn alpha into cooldown
    _seed_pending(isolated_dir, [
        {"package": "alpha", "to_version": "2.0.0",
         "from_version": "1.0.0",
         "requested_at": "2026-05-23T00:00:00+00:00"},
    ])
    ts.run_one_tick(
        runner=runner, repo_root_override=isolated_dir,
        now=datetime(2026, 5, 23, tzinfo=timezone.utc),
    )

    # Now queue both alpha (cooldown) + beta (fresh)
    _seed_pending(isolated_dir, [
        {"package": "alpha", "to_version": "2.0.0",
         "from_version": "1.0.0",
         "requested_at": "2026-05-24T00:00:00+00:00"},
        {"package": "beta", "to_version": "3.0.0",
         "from_version": "2.5.0",
         "requested_at": "2026-05-24T01:00:00+00:00"},
    ])
    out = ts.run_one_tick(
        runner=runner, repo_root_override=isolated_dir,
        now=datetime(2026, 5, 24, tzinfo=timezone.utc),
    )
    assert out["processed"] is True
    assert out["package"] == "beta"


def test_cooldown_expires_after_window(isolated_dir, enabled):
    """8 days later, the cooldown is gone."""
    _seed_pending(isolated_dir, [
        {"package": "alpha", "to_version": "2.0.0",
         "from_version": "1.0.0",
         "requested_at": "2026-05-23T00:00:00+00:00"},
    ])
    runner = _runner_returning(_passing_trial("alpha", "2.0.0"))
    ts.run_one_tick(
        runner=runner, repo_root_override=isolated_dir,
        now=datetime(2026, 5, 23, tzinfo=timezone.utc),
    )

    _seed_pending(isolated_dir, [
        {"package": "alpha", "to_version": "2.0.0",
         "from_version": "1.0.0",
         "requested_at": "2026-06-01T00:00:00+00:00"},
    ])
    out = ts.run_one_tick(
        runner=runner, repo_root_override=isolated_dir,
        now=datetime(2026, 6, 1, tzinfo=timezone.utc),    # 9 days later
    )
    assert out["processed"] is True


# ── 7: Master switch ────────────────────────────────────────────────────


def test_master_switch_off_returns_immediately(isolated_dir, monkeypatch):
    monkeypatch.setattr(ts, "_enabled", lambda: False)
    _seed_pending(isolated_dir, [
        {"package": "alpha", "to_version": "2.0.0",
         "from_version": "1.0.0",
         "requested_at": "2026-05-23T00:00:00+00:00"},
    ])
    runner_calls: list = []
    def _runner(**kw):
        runner_calls.append(kw)
        return _passing_trial("alpha", "2.0.0")
    out = ts.run_one_tick(runner=_runner, repo_root_override=isolated_dir)
    assert out["processed"] is False
    assert out["reason"] == "master_switch_off"
    assert runner_calls == []   # runner never called


# ── 8: Runner exception ────────────────────────────────────────────────


def test_runner_exception_caught_and_recorded(isolated_dir, enabled):
    """Crash inside runner produces an infrastructure_error result."""
    _seed_pending(isolated_dir, [
        {"package": "alpha", "to_version": "2.0.0",
         "from_version": "1.0.0",
         "requested_at": "2026-05-23T00:00:00+00:00"},
    ])

    def _exploding_runner(**kw):
        raise RuntimeError("simulated crash")

    out = ts.run_one_tick(
        runner=_exploding_runner, repo_root_override=isolated_dir,
    )
    assert out["processed"] is True
    assert out["status"] == "infrastructure_error"

    from app.upgrade_lifecycle.orchestrator import lookup_trial
    loaded = lookup_trial("alpha", "2.0.0")
    assert loaded is not None
    assert loaded.status == "infrastructure_error"
    assert "simulated crash" in loaded.failures[0]


# ── 9: Atomic queue rewrite ─────────────────────────────────────────────


# ── A1-P0: thread-liveness ─────────────────────────────────────────────


def test_start_refuses_when_thread_alive(isolated_dir, enabled, monkeypatch):
    """Calling start() twice while the thread is alive → second is a no-op."""
    import threading
    started_count = [0]
    original_thread = threading.Thread

    class _CountingThread(original_thread):
        def start(self):
            started_count[0] += 1
            super().start()

    monkeypatch.setattr("threading.Thread", _CountingThread)
    # Force-clear any prior state so first start fires the thread.
    monkeypatch.setattr(ts, "_driver_started", False)
    ts._stop_event.clear()

    try:
        first = ts.start()
        assert first is True
        # Second call while thread is alive → no-op.
        second = ts.start()
        assert second is False
        assert started_count[0] == 1
    finally:
        ts.stop()
        # Wait for thread to actually exit so subsequent tests aren't poisoned.
        for t in threading.enumerate():
            if t.name == ts.DAEMON_THREAD_NAME:
                t.join(timeout=2.0)


def test_start_respawns_after_thread_dies(isolated_dir, enabled, monkeypatch):
    """The critical watchdog-respawn case: thread dies → start() spins a new one."""
    import threading
    # First start — let the thread enter its loop.
    monkeypatch.setattr(ts, "_driver_started", False)
    ts._stop_event.clear()
    ts.start()

    # Wait briefly for thread to exist, then signal stop + join.
    ts.stop()
    for t in threading.enumerate():
        if t.name == ts.DAEMON_THREAD_NAME:
            t.join(timeout=2.0)

    # Confirm no live thread by our name remains.
    assert not ts._thread_alive(), "prior thread should have exited"

    # Clear stop event (start() does this internally; verify the path works).
    # Calling start() again should spawn a fresh thread because no live
    # thread by our name exists.
    third = ts.start()
    assert third is True, "respawn after death must succeed"
    assert ts._thread_alive()

    # Cleanup
    ts.stop()
    for t in threading.enumerate():
        if t.name == ts.DAEMON_THREAD_NAME:
            t.join(timeout=2.0)


def test_pending_rewritten_correctly_with_remaining_rows(isolated_dir, enabled):
    """When one of two rows is processed, the other survives."""
    _seed_pending(isolated_dir, [
        {"package": "alpha", "to_version": "2.0.0",
         "from_version": "1.0.0",
         "requested_at": "2026-05-23T00:00:00+00:00"},
        {"package": "beta", "to_version": "3.0.0",
         "from_version": "2.0.0",
         "requested_at": "2026-05-23T01:00:00+00:00"},
    ])

    runner = _runner_returning(
        lambda pkg, ver: _passing_trial(pkg, ver),
    )
    ts.run_one_tick(runner=runner, repo_root_override=isolated_dir)

    # Read remaining queue
    remaining = ts._read_pending()
    assert len(remaining) == 1
    assert remaining[0]["package"] == "beta"
