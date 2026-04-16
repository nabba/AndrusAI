"""
app.subia.self.competence_map — what the system knows and where it struggles.

Phase 6 of the Self-Improvement Overhaul. Queryable self-knowledge layer:

    get_competence_summary()  →  aggregated view of SkillRecords + gaps
    coverage(topic)           →  "do we already know about this?"
    get_curiosity_signal()    →  scalar in [-0.5, +0.5] feeding homeostasis

Sources consulted:
    - SkillRecord index (what's in the KBs, usage counts)
    - Open LearningGap store (what evidence of gaps we've collected)
    - Novelty Gate (for topic-lookup queries)

The self-model should cite ACTUAL coverage, not skill-file counts. This
module is what the chronicle's "Who I Am" section should read from for
self-descriptions like "I currently have N active skills across 4 KBs,
with M open learning gaps I haven't yet resolved."

IMMUTABLE — infrastructure-level module (read-only for agents).
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# Curiosity signal bounds — clamped so a spike in gaps can't pin homeostasis
# hard against a setpoint. Signal is ADDITIVE to homeostasis.curiosity.
_CURIOSITY_MAX_BOOST = 0.25
_CURIOSITY_MAX_PENALTY = 0.15

# Gap-count thresholds for the curiosity signal mapping.
_GAPS_FOR_FULL_BOOST = 20


def get_competence_summary() -> dict:
    """Aggregated snapshot of self-knowledge.

    Returns {
        total_skills, by_kb = {kb: count}, by_source_gaps = {source: count},
        open_gap_count, resolved_gap_count, curiosity_signal,
        coverage_health: 'healthy'|'thin'|'stretched'
    }
    """
    summary = {
        "total_skills": 0,
        "by_kb": {},
        "open_gap_count": 0,
        "by_source_gaps": {},
        "resolved_gap_count": 0,
        "curiosity_signal": 0.0,
        "coverage_health": "thin",
    }

    try:
        from app.self_improvement.integrator import list_records, KB_CHOICES
        records = list_records(status="active", limit=2000)
        summary["total_skills"] = len(records)
        for kb in KB_CHOICES:
            summary["by_kb"][kb] = sum(1 for r in records if r.kb == kb)
    except Exception:
        logger.debug("competence_summary: integrator read failed", exc_info=True)

    try:
        from app.self_improvement.store import list_open_gaps
        from app.self_improvement.types import GapSource
        open_gaps = list_open_gaps(limit=200)
        summary["open_gap_count"] = len(open_gaps)
        for source in GapSource:
            summary["by_source_gaps"][source.value] = sum(
                1 for g in open_gaps if g.source == source
            )
    except Exception:
        logger.debug("competence_summary: gaps read failed", exc_info=True)

    # Health heuristic:
    #   stretched = more open gaps than active skills (system behind)
    #   thin      = <10 active skills (cold start or consolidated down)
    #   healthy   = otherwise
    total = summary["total_skills"]
    gaps = summary["open_gap_count"]
    if gaps > total and total > 0:
        summary["coverage_health"] = "stretched"
    elif total < 10:
        summary["coverage_health"] = "thin"
    else:
        summary["coverage_health"] = "healthy"

    summary["curiosity_signal"] = get_curiosity_signal(
        open_gap_count=gaps, total_skills=total,
    )
    return summary


def coverage(topic: str) -> dict:
    """Query self-coverage for a topic.

    Returns the Novelty Gate's verdict wrapped in a simpler shape suitable
    for self-description answers ("Do I know about X?"). Example use:
        "Do you know about OAuth?" → coverage("OAuth")
    """
    try:
        from app.self_improvement.novelty import novelty_report
        rep = novelty_report(topic)
        return {
            "topic": topic,
            "covered": rep.is_duplicate,
            "nearest_kb": rep.nearest_kb,
            "distance": rep.nearest_distance,
            "decision": rep.decision.value,
            "has_adjacent_knowledge": rep.decision.value in ("overlap", "adjacent"),
        }
    except Exception:
        return {"topic": topic, "covered": False, "error": "novelty gate unavailable"}


def get_curiosity_signal(
    open_gap_count: Optional[int] = None,
    total_skills: Optional[int] = None,
) -> float:
    """Compute an additive modifier for homeostasis.curiosity.

    Positive = more curiosity (open gaps outstrip what we've resolved).
    Negative = less curiosity (caught up; coast).

    Signal is clamped to [-_CURIOSITY_MAX_PENALTY, +_CURIOSITY_MAX_BOOST].

    Called each homeostasis update; homeostasis adds it to the current
    curiosity value before regulation drift.
    """
    try:
        if open_gap_count is None or total_skills is None:
            # Re-compute if not provided (avoid recursion via summary)
            from app.self_improvement.store import list_open_gaps
            from app.self_improvement.integrator import list_records
            open_gap_count = len(list_open_gaps(limit=200))
            total_skills = len(list_records(status="active", limit=2000))

        # Boost curiosity linearly with gap count up to _GAPS_FOR_FULL_BOOST
        boost = min(1.0, open_gap_count / _GAPS_FOR_FULL_BOOST) * _CURIOSITY_MAX_BOOST

        # Penalty if the system is idle (no gaps AND has a nontrivial KB).
        # Encourages exploration when there's nothing demanding attention.
        if open_gap_count == 0 and total_skills > 20:
            return -_CURIOSITY_MAX_PENALTY * 0.5  # mild

        return round(boost, 4)
    except Exception:
        return 0.0


def describe_self() -> str:
    """Compose a first-person competence description.

    Used by introspective handlers. Reads the actual KB state — no fabrication.
    """
    s = get_competence_summary()
    by_kb = ", ".join(f"{kb}={n}" for kb, n in s["by_kb"].items() if n)
    by_source = ", ".join(
        f"{src}={n}" for src, n in s["by_source_gaps"].items() if n
    )

    lines = [
        f"I currently have **{s['total_skills']}** active skills across the KBs"
        + (f" ({by_kb})" if by_kb else ""),
        f"There are **{s['open_gap_count']}** open learning gaps"
        + (f" (sources: {by_source})" if by_source else ""),
        f"My coverage health is **{s['coverage_health']}**.",
    ]
    if s["curiosity_signal"] > 0.05:
        lines.append(
            f"My curiosity is elevated ({s['curiosity_signal']:+.2f}) — "
            f"there are more gaps than I've processed."
        )
    elif s["curiosity_signal"] < -0.05:
        lines.append(
            f"My curiosity is low ({s['curiosity_signal']:+.2f}) — "
            f"the backlog is caught up."
        )
    return "\n".join(lines)
