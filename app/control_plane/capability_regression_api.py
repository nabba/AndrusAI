"""Control plane — capability-regression surface at
/api/cp/capability-regression.

Operator visibility into the snapshot history and detected regressions
shipped in PROGRAM (2026-05-22). Read-only — the daemon writes via the
hourly scheduler; this surface only exposes what's already on disk:

  GET /api/cp/capability-regression/state        current snapshot + last regression
  GET /api/cp/capability-regression/history      recent snapshot rows (newest-first)
  GET /api/cp/capability-regression/regressions  recent regression records

All three endpoints are dependency-gated by ``require_gateway_auth``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.control_plane.auth_dep import require_gateway_auth

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/cp/capability-regression",
    tags=["control-plane", "capability-regression"],
    dependencies=[Depends(require_gateway_auth)],
)


def _safe_enabled() -> bool:
    try:
        from app import runtime_settings
        return runtime_settings.get_capability_regression_enabled()
    except Exception:
        return True  # fail-open mirrors the daemon


def _safe_load_current() -> dict[str, Any] | None:
    try:
        from app.capability_regression import load_snapshot
        s = load_snapshot()
        return s.to_dict() if s is not None else None
    except Exception:
        logger.debug(
            "capability_regression_api: snapshot read failed",
            exc_info=True,
        )
        return None


def _safe_history_path() -> Path | None:
    try:
        from app.capability_regression.snapshot import _snapshot_dir
        return _snapshot_dir() / "history.jsonl"
    except Exception:
        return None


def _safe_regressions_path() -> Path | None:
    try:
        from app.capability_regression.snapshot import _snapshot_dir
        return _snapshot_dir() / "regressions.jsonl"
    except Exception:
        return None


def _read_tail(path: Path | None, limit: int) -> list[dict]:
    """Return the last ``limit`` JSON rows from a JSONL file, newest-first.

    Failure-isolated — a missing or corrupt file returns an empty list
    rather than raising. The naive implementation walks the whole file;
    history is small (one row per hour ≈ 8.7k rows/year) so this is fine.
    """
    if path is None or not path.exists():
        return []
    rows: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        logger.debug(
            "capability_regression_api: read_tail failed for %s",
            path, exc_info=True,
        )
        return []
    # Newest-first: reverse and cap
    rows.reverse()
    return rows[: max(0, int(limit))]


@router.get("/state")
def state_endpoint() -> dict[str, Any]:
    current = _safe_load_current()
    recent_regressions = _read_tail(_safe_regressions_path(), limit=1)
    return {
        "enabled": _safe_enabled(),
        "current_snapshot": current,
        "last_regression": recent_regressions[0] if recent_regressions else None,
    }


@router.get("/history")
def history_endpoint(
    limit: int = Query(default=24, ge=1, le=500),
) -> dict[str, Any]:
    rows = _read_tail(_safe_history_path(), limit=limit)
    return {"count": len(rows), "snapshots": rows}


@router.get("/regressions")
def regressions_endpoint(
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    rows = _read_tail(_safe_regressions_path(), limit=limit)
    return {"count": len(rows), "regressions": rows}


@router.post("/snapshot")
def force_snapshot_endpoint() -> dict[str, Any]:
    """Force a one-shot snapshot pass — equivalent to one iteration of
    the hourly idle scheduler. Useful for verification (e.g. after the
    operator just blocked a model and wants to confirm the snapshot
    picks it up immediately) instead of waiting up to an hour.

    Returns:
      * ``{ran: true, snapshot: ..., regression: ... | null}`` —
        the new snapshot and the regression report (if any). The
        report is None when no regression was detected against the
        prior snapshot.
      * ``{ran: false, reason: "disabled"}`` (with 200, NOT 4xx) —
        the master switch is OFF. Endpoint stays cooperative so
        the React button can show the operator-readable reason
        instead of an opaque HTTP error.
    """
    if not _safe_enabled():
        return {"ran": False, "reason": "disabled"}

    try:
        from app.capability_regression.scheduler_job import run_one_pass
        report = run_one_pass()
    except Exception as exc:
        logger.warning(
            "capability_regression_api: force snapshot failed: %s",
            exc, exc_info=True,
        )
        return {"ran": False, "reason": f"error: {exc}"}

    snapshot = _safe_load_current()
    return {
        "ran": True,
        "snapshot": snapshot,
        "regression": report.to_dict() if report is not None else None,
    }
