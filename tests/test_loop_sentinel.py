"""Tests for the event-loop stall sentinel + its back-pressure wiring
(Phase 2 of the 2026-06-12 gateway serving-plane hardening).

Pins:
  1. A blocked loop is detected, counted, stack-dumped; recovery closes
     the episode with a duration.
  2. Dump rate-limiting (1 per cooldown window).
  3. substrate policy returns ``event_loop_degraded`` for in-stall /
     recent-stall / high-lag snapshots, and stays quiet otherwise.
  4. The audit logger emits through a QueueHandler (no synchronous
     RotatingFileHandler on the emitting path) and records still land.
"""

from __future__ import annotations

import asyncio
import importlib
import time
from pathlib import Path

import pytest


@pytest.fixture()
def sentinel(monkeypatch, tmp_path):
    """Fresh loop_sentinel module with fast thresholds + tmp dump dir."""
    monkeypatch.setenv("LOOP_SENTINEL_INTERVAL_S", "0.05")
    monkeypatch.setenv("LOOP_STALL_THRESHOLD_S", "0.3")
    monkeypatch.setenv("LOOP_STALL_DUMP_COOLDOWN_S", "60")
    import app.loop_sentinel as ls
    ls = importlib.reload(ls)
    ls._DUMP_DIR = tmp_path / "loop_stalls"
    yield ls
    importlib.reload(ls)  # restore ambient config for sibling tests


def _wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_stall_detected_counted_and_dumped(sentinel):
    async def scenario():
        sentinel.start_loop_sentinel()
        await asyncio.sleep(0.3)            # sentinel warms up, beats flow
        time.sleep(0.9)                     # BLOCK the loop (the bug class)
        await asyncio.sleep(0.5)            # recover; beats resume

    asyncio.run(scenario())
    # The monitor thread closes the episode on its next tick after the
    # final beat — poll rather than race it.
    assert _wait_for(lambda: sentinel.get_stats()["stall_count"] >= 1)
    assert _wait_for(lambda: sentinel.get_stats()["in_stall"] is False)

    stats = sentinel.get_stats()
    assert stats["last_stall_duration_s"] is not None
    assert stats["last_stall_age_s"] is not None
    dumps = list((sentinel._DUMP_DIR).glob("*.txt"))
    assert dumps, "stall must produce a stack dump"
    text = dumps[0].read_text(encoding="utf-8")
    assert "event-loop stall" in text
    assert "Thread" in text or "Current thread" in text  # faulthandler output


def test_dump_rate_limited(sentinel):
    async def scenario():
        sentinel.start_loop_sentinel()
        await asyncio.sleep(0.3)
        time.sleep(0.7)                     # stall 1
        await asyncio.sleep(0.6)            # full recovery (monitor ticks)
        time.sleep(0.7)                     # stall 2 — inside dump cooldown
        await asyncio.sleep(0.6)
    asyncio.run(scenario())

    assert _wait_for(lambda: sentinel.get_stats()["stall_count"] >= 2)
    dumps = list((sentinel._DUMP_DIR).glob("*.txt"))
    assert len(dumps) == 1, "second stall within cooldown must not dump"


def test_healthy_loop_no_stalls(sentinel):
    async def scenario():
        sentinel.start_loop_sentinel()
        await asyncio.sleep(0.6)
    asyncio.run(scenario())
    stats = sentinel.get_stats()
    assert stats["stall_count"] == 0
    assert stats["in_stall"] is False


# ── policy wiring ─────────────────────────────────────────────────────────


class _Snap:
    def __init__(self, resources):
        self.resources = resources
        self.inflight_tasks = 0


def _defer(resources):
    from app.substrate.policy import should_defer_heavy_work
    return should_defer_heavy_work(snapshot=_Snap(resources))


def test_policy_defers_during_stall():
    reason = _defer({"loop_in_stall": True})
    assert reason and "event_loop_degraded" in reason


def test_policy_defers_on_recent_stall():
    reason = _defer({"loop_in_stall": False, "loop_last_stall_age_s": 30.0})
    assert reason and "event_loop_degraded" in reason


def test_policy_defers_on_high_lag():
    reason = _defer({"loop_lag_p95_ms": 1500.0})
    assert reason and "event_loop_degraded" in reason


def test_policy_quiet_when_loop_healthy():
    assert _defer({
        "loop_in_stall": False,
        "loop_last_stall_age_s": 7200.0,   # old stall — outside recency window
        "loop_lag_p95_ms": 20.0,
        "disk_free_gb": 100.0,
    }) is None


def test_policy_quiet_when_sentinel_absent():
    assert _defer({"disk_free_gb": 100.0}) is None


# ── audit logger queue-decoupling ─────────────────────────────────────────


def test_audit_logger_is_queue_decoupled(monkeypatch, tmp_path):
    """After _configure_audit_log: the emitting path holds ONLY a
    QueueHandler (lock-free put); the rotating file handler lives behind
    the listener thread — and records still land in the file."""
    pytest.importorskip("fastapi")  # app.main needs the gateway deps
    import logging

    # Pollution guard: a few legacy test modules still overwrite
    # app.config accessors at module level (not via monkeypatch) — e.g.
    # test_conversation_store.py, test_meta_agent.py. Reload so app.main's
    # first import sees the real get_settings in a combined run.
    # (test_metrics.py + test_idle_scheduler_substrate_policy.py were
    # converted to monkeypatch fixtures 2026-06-12; drop this guard once
    # the remaining module-level overrides are converted too.)
    import app.config as _config_mod
    importlib.reload(_config_mod)

    audit_logger = logging.getLogger("crewai.audit")
    for h in list(audit_logger.handlers):
        audit_logger.removeHandler(h)

    log_path = tmp_path / "audit.log"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(log_path))
    import app.main as main_mod
    monkeypatch.setattr(main_mod, "_WORKSPACE_ROOT", str(tmp_path))
    main_mod._configure_audit_log()

    from logging.handlers import QueueHandler, RotatingFileHandler
    kinds = [type(h) for h in audit_logger.handlers]
    assert QueueHandler in kinds, "audit logger must emit via QueueHandler"
    assert RotatingFileHandler not in kinds, (
        "synchronous file handler must NOT sit on the emitting path"
    )

    audit_logger.info('{"event": "queue_decouple_test"}')
    deadline = time.time() + 5
    while time.time() < deadline:
        if log_path.exists() and "queue_decouple_test" in log_path.read_text(encoding="utf-8"):
            break
        time.sleep(0.05)
    assert "queue_decouple_test" in log_path.read_text(encoding="utf-8"), (
        "record must reach the file via the listener thread"
    )
