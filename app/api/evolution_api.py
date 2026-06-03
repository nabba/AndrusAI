"""Evolution monitoring API routes.

Exposes the results ledger, self-modification history, and metrics for the
React dashboard's Evolution Monitor page.

All routes prefixed with /api/cp/evolution/.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cp/evolution", tags=["evolution"])


# ── Results (experiment history) ────────────────────────────────────────────

@router.get("/results")
def get_evolution_results(
    limit: int = Query(50, ge=1, le=500),
    status: str = Query("", description="Filter by status: keep, discard, crash"),
):
    """Return recent experiment results."""
    from app.results_ledger import get_recent_results

    results = get_recent_results(limit)

    if status:
        results = [r for r in results if r["status"] == status]

    return {"results": results, "total": len(results)}


# ── Summary stats ───────────────────────────────────────────────────────────

@router.get("/summary")
def get_evolution_summary():
    """High-level evolution statistics for dashboard cards."""
    from app.results_ledger import get_recent_results, get_best_score, get_improvement_trend
    from app.metrics import composite_score

    results = get_recent_results(100)

    total = len(results)
    kept = sum(1 for r in results if r["status"] == "keep")
    discarded = sum(1 for r in results if r["status"] == "discard")
    crashed = sum(1 for r in results if r["status"] == "crash")

    # Recent trend (last 20 kept experiments)
    trend = get_improvement_trend(20)

    return {
        "total_experiments": total,
        "kept": kept,
        "discarded": discarded,
        "crashed": crashed,
        "kept_ratio": round(kept / max(1, total), 3),
        "best_score": round(get_best_score(), 4),
        "current_score": round(composite_score(), 4),
        "score_trend": [round(s, 4) for s in trend],
    }


# ── Composite score breakdown ───────────────────────────────────────────────

@router.get("/metrics")
def get_evolution_metrics():
    """Current composite score with all component breakdowns."""
    from app.metrics import compute_metrics

    metrics = compute_metrics()

    # Add external benchmark if available
    ext_score = None
    try:
        from app.external_benchmarks import get_cached_benchmark_score
        ext_score = get_cached_benchmark_score()
    except Exception:
        pass

    return {
        "composite_score": metrics.get("composite_score", 0),
        "components": {
            "task_success_rate": metrics.get("task_success_rate", 0),
            "error_rate_24h": metrics.get("error_rate_24h", 0),
            "self_heal_rate": metrics.get("self_heal_rate", 0),
            "output_quality": metrics.get("output_quality", 0),
            "evolution_efficiency": metrics.get("evolution_efficiency", 0),
            "avg_response_time_s": metrics.get("avg_response_time_s", 0),
        },
        "external_benchmark": ext_score,
        "measured_at": metrics.get("measured_at", ""),
    }


# ── Self-modification history (verified change-requests) ────────────────────

@router.get("/variants")
def get_variants(n: int = Query(30, ge=1, le=200)):
    """Recent verified self-modifications (applied/rolled-back change-requests),
    sourced from the canonical CR audit (round-5 consolidation 2026-06-03 —
    the population-era variant archive was retired)."""
    try:
        from app.self_improvement.history import recent_modifications, drift_score
        variants = recent_modifications(n, raw=True)  # operator surface: verbatim
        return {"variants": variants, "drift_score": drift_score()}
    except Exception as e:
        return {"variants": [], "drift_score": 0, "error": str(e)[:200]}


@router.get("/variants/{variant_id}/lineage")
def get_variant_lineage(variant_id: str):
    """Genealogy was a population-era concept; the verified engine has no
    parent-chain, so this is always empty. Kept so the dashboard route
    never 404s."""
    return {"lineage": []}


# ── Snapshot archive (historical tags) ──────────────────────────────────────

@router.get("/snapshots")
def get_snapshots(n: int = Query(20, ge=1, le=100)):
    """Return evolution snapshot tags for historical exploration."""
    try:
        from app.workspace_versioning import list_evolution_tags
        tags = list_evolution_tags(n)
        return {"tags": tags}
    except Exception as e:
        return {"tags": [], "error": str(e)[:200]}
