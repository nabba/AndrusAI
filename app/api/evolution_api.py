"""Evolution monitoring API routes.

Exposes the results ledger, variant archive, metrics, and engine selection
data for the React dashboard's Evolution Monitor page.

All routes prefixed with /api/cp/evolution/.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cp/evolution", tags=["evolution"])


def _infer_engine(result: dict) -> str:
    """Infer which evolution engine produced a result."""
    detail = (result.get("detail") or "").lower()
    hypothesis = (result.get("hypothesis") or "").lower()
    if "shinkaevolve" in detail or "shinka" in detail or "shinka" in hypothesis:
        return "shinka"
    if "meta-evolution" in detail or "meta_evolution" in hypothesis:
        return "meta"
    return "avo"


# ── Results (experiment history) ────────────────────────────────────────────

@router.get("/results")
def get_evolution_results(
    limit: int = Query(50, ge=1, le=500),
    status: str = Query("", description="Filter by status: keep, discard, crash"),
    engine: str = Query("", description="Filter by engine: avo, shinka, meta"),
):
    """Return recent experiment results with engine inference."""
    from app.results_ledger import get_recent_results

    results = get_recent_results(limit)

    # Enrich with engine field
    for r in results:
        r["engine"] = _infer_engine(r)

    # Apply filters
    if status:
        results = [r for r in results if r["status"] == status]
    if engine:
        results = [r for r in results if r["engine"] == engine]

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

    # Engine breakdown
    engine_counts = {"avo": 0, "shinka": 0, "meta": 0}
    engine_kept = {"avo": 0, "shinka": 0, "meta": 0}
    for r in results:
        eng = _infer_engine(r)
        engine_counts[eng] = engine_counts.get(eng, 0) + 1
        if r["status"] == "keep":
            engine_kept[eng] = engine_kept.get(eng, 0) + 1

    # Recent trend (last 20 kept experiments)
    trend = get_improvement_trend(20)

    # Current engine selection
    current_engine = "unknown"
    try:
        from app.evolution import _select_evolution_engine
        current_engine = _select_evolution_engine()
    except Exception:
        pass

    # SUBIA safety
    subia_safety = 0.8
    try:
        from app.evolution import _get_subia_safety_value
        subia_safety = _get_subia_safety_value()
    except Exception:
        pass

    return {
        "total_experiments": total,
        "kept": kept,
        "discarded": discarded,
        "crashed": crashed,
        "kept_ratio": round(kept / max(1, total), 3),
        "best_score": round(get_best_score(), 4),
        "current_score": round(composite_score(), 4),
        "score_trend": [round(s, 4) for s in trend],
        "current_engine": current_engine,
        "subia_safety": round(subia_safety, 3),
        "engines": {
            name: {
                "total": engine_counts.get(name, 0),
                "kept": engine_kept.get(name, 0),
                "kept_ratio": round(
                    engine_kept.get(name, 0) / max(1, engine_counts.get(name, 0)), 3
                ),
            }
            for name in ["avo", "shinka", "meta"]
        },
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


# ── Variant archive (genealogy) ────────────────────────────────────────────

@router.get("/variants")
def get_variants(n: int = Query(30, ge=1, le=200)):
    """Return recent variants from the genealogy archive."""
    try:
        from app.variant_archive import get_recent_variants, get_drift_score
        variants = get_recent_variants(n)
        drift = get_drift_score()
        return {"variants": variants, "drift_score": drift}
    except Exception as e:
        return {"variants": [], "drift_score": 0, "error": str(e)[:200]}


@router.get("/variants/{variant_id}/lineage")
def get_variant_lineage(variant_id: str):
    """Return the full ancestry chain for a variant."""
    try:
        from app.variant_archive import get_lineage
        lineage = get_lineage(variant_id)
        return {"lineage": lineage}
    except Exception as e:
        return {"lineage": [], "error": str(e)[:200]}


# ── Meta-evolution history ──────────────────────────────────────────────────

@router.get("/meta")
def get_meta_evolution_history():
    """Return meta-evolution cycle history."""
    try:
        from app.meta_evolution import _load_history, measure_evolution_effectiveness
        history = _load_history()
        effectiveness = measure_evolution_effectiveness()
        return {
            "history": history[-20:],
            "effectiveness": effectiveness,
            "total_cycles": len(history),
            "promoted": sum(1 for h in history if h.get("promoted")),
        }
    except Exception as e:
        return {"history": [], "effectiveness": {}, "error": str(e)[:200]}


# ── Engine selection info ───────────────────────────────────────────────────

@router.get("/engine")
def get_engine_info():
    """Return current engine selection details and reasoning."""
    from app.config import get_settings

    config_engine = "auto"
    try:
        config_engine = get_settings().evolution_engine
    except AttributeError:
        pass

    selected = "unknown"
    try:
        from app.evolution import _select_evolution_engine
        selected = _select_evolution_engine()
    except Exception:
        pass

    shinka_available = False
    try:
        from app.evolution import _is_shinka_available
        shinka_available = _is_shinka_available()
    except Exception:
        pass

    return {
        "config_mode": config_engine,
        "selected_engine": selected,
        "shinka_available": shinka_available,
    }


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
