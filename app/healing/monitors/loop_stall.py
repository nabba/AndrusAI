"""loop_stall — operator alerting for event-loop stalls (loop_sentinel).

2026-06-12 gateway serving-plane hardening. ``app/loop_sentinel.py``
detects asyncio event-loop stalls in-process and dumps every thread's
stack to ``workspace/healing/loop_stalls/<ts>.txt`` at the moment of the
wedge. This monitor turns those detections into operator Signal alerts
with the dump path, so a wedge is diagnosed from the artifact instead of
re-derived from symptoms after a watchdog restart.

What this monitor observes
==========================
  * ``loop_sentinel.get_stats()`` — stall count + last dump path.
  * Alerts when the stall count advanced since the last pass; includes
    the latest stall duration and the dump file path.

What it deliberately doesn't do
===============================
  * No restarts, no deferral — the substrate policy already defers
    MEDIUM/HEAVY work on loop degradation, and the host watchdog owns
    restarts. This is a pure visibility surface.

State: ``workspace/healing/loop_stall_monitor_state.json``
(last-seen stall count; alert dedup is implicit — only NEW stalls alert).
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from app.paths import WORKSPACE_ROOT
    _STATE_FILE = Path(WORKSPACE_ROOT) / "healing" / "loop_stall_monitor_state.json"
except Exception:  # pragma: no cover - defensive
    _STATE_FILE = Path("/app/workspace/healing/loop_stall_monitor_state.json")


def _read_state() -> dict:
    import json
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(state: dict) -> None:
    import json
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        logger.debug("loop_stall: state write failed", exc_info=True)


def run() -> None:
    try:
        from app.loop_sentinel import get_stats
        stats = get_stats()
    except Exception:
        logger.debug("loop_stall: sentinel unavailable", exc_info=True)
        return

    count = int(stats.get("stall_count") or 0)
    state = _read_state()
    seen = int(state.get("last_seen_stall_count") or 0)
    if count <= seen:
        return  # nothing new

    new_stalls = count - seen
    state["last_seen_stall_count"] = count
    _write_state(state)

    duration = stats.get("last_stall_duration_s")
    dump = stats.get("last_dump_path") or "(no dump — rate-limited)"
    in_stall = bool(stats.get("in_stall"))
    try:
        from app.life_companion._common import send_signal_alert
        send_signal_alert(
            f"🧵 Event loop stalled {new_stalls}× since last check"
            + (f" (latest {duration:.0f}s)" if duration else "")
            + (" — STILL STALLED." if in_stall else ".")
            + f" Thread stacks: `{dump}` — the asyncio-loop frame names "
            f"the blocking call. MEDIUM/HEAVY idle work auto-defers while "
            f"degraded (substrate policy).",
            tag="loop_stall",
        )
    except Exception:
        logger.debug("loop_stall: alert failed", exc_info=True)
    logger.warning(
        "loop_stall: %d new event-loop stall(s); latest dump %s",
        new_stalls, dump,
    )
