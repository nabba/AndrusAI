"""feedback_bridge — Signal timestamp ↔ briefing trial section id.

Mirrors :mod:`app.governance_signal_bridge` but for the briefing
evolution flow: when a morning briefing carrying a trial section is
sent, ``register(signal_ts, section_id)`` records the pairing. When
the reaction handler in ``main.py`` sees 👎/👍 on that timestamp it
calls :func:`find_section_for_ts` to resolve back to the trial id,
then dispatches to ``trial_state.mark_dropped`` / ``mark_adopted``.

Why a sidecar JSON map (same rationale as governance_signal_bridge):
 * Briefing trial state lives in its own JSON store; adding a column
   to a Postgres table for a routing aid would be overkill.
 * Loss of the map only means the operator falls back to a future
   trial-section drop via /api/cp/briefing/sections POST, not data
   corruption.

Storage is the shared :class:`app.signal_ts_bridge.SignalTsBridge`
(2026-06-07 consolidation); public API, 8-day TTL, and on-disk schema
(``{section_id, created_at_iso, created_at_epoch}``) unchanged.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from app.signal_ts_bridge import SignalTsBridge

logger = logging.getLogger(__name__)

# 8 days — slightly longer than the auto-adopt 7d window.
_MAX_AGE_SECONDS = 8 * 86400


def _bridge_path() -> Path:
    from app.paths import WORKSPACE_ROOT
    p = WORKSPACE_ROOT / "life_companion" / "briefing_evolution"
    p.mkdir(parents=True, exist_ok=True)
    return p / "feedback_bridge.json"


_BRIDGE = SignalTsBridge(_bridge_path, max_age_seconds=_MAX_AGE_SECONDS)


def register(signal_ts: str, section_id: str) -> None:
    """Record that the briefing message at ``signal_ts`` carried
    ``section_id`` as its trial section. Idempotent — second call with
    the same pair is a no-op."""
    if not signal_ts or not section_id:
        return
    existing = _BRIDGE.get(str(signal_ts))
    if existing and existing.get("section_id") == section_id:
        return
    _BRIDGE.put(str(signal_ts), {
        "section_id": section_id,
        "created_at_iso": datetime.now(timezone.utc).isoformat(),
    })


def find_section_for_ts(signal_ts: str) -> str | None:
    """Resolve a Signal reaction-target timestamp back to its trial
    section id. ``None`` when there's no matching briefing — the
    reaction is for some other surface (governance, action request,
    person suggestion, …)."""
    if not signal_ts:
        return None
    entry = _BRIDGE.get(str(signal_ts))
    return (entry or {}).get("section_id") or None
