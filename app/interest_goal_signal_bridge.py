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
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Entries older than this are purged on every access. 25h gives a small
# margin over typical day-long reaction windows so we don't drop entries
# the operator is actually intending to react to.
_MAX_AGE_SECONDS = 25 * 3600

_LOCK = threading.Lock()


def _bridge_path() -> Path:
    try:
        from app.paths import WORKSPACE_ROOT  # type: ignore

        return Path(WORKSPACE_ROOT) / "interest_goal_signal_bridge.json"
    except Exception:
        return Path("/app/workspace/interest_goal_signal_bridge.json")


def _load() -> dict:
    p = _bridge_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text() or "{}")
    except Exception:
        logger.debug(
            "interest_goal_signal_bridge: load failed; starting fresh",
            exc_info=True,
        )
        return {}


def _save(data: dict) -> None:
    p = _bridge_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        tmp.replace(p)
    except Exception:
        logger.debug(
            "interest_goal_signal_bridge: save failed", exc_info=True
        )


def _purge_expired(data: dict) -> dict:
    now = datetime.now(timezone.utc).timestamp()
    kept: dict = {}
    for ts_str, entry in data.items():
        try:
            created = float(entry.get("created_at_epoch") or 0)
            if (now - created) <= _MAX_AGE_SECONDS:
                kept[ts_str] = entry
        except Exception:
            continue
    return kept


def register(signal_ts: int | str, run_id: str) -> None:
    """Record a (signal_ts → executor_run_id) mapping. Failure-isolated.

    Called from ``interest_goal_emitter._register_signal_bridge`` right
    after the Signal alert is sent via ``send_message_blocking``.
    """
    if not signal_ts or not run_id:
        return
    try:
        with _LOCK:
            data = _purge_expired(_load())
            key = str(signal_ts)
            data[key] = {
                "run_id": str(run_id),
                "created_at_epoch": datetime.now(timezone.utc).timestamp(),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            _save(data)
    except Exception:
        logger.debug(
            "interest_goal_signal_bridge.register failed", exc_info=True
        )


def find_run_id(signal_ts: int | str) -> str | None:
    """Return the executor run_id for a Signal timestamp, or None.

    Used by the reaction handler in main.py to resolve a 👍/👎 reaction
    on an ``💡 Interest signal`` alert. None means the reaction was on
    a different message — caller falls through to other bridge stacks.
    """
    if not signal_ts:
        return None
    try:
        with _LOCK:
            raw = _load()
            kept = _purge_expired(raw)
            if len(kept) != len(raw):
                _save(kept)
            entry = kept.get(str(signal_ts))
            if entry:
                return str(entry.get("run_id") or "") or None
    except Exception:
        logger.debug(
            "interest_goal_signal_bridge.find_run_id failed", exc_info=True
        )
    return None


def unregister(run_id: str) -> None:
    """Drop any entries pointing at this run_id (post-resolution)."""
    if not run_id:
        return
    try:
        with _LOCK:
            data = _load()
            kept = {
                ts: entry
                for ts, entry in data.items()
                if str(entry.get("run_id") or "") != str(run_id)
            }
            if len(kept) != len(data):
                _save(kept)
    except Exception:
        logger.debug(
            "interest_goal_signal_bridge.unregister failed", exc_info=True
        )
