"""
app.self_improvement.gap_detector — multi-source learning-gap detection.

Replaces the single biased query (`scope_ecology` in idle_scheduler) with
a structured stream of LearningGap records from multiple signal sources.

Three v1 emitters (highest-value, most tractable):

    1. Retrieval miss      — KB query returned nothing or low-score
    2. Reflexion failure   — crew exhausted retries on a task
    3. MAP-Elites voids    — empty grid cells flanked by strong neighbors

Three more land in Phase 4–5 (low-confidence flagging, user-correction
extraction, usage-decay sweep).

Public surface:

    emit_retrieval_miss(query, top_score, collections)
    emit_reflexion_failure(task, crew_name, retries, reflections)
    emit_mapelites_voids(roles=None, max_per_role=2) -> int
    get_recent_evidence_block(limit=10) -> str

The first three are write-side; the fourth is the read-side consumed by
idle_scheduler when composing the topic-discovery prompt.

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.self_improvement.types import (
    LearningGap, GapSource, GapStatus,
)
from app.self_improvement.store import emit_gap, list_open_gaps

logger = logging.getLogger(__name__)


# ── IMMUTABLE: per-source signal-strength weights ────────────────────────────
#
# Tuned per source: a user correction is worth more than an automated
# retrieval miss. These map raw detection events to LearningGap signal_strength.

SOURCE_WEIGHTS: dict[GapSource, float] = {
    GapSource.RETRIEVAL_MISS:    0.6,
    GapSource.REFLEXION_FAILURE: 0.8,
    GapSource.LOW_CONFIDENCE:    0.4,
    GapSource.USER_CORRECTION:   0.9,
    GapSource.TENSION:           0.5,
    GapSource.MAPELITES_VOID:    0.3,
    GapSource.USAGE_DECAY:       0.2,
}

# Retrieval miss threshold: scores below this count as a miss.
# Calibrated against the orchestrator's default min_score=0.25 — a hit
# clearing 0.25 but below 0.40 indicates the KB has *something* but not
# strongly relevant. We treat <0.40 as a learning gap.
RETRIEVAL_MISS_SCORE_THRESHOLD = 0.40

# Maximum chars to keep from a query/task in the gap description.
_DESC_MAX = 200


# ── Emitters ─────────────────────────────────────────────────────────────────

def emit_retrieval_miss(
    query: str,
    top_score: float,
    collections: list[str],
    task_id: Optional[str] = None,
) -> bool:
    """Emit a gap when a retrieval returns weak or no results.

    Called from the RetrievalOrchestrator's instrumentation point. A miss is:
      - 0 results returned, OR
      - top result score < RETRIEVAL_MISS_SCORE_THRESHOLD

    Best-effort and silent on failure (never breaks retrieval).
    """
    try:
        if top_score >= RETRIEVAL_MISS_SCORE_THRESHOLD:
            return False  # not a miss — we have it covered
        query = (query or "").strip()
        if not query:
            return False

        gap = LearningGap(
            id="",  # store assigns deterministic id
            source=GapSource.RETRIEVAL_MISS,
            description=f"Retrieval miss: {query[:_DESC_MAX]}",
            evidence={
                "query": query[:_DESC_MAX * 2],
                "top_score": round(float(top_score), 4),
                "collections": collections,
                "task_id": task_id or "",
            },
            signal_strength=SOURCE_WEIGHTS[GapSource.RETRIEVAL_MISS],
        )
        return emit_gap(gap)
    except Exception:
        logger.debug("emit_retrieval_miss failed", exc_info=True)
        return False


def emit_reflexion_failure(
    task: str,
    crew_name: str,
    retries: int,
    reflections: Optional[list[str]] = None,
) -> bool:
    """Emit a gap when reflexion exhausts retries.

    Called once from `_run_with_reflexion` when `max_trials` is reached
    without passing the quality gate. The reflections list — if supplied —
    becomes structured evidence for the topic suggester ("here's what the
    crew identified as missing").
    """
    try:
        task = (task or "").strip()
        if not task:
            return False

        # Strength scales with retry count: more retries → higher signal
        base = SOURCE_WEIGHTS[GapSource.REFLEXION_FAILURE]
        strength = min(1.0, base + 0.05 * max(0, retries - 1))

        gap = LearningGap(
            id="",
            source=GapSource.REFLEXION_FAILURE,
            description=f"{crew_name} exhausted retries on: {task[:_DESC_MAX]}",
            evidence={
                "task": task[:_DESC_MAX * 2],
                "crew": crew_name,
                "retries": int(retries),
                "reflections": [r[:300] for r in (reflections or [])][:3],
            },
            signal_strength=strength,
        )
        return emit_gap(gap)
    except Exception:
        logger.debug("emit_reflexion_failure failed", exc_info=True)
        return False


def emit_mapelites_voids(
    roles: Optional[list[str]] = None,
    max_per_role: int = 2,
    min_neighbor_fitness: float = 0.55,
) -> int:
    """Periodic scan: emit one gap per high-priority MAP-Elites void per role.

    Called from idle_scheduler. A void flanked by high-fitness neighbors
    means: "the system performs well *around* this configuration but has
    never tried *exactly* this region of strategy space."

    Returns: number of gaps emitted this scan.
    """
    try:
        from app.map_elites import get_db, FEATURE_DIMENSIONS
    except Exception:
        return 0

    roles = roles or ["researcher", "coder", "writer", "commander"]
    emitted = 0

    for role in roles:
        try:
            db = get_db(role)
            cov = db.get_coverage_report()
            if cov["total_filled"] < 5:
                continue  # too sparse to identify meaningful voids

            voids = db.get_voids(
                min_neighbor_fitness=min_neighbor_fitness,
                min_neighbors_filled=2,
                top_n=max_per_role,
            )
            for v in voids:
                feat_str = ", ".join(
                    f"{d}={v['feature_target'][d]:.2f}" for d in FEATURE_DIMENSIONS
                )
                gap = LearningGap(
                    id="",
                    source=GapSource.MAPELITES_VOID,
                    description=(
                        f"{role} strategy void at ({feat_str}) — "
                        f"flanked by {v['neighbor_count']} high-fitness "
                        f"neighbors (mean {v['mean_neighbor_fitness']:.2f})"
                    ),
                    evidence={
                        "role": role,
                        "feature_target": v["feature_target"],
                        "neighbor_count": v["neighbor_count"],
                        "mean_neighbor_fitness": v["mean_neighbor_fitness"],
                    },
                    signal_strength=SOURCE_WEIGHTS[GapSource.MAPELITES_VOID],
                )
                if emit_gap(gap):
                    emitted += 1
        except Exception:
            logger.debug(f"emit_mapelites_voids: {role} failed", exc_info=True)

    if emitted:
        logger.info(f"gap_detector: emitted {emitted} MAP-Elites void gaps")
    return emitted


# ── Read-side: compose evidence block for topic discovery ────────────────────

def get_recent_evidence_block(limit: int = 10) -> str:
    """Compose a context block from open gaps for the topic-suggester prompt.

    Replaces the `scope_ecology` query in `_auto_discover_topics`. Pulls the
    most-recent, highest-strength open gaps across all sources, formats them
    grouped by source so the LLM sees the diversity of evidence.

    Returns "" if no gaps are open (the system is currently caught up —
    don't fabricate work).
    """
    gaps = list_open_gaps(limit=limit)
    if not gaps:
        return ""

    # Group by source for readable structure
    by_source: dict[GapSource, list[LearningGap]] = {}
    for g in gaps:
        by_source.setdefault(g.source, []).append(g)

    lines = ["Open learning gaps (signals across multiple sources):"]
    # Stable presentation order: highest-weight sources first
    source_order = sorted(by_source.keys(),
                          key=lambda s: SOURCE_WEIGHTS.get(s, 0.0),
                          reverse=True)
    for source in source_order:
        lines.append(f"\n[{source.value}]")
        for g in by_source[source][:5]:  # cap per source
            lines.append(f"  • (strength={g.signal_strength:.2f}) {g.description[:160]}")

    return "\n".join(lines)
