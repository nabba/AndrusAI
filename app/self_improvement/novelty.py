"""
app.self_improvement.novelty — Novelty Gate.

Replaces the bag-of-words `_is_duplicate` check in idle_scheduler with
embedding-based novelty detection across the full KB graph.

Single entry point: novelty_report(text, kbs=None) -> NoveltyReport.

The Gate queries the unified RetrievalOrchestrator across all knowledge
sources (the four KBs + team_shared skills collection) and returns a
verdict + nearest neighbor in one structure.

Decision is made on cosine distance to the nearest existing entry.
Thresholds are calibrated, configurable, and live at module scope so they
can be tuned without touching consumers.

Three layers of defense (per overhaul plan):
    1. Topic-level (cheap):  novelty_report(topic_string)
    2. Content-level (decisive): novelty_report(generated_skill_content)
    3. Continuous (Phase 5):  Consolidator clusters and merges drift

Layers 1 + 2 are implemented here; Layer 3 follows in a later phase.

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.self_improvement.types import NoveltyReport, NoveltyDecision

logger = logging.getLogger(__name__)


# ── IMMUTABLE: novelty thresholds (cosine distance) ──────────────────────────
#
# Distance ranges, lower = more similar. Calibrated against the existing 223
# skills (where the median pairwise cosine distance among "rapid_ecological_*"
# files is ~0.20, demonstrating the bag-of-words check could not separate
# them).
#
# To tune: edit here, not in consumers. Consumers must use the named decisions
# from NoveltyDecision.

NOVELTY_THRESHOLDS: dict[NoveltyDecision, tuple[float, float]] = {
    NoveltyDecision.COVERED:  (0.0,  0.30),
    NoveltyDecision.OVERLAP:  (0.30, 0.55),
    NoveltyDecision.ADJACENT: (0.55, 0.80),
    NoveltyDecision.NOVEL:    (0.80, 2.0),   # cosine distance can exceed 1.0
}

# Default set of collections queried — all knowledge sources the system has.
# An entry near any of these implies coverage; we query all in parallel.
DEFAULT_KB_COLLECTIONS: list[str] = [
    "episteme_research",      # P5 — research/theory KB
    "experiential_journal",   # P5 — distilled lived experience
    "aesthetic_patterns",     # P5 — style/judgement KB
    "unresolved_tensions",    # P5 — open contradictions
    "team_shared",            # legacy skill files (still authoritative until Phase 3 migration)
]

# Number of nearest neighbors to fetch from the orchestrator. The decision
# uses only the top-1, but additional neighbors are useful for the rationale
# string ("near A, B, C — consider extending A").
_TOP_K = 3

# Minimum semantic score to consider a hit at all. Below this, the orchestrator
# returns nothing (treated as fully novel).
_MIN_SCORE = 0.10


def _classify(distance: float) -> NoveltyDecision:
    """Map a cosine distance to a NoveltyDecision."""
    for decision, (lo, hi) in NOVELTY_THRESHOLDS.items():
        if lo <= distance < hi:
            return decision
    return NoveltyDecision.NOVEL  # safety net for distance >= upper bound


def novelty_report(text: str, kbs: Optional[list[str]] = None) -> NoveltyReport:
    """Embed `text` and find the nearest existing knowledge across KBs.

    Args:
        text: candidate topic phrase or full skill content
        kbs:  collection names to query; defaults to DEFAULT_KB_COLLECTIONS

    Returns:
        NoveltyReport with decision, nearest distance, nearest text/kb/id,
        and a human-readable rationale. Always returns — orchestrator failures
        degrade to NOVEL (better to over-create than to silently dedup).
    """
    text = (text or "").strip()
    if not text:
        return NoveltyReport(
            decision=NoveltyDecision.COVERED,
            nearest_distance=0.0,
            rationale="empty input — treated as already-covered",
        )

    collections = kbs or DEFAULT_KB_COLLECTIONS

    try:
        from app.retrieval.orchestrator import RetrievalOrchestrator
        orch = RetrievalOrchestrator()
        results = orch.retrieve(
            query=text,
            collections=collections,
            top_k=_TOP_K,
            min_score=_MIN_SCORE,
        )
    except Exception as exc:
        logger.debug(f"novelty_report: orchestrator failed: {exc}")
        return NoveltyReport(
            decision=NoveltyDecision.NOVEL,
            nearest_distance=1.0,
            rationale=f"retrieval orchestrator unavailable: {exc.__class__.__name__}",
        )

    if not results:
        return NoveltyReport(
            decision=NoveltyDecision.NOVEL,
            nearest_distance=1.0,
            rationale=f"no hits across {len(collections)} KBs above min_score",
        )

    # Top-1 drives the decision
    top = max(results, key=lambda r: r.score)
    distance = max(0.0, 1.0 - float(top.score))
    decision = _classify(distance)

    nearest_kb = top.provenance.get("collection", "unknown") if top.provenance else "unknown"
    nearest_text = (top.text or "")[:200]

    # Build rationale showing top-3 for transparency
    rationale_parts = [
        f"nearest at distance {distance:.3f} ({decision.value})"
    ]
    extras = sorted(results, key=lambda r: r.score, reverse=True)[:3]
    for r in extras:
        d = max(0.0, 1.0 - float(r.score))
        col = r.provenance.get("collection", "?") if r.provenance else "?"
        rationale_parts.append(f"  • d={d:.3f} [{col}] {r.text[:80]!r}")

    return NoveltyReport(
        decision=decision,
        nearest_distance=round(distance, 4),
        nearest_text=nearest_text,
        nearest_kb=nearest_kb,
        nearest_id=str(top.metadata.get("id", "")) if top.metadata else "",
        rationale="\n".join(rationale_parts),
    )


def is_duplicate(text: str, kbs: Optional[list[str]] = None) -> bool:
    """Convenience predicate: True if the candidate is already covered.

    Drop-in replacement for the legacy `_is_duplicate` in idle_scheduler,
    but using real embedding similarity instead of bag-of-words matching.
    """
    return novelty_report(text, kbs=kbs).is_duplicate
