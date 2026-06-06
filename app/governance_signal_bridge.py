"""governance_signal_bridge.py — Signal timestamp ↔ governance request map.

Bridges the existing TIER_IMMUTABLE `control_plane.governance` approval
queue to Signal 👍/👎 reactions. The governance table itself has no
``signal_ts`` column (and is TIER_IMMUTABLE, so adding one requires the
Tier-3 amendment protocol). This module keeps a small JSON sidecar map
so the reaction handler in ``main.py`` can resolve a reacted-to message
timestamp back to its governance request UUID.

Why a JSON sidecar instead of a Postgres column:
 - Postgres column would touch ``governance.py`` (TIER_IMMUTABLE)
 - Migrations under ``migrations/*.sql`` are not auto-applied at boot
   (``startup_migrations.apply_all`` only handles pgvector HNSW indexes)
 - The map is purely a notification-routing aid; loss of the file just
   means the operator falls back to the text command or React dashboard
 - Entries expire fast — governance requests default to 24h TTL

Used by:
 - ``app.auto_deployer.schedule_deploy`` — calls ``register`` after the
   approval-needed message is sent (via ``send_message_blocking`` so
   the Signal timestamp is captured).
 - ``app.main`` reaction handler — calls ``find_request_id`` to resolve
   the reaction target.
 - ``app.agents.commander.commands`` text-command path — calls
   ``find_pending_by_id_prefix`` for ``approve <hex>``.

The ts→id map storage is the shared :class:`app.signal_ts_bridge.SignalTsBridge`
(2026-06-07 consolidation of the four copy-pasted Signal-ts bridges); the
public API + on-disk schema (``{request_id, created_at, created_at_epoch}``)
are unchanged.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.paths import WORKSPACE_ROOT
from app.signal_ts_bridge import SignalTsBridge

logger = logging.getLogger(__name__)

# 25h gives a small margin over the governance default 24h TTL so we don't
# drop entries that are still actionable.
_MAX_AGE_SECONDS = 25 * 3600


def _bridge_path():
    return WORKSPACE_ROOT / "governance_signal_bridge.json"


_BRIDGE = SignalTsBridge(_bridge_path, max_age_seconds=_MAX_AGE_SECONDS)


def register(signal_ts: int, request_id: str) -> None:
    """Record a (signal_ts → governance_request_id) mapping.

    Called from auto_deployer right after the approval-needed Signal
    message is sent. Fire-and-forget — any failure is logged and
    swallowed so the deploy path stays alive.
    """
    if not signal_ts or not request_id:
        return
    try:
        _BRIDGE.put(str(int(signal_ts)), {
            "request_id": str(request_id),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        logger.debug("governance_signal_bridge.register failed", exc_info=True)


def find_request_id(signal_ts: int) -> str | None:
    """Return the governance request_id for a Signal timestamp, or None.

    Called from the reaction handler in main.py. None means the
    reaction wasn't on a tracked governance message — caller falls
    through to other reaction handlers / feedback pipeline.
    """
    if not signal_ts:
        return None
    try:
        entry = _BRIDGE.get(str(int(signal_ts)))
        if entry:
            return str(entry.get("request_id") or "") or None
    except Exception:
        logger.debug("governance_signal_bridge.find_request_id failed", exc_info=True)
    return None


def unregister(request_id: str) -> None:
    """Drop any entries pointing at this request_id (post-resolution cleanup)."""
    if not request_id:
        return
    _BRIDGE.remove_where(lambda v: str(v.get("request_id") or "") == str(request_id))


def find_pending_by_id_prefix(id_prefix: str) -> dict | None:
    """Find a single pending governance request whose UUID starts with id_prefix.

    Used by the ``approve <hex>`` text-command fallback in
    ``agents/commander/commands.py``. Returns the request row dict
    (id, request_type, title, detail_json, status, created_at) when
    exactly one pending request matches the prefix; None on no match
    or ambiguous prefix.
    """
    if not id_prefix:
        return None
    try:
        from app.control_plane.governance import get_governance
        pending = get_governance().get_pending() or []
    except Exception:
        logger.debug("governance_signal_bridge: get_pending failed", exc_info=True)
        return None
    matches = [r for r in pending if str(r.get("id", "")).startswith(id_prefix)]
    if len(matches) == 1:
        return matches[0]
    return None
