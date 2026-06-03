"""Self-modification history — the read surface that replaces the retired
population-era genealogy stores (``variant_archive`` / ``evolution_db`` archive
/ ``evolution_roi``).

The verified mutation engine's **applied** (and **rolled-back**) change-requests
ARE its self-modification record. This module surfaces them — sourced from the
single canonical hash-chained CR audit (``app.change_requests.store``) — in the
variant-shaped dicts the legacy display readers expect, so the display surfaces
keep working against one record instead of a parallel store.

Honesty by construction: there is **no fabricated fitness delta**. The legacy
archive froze an ``eval_set_score`` delta that the old loop never actually
measured (CLAUDE.md §73 — "pure noise"). Here the signal is the real,
operator-gated, execution-verified outcome: applied vs rolled-back.

The verified engine files its CRs under requestor ``self_improver``
(``orchestrator.run_verified_cycle`` default); this module scopes to exactly
those so it reports self-*modifications*, not every change-request in the system.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# The verified engine's CR requestor (orchestrator.run_verified_cycle default).
_SELF_REQUESTOR = "self_improver"
# Map CR status → the legacy variant-status vocabulary the readers filter on
# (``keep`` / ``discard``) so Counters and ``status == "keep"`` checks survive.
_STATUS_MAP = {"applied": "keep", "rolled_back": "discard", "rejected": "discard"}


def _status_value(cr) -> str:
    return getattr(getattr(cr, "status", None), "value", "") or ""


def _self_mod_crs(limit: int = 200) -> list:
    """Applied + rolled-back self-improvement change-requests, newest first.

    Failure-isolated: returns ``[]`` if the change-request layer is unavailable
    (e.g. host without the pydantic env), so every reader degrades gracefully.
    """
    try:
        from app.change_requests import store as cr_store
        from app.change_requests.models import Status
    except Exception as exc:  # pragma: no cover - host-without-deps path
        logger.debug("history: change_requests unavailable: %s", exc)
        return []

    rows: list = []
    for st in (Status.APPLIED, Status.ROLLED_BACK):
        try:
            rows.extend(cr_store.list_all(status=st, limit=limit))
        except Exception:
            continue
    rows = [c for c in rows if getattr(c, "requestor", "") == _SELF_REQUESTOR]
    rows.sort(key=lambda c: getattr(c, "created_at", "") or "", reverse=True)
    return rows[:limit]


def _to_variant(cr) -> dict:
    """Map a ChangeRequest into the legacy variant dict shape."""
    raw_status = _status_value(cr)
    path = getattr(cr, "path", "") or ""
    return {
        "id": getattr(cr, "id", "") or "",
        "parent_id": "root",
        "hypothesis": (getattr(cr, "reason", "") or "")[:500],
        "change_type": "code",
        "fitness_before": 0.0,
        "fitness_after": 0.0,
        "delta": 0.0,  # no fabricated delta — applied/rolled-back is the signal
        "test_pass_rate": 0.0,
        "status": _STATUS_MAP.get(raw_status, raw_status or "pending"),
        "files_changed": [path] if path else [],
        "mutation_summary": "",
        "timestamp": getattr(cr, "created_at", "") or "",
        "generation": 0,
    }


def recent_modifications(n: int = 10, *, raw: bool = False) -> list[dict]:
    """Recent verified self-modifications (applied/rolled-back CRs), newest first.

    ``raw`` is accepted for call-site parity with the retired
    ``variant_archive.get_recent_variants`` — there is no frozen prose to
    neutralise here (reasons are operator-facing), so it has no effect.
    """
    return [_to_variant(c) for c in _self_mod_crs(n)]


def drift_score() -> int:
    """How far the system has modified itself: count of APPLIED self-mod CRs."""
    return sum(1 for c in _self_mod_crs(500) if _status_value(c) == "applied")


def modification_stats() -> dict:
    """Dashboard summary (shape-compatible with the retired ``get_evolution_stats``)."""
    crs = _self_mod_crs(500)
    applied = [c for c in crs if _status_value(c) == "applied"]
    rolled = [c for c in crs if _status_value(c) == "rolled_back"]
    return {
        "total_variants": len(crs),
        "passed_variants": len(applied),
        "rolled_back": len(rolled),
        "best_score": 0.0,
        "active_runs": 0,
        "recent": [_to_variant(c) for c in crs[:5]],
    }


def cr_rollback_stats(window_days: int = 30) -> dict:
    """Applied vs rolled-back self-mod CRs within the window.

    Replaces the ``evolution_roi`` rolling-ROI read for
    ``goodhart_guard``'s ``rollback_silence`` signal — same intent ("are we
    catching regressions in what we ship?"), one canonical record.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

    def _in_window(cr) -> bool:
        ts = getattr(cr, "created_at", "") or ""
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt >= cutoff
        except Exception:
            return True  # undated → count it (conservative)

    crs = [c for c in _self_mod_crs(1000) if _in_window(c)]
    applied = sum(1 for c in crs if _status_value(c) == "applied")
    rolled = sum(1 for c in crs if _status_value(c) == "rolled_back")
    total = applied + rolled
    return {
        "applied": applied,
        "rolled_back": rolled,
        "rollback_rate": (rolled / total) if total else 0.0,
    }


def format_modifications(n: int = 8) -> str:
    """Human-readable recent self-modifications.

    Replaces ``variant_archive.format_archive_context`` for the verified-engine
    planner context and the Signal ``variants`` command.
    """
    mods = recent_modifications(n)
    if not mods:
        return "No verified self-modifications recorded yet."
    lines = ["## Recent verified self-modifications (operator-gated, execution-verified)\n"]
    for m in mods:
        files = ", ".join(m["files_changed"])[:48]
        lines.append(f"  [{m['status']:7s}] {m['hypothesis'][:70]} ({files})")
    lines.append(f"\nApplied self-modifications to date: {drift_score()}")
    return "\n".join(lines)
