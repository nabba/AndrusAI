"""
llm_role_assignments.py — Runtime overlay for ROLE_DEFAULTS.

Discovery can promote a new model (free tier auto, others via governance
approval). The original pipeline added the model to ``CATALOG`` but left
``ROLE_DEFAULTS`` untouched, so ``select_model`` would never actually
pick the promoted model — that's the "selection ghost" fixed here.

This module stores role→model assignments in
``control_plane.role_assignments`` (see ``migrations/016``) and is
consulted by ``app.llm_catalog.get_default_for_role`` before falling
back to the static defaults.

Design notes:
- DB-backed, not in-memory. Assignments persist across restarts.
- ``get_assigned_model`` returns the highest-priority active row for
  the (role, cost_mode) pair, with ties broken by ``created_at DESC``.
- Gracefully degrades to ``None`` when PostgreSQL is unreachable —
  selection falls through to the static catalog.
- A bounded in-process cache (5s TTL) absorbs hot-path lookups without
  introducing staleness under human-scale promotion cadence.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Iterable

logger = logging.getLogger(__name__)

# ── Cache ────────────────────────────────────────────────────────────────
# Role assignment queries sit on the selector's hot path. Assignments change
# on the order of minutes (governance approvals, promotions); a 5-second
# cache removes tens of thousands of SQL round-trips per minute without any
# user-visible staleness.

_CACHE_TTL_S = 5.0
_cache: dict[tuple[str, str], tuple[float, str | None]] = {}
_cache_lock = threading.Lock()


def _cached_get(role: str, cost_mode: str) -> str | None:
    key = (role, cost_mode)
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (now - entry[0]) < _CACHE_TTL_S:
            return entry[1]
    value = _query_assigned_model(role, cost_mode)
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)
    return value


def invalidate_cache(role: str | None = None, cost_mode: str | None = None) -> None:
    """Drop cached entries. Callers mutating assignments should invoke this
    so the next selection observes the change immediately.
    """
    with _cache_lock:
        if role is None and cost_mode is None:
            _cache.clear()
            return
        for key in list(_cache):
            if (role is None or key[0] == role) and (cost_mode is None or key[1] == cost_mode):
                _cache.pop(key, None)


# ── Queries ──────────────────────────────────────────────────────────────

def _query_assigned_model(role: str, cost_mode: str) -> str | None:
    try:
        from app.control_plane.db import execute_scalar
        return execute_scalar(
            """
            SELECT model
              FROM control_plane.role_assignments
             WHERE role = %s
               AND cost_mode = %s
               AND active = TRUE
          ORDER BY priority DESC, created_at DESC
             LIMIT 1
            """,
            (role, cost_mode),
        )
    except Exception as exc:
        logger.debug(f"role_assignments: query failed: {exc}")
        return None


def get_assigned_model(role: str, cost_mode: str) -> str | None:
    """Return the currently active model for the (role, cost_mode) pair,
    or ``None`` if no active overlay exists.

    The returned string is a *catalog key* (e.g. ``"deepseek-v3.2"``),
    not a provider-prefixed model_id. Callers must verify that the key
    is present in ``CATALOG`` before using it.
    """
    if not role or not cost_mode:
        return None
    return _cached_get(role, cost_mode)


def set_assignment(
    role: str,
    cost_mode: str,
    model: str,
    *,
    source: str = "manual",
    reason: str = "",
    assigned_by: str = "system",
    priority: int = 100,
) -> bool:
    """Upsert an active assignment. Returns True on success.

    Re-activates a previously retired (role, cost_mode, model) row
    rather than creating a duplicate, preserving the primary-key
    invariant. Existing assignments for the same (role, cost_mode)
    stay in the table — priority + created_at order resolves conflicts
    on read.
    """
    try:
        from app.control_plane.db import execute
        execute(
            """
            INSERT INTO control_plane.role_assignments
                   (role, cost_mode, model, priority, source, reason, assigned_by, active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
            ON CONFLICT (role, cost_mode, model) DO UPDATE SET
                priority   = EXCLUDED.priority,
                source     = EXCLUDED.source,
                reason     = EXCLUDED.reason,
                assigned_by = EXCLUDED.assigned_by,
                active     = TRUE,
                retired_at = NULL
            """,
            (role, cost_mode, model, priority, source, reason, assigned_by),
        )
        invalidate_cache(role, cost_mode)
        logger.info(
            "role_assignments: (%s, %s) -> %s source=%s priority=%d",
            role, cost_mode, model, source, priority,
        )
        return True
    except Exception as exc:
        logger.warning(f"role_assignments: upsert failed: {exc}")
        return False


def retire_assignment(role: str, cost_mode: str, model: str, reason: str = "") -> bool:
    """Mark a single assignment inactive. Returns True on success."""
    try:
        from app.control_plane.db import execute
        execute(
            """
            UPDATE control_plane.role_assignments
               SET active = FALSE,
                   retired_at = NOW(),
                   reason = COALESCE(NULLIF(%s, ''), reason)
             WHERE role = %s
               AND cost_mode = %s
               AND model = %s
            """,
            (reason, role, cost_mode, model),
        )
        invalidate_cache(role, cost_mode)
        return True
    except Exception as exc:
        logger.warning(f"role_assignments: retire failed: {exc}")
        return False


def list_assignments(active_only: bool = True) -> list[dict]:
    """Return all assignments (for dashboards and tests)."""
    try:
        from app.control_plane.db import execute
        q = """
            SELECT role, cost_mode, model, priority, source, reason,
                   assigned_by, active, created_at, retired_at
              FROM control_plane.role_assignments
        """
        if active_only:
            q += " WHERE active = TRUE"
        q += " ORDER BY role, cost_mode, priority DESC, created_at DESC"
        return execute(q, (), fetch=True) or []
    except Exception:
        return []


def bulk_set(
    cost_modes: Iterable[str],
    role: str,
    model: str,
    *,
    source: str = "manual",
    reason: str = "",
    assigned_by: str = "system",
    priority: int = 100,
) -> int:
    """Upsert the same assignment across multiple cost modes.

    Used when a discovered model dominates the incumbent across the
    whole cost spectrum (e.g. a cheaper-and-stronger drop-in).
    Returns the number of successful writes.
    """
    written = 0
    for mode in cost_modes:
        if set_assignment(
            role, mode, model,
            source=source, reason=reason,
            assigned_by=assigned_by, priority=priority,
        ):
            written += 1
    return written
