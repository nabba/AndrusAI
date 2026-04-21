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

# Priority bands drive resolver authority. Hand-pins (priority ≥ this
# floor) are hard overrides — the resolver returns them directly without
# scoring. Everything below is advisory / historical and gets filtered
# out of the hot path.
HAND_PIN_PRIORITY: int = 1000

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
    """Return the top hand-pin for (role, cost_mode), or None.

    Hand-pins are overlay rows with ``priority ≥ HAND_PIN_PRIORITY``.
    Lower-priority rows (legacy auto_promotion artefacts, future
    suggestion layers) are NOT returned from this function — they
    surface only via ``list_assignments`` for the dashboard.
    """
    try:
        from app.control_plane.db import execute_scalar
        return execute_scalar(
            """
            SELECT model
              FROM control_plane.role_assignments
             WHERE role = %s
               AND cost_mode = %s
               AND active = TRUE
               AND priority >= %s
          ORDER BY priority DESC, created_at DESC
             LIMIT 1
            """,
            (role, cost_mode, HAND_PIN_PRIORITY),
        )
    except Exception as exc:
        logger.debug(f"role_assignments: query failed: {exc}")
        return None


def get_assigned_model(role: str, cost_mode: str) -> str | None:
    """Return the active HAND-PIN for (role, cost_mode), or ``None``.

    Returns only manual (hand-pinned) overrides — the resolver's
    "return directly without scoring" layer. Auto-promotion rows and
    other advisory overlays are excluded; use :func:`list_assignments`
    when you need the full history.

    The returned string is a *catalog key*. Callers should still
    verify ``override in CATALOG`` before using it.
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

    Write-time validation: the ``model`` must be a known key in the
    live ``CATALOG``. Writes pointing at non-catalog keys are rejected
    at the source so the overlay never lies to the dashboard.
    """
    try:
        from app.llm_catalog import CATALOG
        if model not in CATALOG:
            logger.warning(
                "role_assignments: refusing to set (%s, %s) -> %s — "
                "not in live CATALOG (%d entries). Refresh catalog first.",
                role, cost_mode, model, len(CATALOG),
            )
            return False

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


def purge_stale_assignments() -> int:
    """Retire overlay rows whose ``model`` isn't in the live CATALOG.

    Cleans up leftovers from discovery runs that targeted models later
    renamed or removed (e.g. ``auto``, ``deepseek-chat``,
    ``gemma-4-31b-it``). Called by the idle scheduler so the overlay
    self-heals as the catalog evolves.
    """
    try:
        from app.control_plane.db import execute
        from app.llm_catalog import CATALOG
    except Exception:
        return 0

    rows = execute(
        "SELECT role, cost_mode, model FROM control_plane.role_assignments WHERE active = TRUE",
        (),
        fetch=True,
    ) or []
    stale = [r for r in rows if r["model"] not in CATALOG]
    for r in stale:
        try:
            execute(
                """
                UPDATE control_plane.role_assignments
                   SET active = FALSE,
                       retired_at = NOW(),
                       reason = 'purged: target not in catalog'
                 WHERE role = %s AND cost_mode = %s AND model = %s
                """,
                (r["role"], r["cost_mode"], r["model"]),
            )
        except Exception:
            continue
    if stale:
        invalidate_cache()
        logger.info(f"role_assignments: purged {len(stale)} stale overlays")
    return len(stale)


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


# ── Hand-pin convenience layer ──────────────────────────────────────
# Hand-pins are the strongest layer in the resolver's authority cake.
# They write to the same ``role_assignments`` table but always at
# priority ≥ HAND_PIN_PRIORITY and source='manual' so ``get_assigned_model``
# recognises them. ``unpin_role`` retires every active hand-pin for the
# (role, cost_mode) pair — legacy priority-100 auto_promotion rows are
# left alone.

def pin_role(
    role: str,
    cost_mode: str,
    model: str,
    *,
    assigned_by: str = "user",
    reason: str = "",
) -> bool:
    """Hand-assign ``model`` to ``(role, cost_mode)`` — hard override.

    The resolver will return this model directly without scoring while
    the pin is active. ``unpin_role`` removes the pin and the resolver
    takes back over.
    """
    return set_assignment(
        role=role,
        cost_mode=cost_mode,
        model=model,
        source="manual",
        reason=reason,
        assigned_by=assigned_by,
        priority=HAND_PIN_PRIORITY,
    )


def unpin_role(role: str, cost_mode: str) -> int:
    """Retire every active hand-pin for ``(role, cost_mode)``.

    Returns the number of rows retired. Does NOT touch lower-priority
    overlays (auto_promotion artefacts) — use ``retire_assignment``
    for surgical removals.
    """
    try:
        from app.control_plane.db import execute
        rows = execute(
            """
            UPDATE control_plane.role_assignments
               SET active = FALSE,
                   retired_at = NOW(),
                   reason = COALESCE(NULLIF(reason, ''), '') || ' [unpinned]'
             WHERE role = %s
               AND cost_mode = %s
               AND active = TRUE
               AND priority >= %s
         RETURNING role, cost_mode, model
            """,
            (role, cost_mode, HAND_PIN_PRIORITY),
            fetch=True,
        ) or []
        invalidate_cache(role, cost_mode)
        if rows:
            logger.info(
                "role_assignments: unpinned %d hand-pin(s) for (%s, %s)",
                len(rows), role, cost_mode,
            )
        return len(rows)
    except Exception as exc:
        logger.warning(f"role_assignments: unpin failed: {exc}")
        return 0


def list_pins() -> list[dict]:
    """Return all active hand-pins (priority ≥ HAND_PIN_PRIORITY)."""
    try:
        from app.control_plane.db import execute
        return execute(
            """
            SELECT role, cost_mode, model, priority, source, reason,
                   assigned_by, created_at
              FROM control_plane.role_assignments
             WHERE active = TRUE
               AND priority >= %s
          ORDER BY role, cost_mode, priority DESC
            """,
            (HAND_PIN_PRIORITY,),
            fetch=True,
        ) or []
    except Exception:
        return []


def format_pins() -> str:
    """Human-readable pin list for Signal output."""
    rows = list_pins()
    if not rows:
        return "No hand-pinned role assignments. Use `pin <role> <cost_mode> <model>`."
    lines = [f"📌 Hand-pinned assignments ({len(rows)}):"]
    for r in rows:
        who = r.get("assigned_by", "?")
        reason = r.get("reason") or ""
        tail = f" — {reason}" if reason else ""
        lines.append(
            f"  · {r['role']:<14} [{r['cost_mode']:<8}] → {r['model']:<30} by {who}{tail}"
        )
    return "\n".join(lines)
