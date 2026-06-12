"""Event-loop stall sentinel — permanent in-process wedge diagnosis.

The gateway's chronic failure mode (2026-06 incident class) is the asyncio
event loop blocking on synchronous I/O while every thread sleeps — ``/health``
goes dark, the host watchdog eventually restarts the process, and the
diagnosis evaporates with it. ``app/liveness.py`` proves the *process* is
alive; nothing proved the *loop* was, and nothing captured WHERE it was stuck.

This module closes both gaps with two tiny cooperating parts:

  • a **heartbeat task** on the event loop that updates a timestamp every
    second and records scheduling lag into a bounded window, and
  • a **monitor daemon thread** (pattern: ``app/liveness.py``) that watches
    the timestamp. When the loop hasn't beaten for ``LOOP_STALL_THRESHOLD_S``
    it dumps EVERY thread's Python stack via ``faulthandler`` to
    ``workspace/healing/loop_stalls/<ts>.txt`` — capturing the exact frame
    the loop thread is blocked in *while it is blocked* — then counts the
    stall and measures its duration once the loop recovers.

``get_stats()`` feeds the substrate snapshot so the idle scheduler's
back-pressure policy can defer MEDIUM/HEAVY work whenever the serving plane
degrades (congestion control), and the ``loop_stall`` healing monitor alerts
the operator with the dump path.

Additive and failure-isolated: every write is best-effort; a broken sentinel
degrades to exactly the pre-sentinel world.
"""
from __future__ import annotations

import asyncio
import faulthandler
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path

logger = logging.getLogger(__name__)

try:  # canonical workspace root (bind-mounted, host-readable)
    from app.paths import WORKSPACE_ROOT

    _DUMP_DIR = Path(WORKSPACE_ROOT) / "healing" / "loop_stalls"
except Exception:  # pragma: no cover - defensive
    _DUMP_DIR = Path("/app/workspace/healing/loop_stalls")

_BEAT_INTERVAL_S = float(os.environ.get("LOOP_SENTINEL_INTERVAL_S", "1.0"))
_STALL_THRESHOLD_S = float(os.environ.get("LOOP_STALL_THRESHOLD_S", "5.0"))
_DUMP_COOLDOWN_S = float(os.environ.get("LOOP_STALL_DUMP_COOLDOWN_S", "300"))
_LAG_WINDOW = 300  # ~5 min of 1 s beats

_state_lock = threading.Lock()
_last_beat: float | None = None          # time.monotonic() of last loop beat
_lag_window: deque[float] = deque(maxlen=_LAG_WINDOW)  # seconds of beat lag
_stall_count = 0
_in_stall = False
_stall_started_at: float | None = None   # monotonic
_last_stall_ended_at: float | None = None  # monotonic
_last_stall_duration_s: float | None = None
_last_dump_at = 0.0                      # monotonic, rate limiter
_last_dump_path: str | None = None
_started = False
_start_lock = threading.Lock()


async def _beat_loop() -> None:
    """Heartbeat coroutine — runs ON the event loop being watched."""
    global _last_beat
    prev = time.monotonic()
    with _state_lock:
        _last_beat = prev
    while True:
        await asyncio.sleep(_BEAT_INTERVAL_S)
        now = time.monotonic()
        lag = max(0.0, (now - prev) - _BEAT_INTERVAL_S)
        prev = now
        with _state_lock:
            _last_beat = now
            _lag_window.append(lag)


def _dump_stacks(staleness: float) -> str | None:
    """Write all thread stacks to a timestamped file. Returns the path."""
    try:
        _DUMP_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        path = _DUMP_DIR / f"{ts}.txt"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                f"# event-loop stall — no heartbeat for {staleness:.1f}s "
                f"(threshold {_STALL_THRESHOLD_S}s)\n"
                f"# captured {ts} while the loop was still blocked; the\n"
                f"# asyncio loop thread's frame below names the blocking call.\n\n"
            )
            fh.flush()
            faulthandler.dump_traceback(file=fh, all_threads=True)
        return str(path)
    except Exception:
        logger.debug("loop_sentinel: stack dump failed", exc_info=True)
        return None


def _monitor_loop() -> None:
    """Daemon thread — detects stalls, dumps stacks, measures duration.

    Tick scales with the threshold (capped at 1 s) so short thresholds —
    tests, aggressive configs — can't out-race the detector; production
    (5 s threshold) keeps the 1 s tick.
    """
    global _stall_count, _in_stall, _stall_started_at
    global _last_stall_ended_at, _last_stall_duration_s
    global _last_dump_at, _last_dump_path
    tick = min(1.0, max(0.02, _STALL_THRESHOLD_S / 3.0))
    while True:
        time.sleep(tick)
        try:
            with _state_lock:
                beat = _last_beat
            if beat is None:
                continue
            staleness = time.monotonic() - beat
            if staleness > _STALL_THRESHOLD_S:
                if not _in_stall:
                    # Stall onset — capture the evidence while it's live.
                    with _state_lock:
                        _in_stall = True
                        _stall_started_at = beat
                        _stall_count += 1
                    now = time.monotonic()
                    if now - _last_dump_at >= _DUMP_COOLDOWN_S:
                        _last_dump_at = now
                        path = _dump_stacks(staleness)
                        with _state_lock:
                            _last_dump_path = path
                        logger.warning(
                            "loop_sentinel: event loop stalled %.1fs — "
                            "stacks dumped to %s", staleness, path,
                        )
            elif _in_stall:
                # Loop recovered — close the episode.
                with _state_lock:
                    _in_stall = False
                    if _stall_started_at is not None:
                        _last_stall_duration_s = round(
                            time.monotonic() - _stall_started_at, 2
                        )
                    _stall_started_at = None
                    _last_stall_ended_at = time.monotonic()
                logger.warning(
                    "loop_sentinel: event loop recovered after %.1fs stall",
                    _last_stall_duration_s or 0.0,
                )
        except Exception:
            # The sentinel must never die; worst case we miss one tick.
            logger.debug("loop_sentinel: monitor tick failed", exc_info=True)


def start_loop_sentinel() -> None:
    """Start the heartbeat task + monitor thread. Call from a running loop
    (the lifespan). Idempotent; failure-isolated."""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    try:
        asyncio.get_running_loop().create_task(
            _beat_loop(), name="loop-sentinel-beat"
        )
        threading.Thread(
            target=_monitor_loop, name="loop-sentinel-monitor", daemon=True
        ).start()
        logger.info(
            "loop_sentinel: started (threshold=%.1fs, dumps → %s)",
            _STALL_THRESHOLD_S, _DUMP_DIR,
        )
    except Exception:
        logger.warning("loop_sentinel: failed to start", exc_info=True)


def get_stats() -> dict:
    """Snapshot for substrate status / system-status / the healing monitor.

    ``last_stall_age_s`` is seconds since the last stall ENDED (None if the
    loop never stalled); ``in_stall`` flags a stall in progress right now.
    """
    with _state_lock:
        lag_p95_ms = None
        if _lag_window:
            ordered = sorted(_lag_window)
            idx = min(len(ordered) - 1, int(0.95 * len(ordered)))
            lag_p95_ms = round(ordered[idx] * 1000, 1)
        return {
            "stall_count": _stall_count,
            "in_stall": _in_stall,
            "last_stall_age_s": (
                round(time.monotonic() - _last_stall_ended_at, 1)
                if _last_stall_ended_at is not None else None
            ),
            "last_stall_duration_s": _last_stall_duration_s,
            "lag_p95_ms": lag_p95_ms,
            "last_dump_path": _last_dump_path,
        }
