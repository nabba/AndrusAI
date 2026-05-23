"""Rolling stats for the risk classifier (Verified Plan §3 closure,
2026-05-22).

The plan called for ``app/risk_classifier/evidence.py`` with
"rolling stats: actions_per_zone_per_day, rollback_rate_30d".
This is the data layer the widening proposer consumes when deciding
whether a (requestor, path_prefix) combination has earned widening
of the AUTO_APPLY allowlists.

Source of truth: ``app.change_requests.store.list_all()`` — every
CR carries ``requestor``, ``path``, ``status``, ``decision_at``, and
(for applied CRs) ``rolled_back_at``. The widening proposer already
walks this list; evidence.py centralises the two statistics so
multiple call sites stay consistent.

Public surface
──────────────

  * :func:`actions_per_zone_per_day` — per-zone CR counts in a
    rolling window, normalised to per-day. Useful for "is this
    zone seeing more activity than its safe baseline?"
  * :func:`rollback_rate_30d` — rolled_back / applied across the
    last 30 days, per requestor (and optionally per path-prefix).
  * :func:`evidence_for` — combined snapshot for one (requestor,
    path_prefix) pair, returned in the shape the widening proposer
    consumes.

All three are pure-function over the CR list — no side effects, no
LLM calls, no I/O beyond the standard CR store reads.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────


def _parse_iso(ts: str) -> Optional[datetime]:
    """Parse an ISO8601 timestamp. None on malformed input."""
    if not ts or not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _zone_for_path_safe(path: str) -> str:
    """Lookup zone string for a path. Defaults to "operator_gated"
    on any failure so a corrupted zones module never breaks the
    stats."""
    try:
        from app.risk_classifier.zones import zone_for_path
        return zone_for_path(path).value
    except Exception:
        return "operator_gated"


# ── Output shapes ────────────────────────────────────────────────────


@dataclass(frozen=True)
class ZoneActivity:
    """One row of the actions_per_zone_per_day aggregate."""

    zone: str
    window_days: int
    cr_count: int
    rate_per_day: float


@dataclass(frozen=True)
class RollbackStats:
    """Per-requestor rollback rate. ``rollback_rate`` is
    rolled_back / applied; both raw counts surfaced for transparency."""

    requestor: str
    window_days: int
    applied_count: int
    rolled_back_count: int
    rollback_rate: float


@dataclass(frozen=True)
class CombinedEvidence:
    """The widening proposer's input shape for one (requestor, path_prefix)
    candidate. Mirrors what the widening proposer needs to decide
    whether to widen."""

    requestor: str
    path_prefix: str
    window_days: int
    # Counts across the window
    applied: int = 0
    rejected: int = 0
    rolled_back: int = 0
    # Derived rates
    rollback_rate: float = 0.0
    rejection_rate: float = 0.0
    # Activity surface
    cr_count: int = 0
    rate_per_day: float = 0.0
    # First + last activity timestamps (ISO8601 UTC)
    first_at: Optional[str] = None
    last_at: Optional[str] = None
    # Sample CR ids for operator inspection (cap 5)
    sample_cr_ids: tuple[str, ...] = field(default_factory=tuple)


# ── Aggregations ─────────────────────────────────────────────────────


def _load_crs() -> list:
    """Load all CRs via the canonical store. Returns [] on any
    failure so the stats degrade gracefully when the store isn't
    importable in a stripped test env."""
    try:
        from app.change_requests import store
        return list(store.list_all())
    except Exception:
        logger.debug(
            "risk_classifier.evidence: store unavailable", exc_info=True,
        )
        return []


def actions_per_zone_per_day(
    *, window_days: int = 30, crs: Optional[list] = None,
) -> list[ZoneActivity]:
    """Per-zone CR counts in the trailing ``window_days``, normalised
    to per-day. Sorted by descending count.

    The window's "now" is the actual UTC current time; CRs older than
    ``window_days`` are skipped. Each CR is bucketed by the zone
    derived from its ``path`` field.
    """
    if window_days <= 0:
        return []
    if crs is None:
        crs = _load_crs()
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    counts: dict[str, int] = defaultdict(int)
    for cr in crs:
        ts = _parse_iso(getattr(cr, "created_at", "") or "")
        if ts is None or ts < cutoff:
            continue
        path = getattr(cr, "path", "") or ""
        if not path:
            continue
        counts[_zone_for_path_safe(path)] += 1
    rows = [
        ZoneActivity(
            zone=zone, window_days=window_days,
            cr_count=count,
            rate_per_day=round(count / window_days, 4),
        )
        for zone, count in counts.items()
    ]
    rows.sort(key=lambda r: -r.cr_count)
    return rows


def rollback_rate_30d(
    *,
    window_days: int = 30,
    requestor: Optional[str] = None,
    crs: Optional[list] = None,
) -> list[RollbackStats]:
    """Per-requestor rollback rate over ``window_days``.

    When ``requestor`` is supplied, returns a single-element list for
    that requestor (or empty if no qualifying CRs). When None,
    returns one row per distinct requestor with any qualifying CR.

    ``rollback_rate`` = rolled_back / applied. When applied == 0,
    rollback_rate is 0.0 (no signal — caller decides what to do).
    """
    if window_days <= 0:
        return []
    if crs is None:
        crs = _load_crs()
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    by_req: dict[str, dict[str, int]] = defaultdict(
        lambda: {"applied": 0, "rolled_back": 0},
    )
    for cr in crs:
        req = (getattr(cr, "requestor", "") or "").strip()
        if not req:
            continue
        if requestor and req != requestor:
            continue
        # Use decision_at when available (apply time) else created_at
        ts = _parse_iso(
            (getattr(cr, "decision_at", None) or "")
            or (getattr(cr, "created_at", "") or "")
        )
        if ts is None or ts < cutoff:
            continue
        status = (getattr(cr, "status", None) or "")
        # Some stores use string statuses, some use enums
        status_value = getattr(status, "value", status)
        if status_value == "applied":
            by_req[req]["applied"] += 1
        elif status_value == "rolled_back":
            by_req[req]["rolled_back"] += 1
            # rolled_back implies a prior applied — count both
            by_req[req]["applied"] += 1

    rows: list[RollbackStats] = []
    for req, counts in by_req.items():
        applied = counts["applied"]
        rolled_back = counts["rolled_back"]
        rate = (rolled_back / applied) if applied > 0 else 0.0
        rows.append(RollbackStats(
            requestor=req, window_days=window_days,
            applied_count=applied,
            rolled_back_count=rolled_back,
            rollback_rate=round(rate, 4),
        ))
    rows.sort(key=lambda r: -r.rollback_rate)
    return rows


def evidence_for(
    *,
    requestor: str,
    path_prefix: str,
    window_days: int = 30,
    crs: Optional[list] = None,
) -> CombinedEvidence:
    """Combined evidence snapshot for one (requestor, path_prefix)
    candidate. Returned in the shape the widening proposer consumes.

    Used by the widening proposer when deciding whether to propose
    expanding the AUTO_APPLY allowlists. The same shape can be
    surfaced in the React TrustZonesCard for operator inspection.
    """
    req = (requestor or "").strip()
    prefix = (path_prefix or "").strip()
    if not req or not prefix:
        return CombinedEvidence(
            requestor=req, path_prefix=prefix,
            window_days=window_days,
        )

    if crs is None:
        crs = _load_crs()
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    applied = 0
    rejected = 0
    rolled_back = 0
    cr_count = 0
    first_ts: Optional[datetime] = None
    last_ts: Optional[datetime] = None
    sample_ids: list[str] = []

    for cr in crs:
        cr_req = (getattr(cr, "requestor", "") or "").strip()
        if cr_req != req:
            continue
        cr_path = (getattr(cr, "path", "") or "")
        if not cr_path.startswith(prefix):
            continue
        ts = _parse_iso(getattr(cr, "created_at", "") or "")
        if ts is None or ts < cutoff:
            continue
        cr_count += 1
        if first_ts is None or ts < first_ts:
            first_ts = ts
        if last_ts is None or ts > last_ts:
            last_ts = ts
        if len(sample_ids) < 5:
            cr_id = getattr(cr, "id", None) or getattr(cr, "request_id", None)
            if cr_id:
                sample_ids.append(str(cr_id))
        status = (getattr(cr, "status", None) or "")
        status_value = getattr(status, "value", status)
        if status_value == "applied":
            applied += 1
        elif status_value == "rejected":
            rejected += 1
        elif status_value == "rolled_back":
            rolled_back += 1
            applied += 1  # rolled_back implies prior applied
    total_decided = applied + rejected
    rejection_rate = (rejected / total_decided) if total_decided > 0 else 0.0
    rollback_rate = (rolled_back / applied) if applied > 0 else 0.0
    return CombinedEvidence(
        requestor=req, path_prefix=prefix,
        window_days=window_days,
        applied=applied, rejected=rejected, rolled_back=rolled_back,
        rollback_rate=round(rollback_rate, 4),
        rejection_rate=round(rejection_rate, 4),
        cr_count=cr_count,
        rate_per_day=round(cr_count / window_days, 4),
        first_at=first_ts.isoformat() if first_ts else None,
        last_at=last_ts.isoformat() if last_ts else None,
        sample_cr_ids=tuple(sample_ids),
    )


__all__ = [
    "CombinedEvidence",
    "RollbackStats",
    "ZoneActivity",
    "actions_per_zone_per_day",
    "evidence_for",
    "rollback_rate_30d",
]
