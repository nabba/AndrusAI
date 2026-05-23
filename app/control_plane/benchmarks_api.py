"""Benchmark REST surface at /api/cp/benchmarks (Phase C.3, 2026-05-22).

Read-only operator visibility into the leaderboard + write-side trigger
for a manual catalog pass. Five endpoints:

  * ``GET /catalog`` — what tasks are defined
  * ``GET /runs?task_id=X&model=Y&window_days=7`` — filtered raw rows
  * ``GET /leaderboard?window_days=7`` — aggregated dashboard payload
  * ``GET /stats`` — store + scheduler state (rows on disk, last
    pass, master switch)
  * ``POST /refresh`` — operator-initiated catalog pass (force=true)
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from app.control_plane.auth_dep import require_gateway_auth

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/cp/benchmarks",
    tags=["control-plane", "benchmarks"],
    dependencies=[Depends(require_gateway_auth)],
)


def _safe_master_switch() -> bool:
    try:
        from app import runtime_settings
        return runtime_settings.get_benchmarks_enabled()
    except Exception:
        return False


@router.get("/catalog")
def catalog_endpoint() -> dict[str, Any]:
    """List the loaded benchmark tasks + summary stats."""
    from app.benchmarks import catalog_stats, load_tasks
    try:
        tasks = load_tasks()
        return {
            "enabled": _safe_master_switch(),
            "tasks": [
                {
                    "id": t.id,
                    "name": t.name,
                    "description": t.description,
                    "category": t.category,
                    "scorer": t.scorer,
                    "model_targets": list(t.model_targets),
                    "timeout_s": t.timeout_s,
                    "max_tokens": t.max_tokens,
                }
                for t in tasks
            ],
            "stats": catalog_stats(),
        }
    except Exception as exc:
        logger.warning("benchmarks_api.catalog failed: %s", exc)
        return {
            "enabled": _safe_master_switch(),
            "tasks": [],
            "stats": {"task_count": 0, "by_category": {}, "by_scorer": {}},
            "error": f"{type(exc).__name__}: {exc}",
        }


@router.get("/runs")
def runs_endpoint(
    task_id: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    window_days: Optional[int] = Query(None, ge=1, le=365),
    limit: int = Query(100, ge=1, le=1000),
) -> dict[str, Any]:
    """Return matching runs, newest-first, capped at ``limit``."""
    from app.benchmarks import filter_runs, load_all
    try:
        all_runs = load_all()
        filtered = filter_runs(
            all_runs,
            task_id=task_id,
            model=model,
            window_days=window_days,
        )
        # Newest first
        filtered.sort(key=lambda r: r.ts, reverse=True)
        capped = filtered[:limit]
        return {
            "enabled": _safe_master_switch(),
            "n_total": len(filtered),
            "n_returned": len(capped),
            "runs": [
                {
                    "task_id": r.task_id,
                    "model": r.model,
                    "ts": r.ts,
                    "score": r.score,
                    "passed": r.passed,
                    "latency_ms": r.latency_ms,
                    "tokens_in": r.tokens_in,
                    "tokens_out": r.tokens_out,
                    "cost_usd": r.cost_usd,
                    "output_preview": r.output_preview,
                    "error": r.error,
                }
                for r in capped
            ],
        }
    except Exception as exc:
        logger.warning("benchmarks_api.runs failed: %s", exc)
        return {
            "enabled": _safe_master_switch(),
            "n_total": 0, "n_returned": 0,
            "runs": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


@router.get("/leaderboard")
def leaderboard_endpoint(
    window_days: int = Query(7, ge=1, le=365),
) -> dict[str, Any]:
    """The dashboard's main payload — per-model + per-task summaries
    + the (task, model) matrix.
    """
    from app.benchmarks import leaderboard, load_all
    try:
        runs = load_all()
        payload = leaderboard(runs, window_days=window_days)
        payload["enabled"] = _safe_master_switch()
        return payload
    except Exception as exc:
        logger.warning("benchmarks_api.leaderboard failed: %s", exc)
        return {
            "enabled": _safe_master_switch(),
            "window_days": window_days, "n_runs": 0,
            "by_model": {}, "by_task": {}, "matrix": {},
            "error": f"{type(exc).__name__}: {exc}",
        }


@router.get("/stats")
def stats_endpoint() -> dict[str, Any]:
    """Store + scheduler state."""
    from app.benchmarks.store import stats as store_stats
    try:
        s = store_stats()
    except Exception as exc:
        logger.warning("benchmarks_api.stats failed: %s", exc)
        s = {"rows": 0, "bytes": 0, "last_ts": "",
             "error": f"{type(exc).__name__}: {exc}"}
    s["enabled"] = _safe_master_switch()
    return s


@router.post("/refresh")
def refresh_endpoint(force: bool = Query(False)) -> dict[str, Any]:
    """Operator-initiated catalog pass.

    ``force=true`` bypasses both the master-switch and cadence
    guards — useful when the operator wants an immediate refresh
    even though the suite is normally cold.
    """
    from app.benchmarks.scheduler_job import run_refresh
    try:
        return run_refresh(force=force)
    except Exception as exc:
        logger.warning("benchmarks_api.refresh failed: %s", exc)
        return {
            "ran": False, "skipped_reason": "exception",
            "n_runs": 0, "elapsed_s": 0.0, "cost_usd": 0.0,
            "error": f"{type(exc).__name__}: {exc}",
        }
