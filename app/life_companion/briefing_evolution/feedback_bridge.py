"""feedback_bridge — Signal timestamp ↔ briefing trial section id.

Mirrors :mod:`app.governance_signal_bridge` but for the briefing
evolution flow: when a morning briefing carrying a trial section is
sent, ``register(signal_ts, section_id)`` records the pairing. When
the reaction handler in ``main.py`` sees 👎/👍 on that timestamp it
calls :func:`find_section_for_ts` to resolve back to the trial id,
then dispatches to ``trial_state.mark_dropped`` / ``mark_adopted``.

Why a sidecar JSON map (same rationale as governance_signal_bridge):
 * Briefing trial state lives in its own JSON store; adding a column
   to a Postgres table for a 25h-TTL routing aid would be overkill.
 * Loss of the map only means the operator falls back to a future
   trial-section drop via /api/cp/briefing/sections POST, not data
   corruption.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# 25h matches the briefing cadence (one per morning) + a margin for
# the auto-adopt 7d window — but entries this old should already have
# been promoted to adopted, so 25h is the right scope for the reaction
# routing map specifically.
_MAX_AGE_SECONDS = 8 * 86400  # 8 days — slightly longer than auto-adopt 7d window

_LOCK = threading.Lock()


def _bridge_path() -> Path:
    from app.paths import WORKSPACE_ROOT
    p = WORKSPACE_ROOT / "life_companion" / "briefing_evolution"
    p.mkdir(parents=True, exist_ok=True)
    return p / "feedback_bridge.json"


def _load() -> dict:
    p = _bridge_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text() or "{}")
    except Exception:
        logger.warning("feedback_bridge: load failed", exc_info=True)
        return {}


def _save(data: dict) -> None:
    p = _bridge_path()
    try:
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        tmp.replace(p)
    except Exception:
        logger.warning("feedback_bridge: save failed", exc_info=True)


def _purge_expired(data: dict) -> dict:
    now = datetime.now(timezone.utc).timestamp()
    kept = {}
    for ts_key, entry in data.items():
        try:
            created = float((entry or {}).get("created_at_epoch") or 0)
            if (now - created) <= _MAX_AGE_SECONDS:
                kept[ts_key] = entry
        except Exception:
            continue
    return kept


def register(signal_ts: str, section_id: str) -> None:
    """Record that the briefing message at ``signal_ts`` carried
    ``section_id`` as its trial section. Idempotent — second call with
    the same pair is a no-op."""
    if not signal_ts or not section_id:
        return
    with _LOCK:
        data = _purge_expired(_load())
        ts_key = str(signal_ts)
        if data.get(ts_key, {}).get("section_id") == section_id:
            return
        data[ts_key] = {
            "section_id": section_id,
            "created_at_epoch": datetime.now(timezone.utc).timestamp(),
            "created_at_iso": datetime.now(timezone.utc).isoformat(),
        }
        _save(data)


def find_section_for_ts(signal_ts: str) -> str | None:
    """Resolve a Signal reaction-target timestamp back to its trial
    section id. ``None`` when there's no matching briefing — the
    reaction is for some other surface (governance, action request,
    person suggestion, …)."""
    if not signal_ts:
        return None
    with _LOCK:
        data = _purge_expired(_load())
        # Save the purged form so old entries don't accumulate.
        _save(data)
        entry = data.get(str(signal_ts))
    return (entry or {}).get("section_id") or None
