"""
delegation_settings.py — persistent on/off switch for delegation-mode crews.

When ON, a crew dispatches to a Coordinator + specialist sub-agents instead
of a single monolithic agent.  See app/crews/delegated_research.py for the
research-crew example.

Backed by a simple JSON file in the workspace so the setting survives
container restarts without needing env-var redeploys.  The dashboard Org
Chart toggles this via the /api/cp/delegation endpoints.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_SETTINGS_PATH = Path(os.environ.get(
    "DELEGATION_SETTINGS_PATH",
    "/app/workspace/delegation_settings.json",
))
_LOCK = threading.Lock()

# Default state: OFF for every crew.  Safer rollout; user opts in explicitly
# via the dashboard Org Chart toggle.
_DEFAULTS: dict[str, bool] = {
    "research": False,
    "coding": False,
    "writing": False,
}


def _load() -> dict[str, bool]:
    if not _SETTINGS_PATH.exists():
        return dict(_DEFAULTS)
    try:
        raw = json.loads(_SETTINGS_PATH.read_text())
        if not isinstance(raw, dict):
            return dict(_DEFAULTS)
        # Merge with defaults so new crews added later default to OFF
        merged = dict(_DEFAULTS)
        for k, v in raw.items():
            if isinstance(v, bool) and k in _DEFAULTS:
                merged[k] = v
        return merged
    except Exception:
        logger.debug("delegation_settings: load failed", exc_info=True)
        return dict(_DEFAULTS)


def _save(state: dict[str, bool]) -> None:
    _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SETTINGS_PATH.write_text(json.dumps(state, indent=2, sort_keys=True))


def get_all() -> dict[str, bool]:
    """Return {crew_name: bool} for every configurable crew."""
    with _LOCK:
        return _load()


def is_enabled(crew: str) -> bool:
    """Return True if delegation mode is active for this crew."""
    return bool(get_all().get(crew, False))


def set_enabled(crew: str, enabled: bool) -> dict[str, bool]:
    """Enable or disable delegation for a specific crew.  Returns the full
    updated state.  Unknown crews are ignored."""
    if crew not in _DEFAULTS:
        return get_all()
    with _LOCK:
        state = _load()
        state[crew] = bool(enabled)
        _save(state)
    logger.info(f"delegation_settings: {crew} → {'ON' if enabled else 'OFF'}")
    return state
