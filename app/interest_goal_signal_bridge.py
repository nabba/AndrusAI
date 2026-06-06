"""interest_goal_signal_bridge.py — Signal timestamp ↔ executor run id.

Gap 2 of the 2026-05-24 ultrathink analysis closure.

Bridges Signal 👍/👎 reactions on ``💡 Interest signal`` alerts emitted by
``app.companion.interest_goal_emitter`` to the executor run id those
alerts represent. Same pattern as ``governance_signal_bridge`` and the
change-request reaction routing — JSON sidecar at
``workspace/interest_goal_signal_bridge.json`` with 25h auto-purge so
the file never grows unbounded.

The reaction handler in ``app/main.py`` walks the bridge stack on every
incoming reaction; ``find_run_id`` returns the run id when the reaction
target matches a tracked interest-goal alert. When the reaction is 👎,
the handler calls ``app.companion.interest_goal_emitter.decline`` which
aborts the run + records a 30-day cooldown.

Storage is the shared :class:`app.signal_ts_bridge.SignalTsBridge`
(2026-06-07 consolidation); public API + on-disk schema unchanged.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from app.signal_ts_bridge import SignalTsBridge

logger = logging.getLogger(__name__)

# 25h gives a small margin over typical day-long reaction windows so we don't
# drop entries the operator is actually intending to react to.
_MAX_AGE_SECONDS = 25 * 3600


def _bridge_path() -> Path:
    try:
        from app.paths import WORKSPACE_ROOT  # type: ignore

        return Path(WORKSPACE_ROOT) / "interest_goal_signal_bridge.json"
    except Exception:
        return Path("/app/workspace/interest_goal_signal_bridge.json")


_BRIDGE = SignalTsBridge(_bridge_path, max_age_seconds=_MAX_AGE_SECONDS)


def register(signal_ts: int | str, run_id: str) -> None:
    """Record a (signal_ts → executor_run_id) mapping. Failure-isolated.

    Called from ``interest_goal_emitter._register_signal_bridge`` right
    after the Signal alert is sent via ``send_message_blocking``.
    """
    if not signal_ts or not run_id:
        return
    try:
        _BRIDGE.put(str(signal_ts), {
            "run_id": str(run_id),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        logger.debug("interest_goal_signal_bridge.register failed", exc_info=True)


def find_run_id(signal_ts: int | str) -> str | None:
    """Return the executor run_id for a Signal timestamp, or None.

    Used by the reaction handler in main.py to resolve a 👍/👎 reaction
    on an ``💡 Interest signal`` alert. None means the reaction was on
    a different message — caller falls through to other bridge stacks.
    """
    if not signal_ts:
        return None
    try:
        entry = _BRIDGE.get(str(signal_ts))
        if entry:
            return str(entry.get("run_id") or "") or None
    except Exception:
        logger.debug("interest_goal_signal_bridge.find_run_id failed", exc_info=True)
    return None


def unregister(run_id: str) -> None:
    """Drop any entries pointing at this run_id (post-resolution)."""
    if not run_id:
        return
    _BRIDGE.remove_where(lambda v: str(v.get("run_id") or "") == str(run_id))
