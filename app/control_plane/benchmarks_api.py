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
import threading
import time as _time
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from app.control_plane.auth_dep import require_gateway_auth

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/cp/benchmarks",
    tags=["control-plane", "benchmarks"],
    dependencies=[Depends(require_gateway_auth)],
)


# Operator-initiated refresh runs in a background daemon thread so
# the HTTP request returns immediately. A non-blocking lock enforces
# one concurrent operator-initiated pass — a second POST while one
# is in flight returns ``started=False, skipped_reason="already_running"``
# without spawning a duplicate. The scheduler's daily idle pass uses
# the same ``scheduler_job.run_refresh`` entry point but goes through
# its internal cadence guard, so it composes safely.
_refresh_in_flight = threading.Lock()
_refresh_state: dict[str, Any] = {
    "started_at": None,
    "last_result": None,
}


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
    """Operator-initiated catalog pass — fire-and-return.

    The full pass over the catalog × tier matrix can take minutes
    end-to-end (15 tasks × 3 model tiers × p95 latency). The
    reverse proxy / Tailscale Funnel times the HTTP request out
    long before that finishes (~60s → 504 Gateway timeout to the
    React client), even though the pass keeps running server-side.
    To avoid the false-failure UX, this endpoint spawns the pass
    on a background daemon thread and returns immediately. The
    leaderboard + stats endpoints reflect rows as they land in
    the JSONL store (no separate progress channel needed — the
    leaderboard's existing 30s react-query poll IS the progress
    indicator).

    Response shape (back-compatible with the legacy synchronous
    form):

      ``started``     — True when a new thread was spawned by
                        this call. NEW field (2026-05-28).
      ``ran``         — Always False from this path (kept for
                        type compatibility with the synchronous
                        result the React side renders).
      ``skipped_reason`` — Set when ``started`` is False:
                        ``already_running`` (a prior refresh is
                        still in flight) or ``thread_spawn_failed``
                        (extremely rare; runtime out of threads).

    Concurrency: a process-wide ``_refresh_in_flight`` lock
    serialises operator-initiated runs. The scheduler's daily
    idle pass uses the same ``run_refresh`` entry point but
    consults its own cadence guard, so the two compose safely.

    ``force=true`` bypasses the master-switch and cadence guards
    inside ``run_refresh`` — useful when the operator wants an
    immediate refresh even though the suite is normally cold.
    """
    if not _refresh_in_flight.acquire(blocking=False):
        return {
            "started": False, "ran": False,
            "skipped_reason": "already_running",
            "n_runs": 0, "elapsed_s": 0.0, "cost_usd": 0.0,
            "error": "",
        }

    from app.benchmarks.scheduler_job import run_refresh

    def _worker() -> None:
        try:
            _refresh_state["last_result"] = run_refresh(force=force)
        except Exception as exc:
            logger.exception("benchmarks_api: background refresh failed")
            _refresh_state["last_result"] = {
                "ran": False, "skipped_reason": "exception",
                "n_runs": 0, "elapsed_s": 0.0, "cost_usd": 0.0,
                "error": f"{type(exc).__name__}: {exc}",
            }
        finally:
            _refresh_in_flight.release()

    try:
        _refresh_state["started_at"] = _time.time()
        threading.Thread(
            target=_worker, daemon=True, name="benchmarks-refresh",
        ).start()
    except Exception as exc:
        # Releasing the lock here is essential — without it any
        # later POST would also be denied.
        _refresh_in_flight.release()
        logger.warning(
            "benchmarks_api: failed to spawn refresh thread: %s", exc,
        )
        return {
            "started": False, "ran": False,
            "skipped_reason": "thread_spawn_failed",
            "n_runs": 0, "elapsed_s": 0.0, "cost_usd": 0.0,
            "error": f"{type(exc).__name__}: {exc}",
        }

    return {
        "started": True, "ran": False, "skipped_reason": "",
        "n_runs": 0, "elapsed_s": 0.0, "cost_usd": 0.0, "error": "",
    }
