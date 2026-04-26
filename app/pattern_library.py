"""
pattern_library.py — Distill successful mutations into reusable templates.

When evolution produces real improvements (delta > 0.05), they often share
underlying patterns: "wrap LLM call in retry," "batch ChromaDB queries,"
"add input validation before parse." This module extracts those patterns
and stores them in ChromaDB for the AVO planner to retrieve as exemplars.

Patterns are NOT applied automatically — they're surfaced to the planner
as inspiration, similar to how `evo_memory.py` surfaces past failures.
The difference: this is positive knowledge, not negative.

Pattern lifecycle:
  1. Extracted: at most weekly, from successful experiments
  2. Stored: in ChromaDB collection "evolution_patterns"
  3. Retrieved: during AVO planning when hypothesis matches
  4. Consolidated: similar patterns merged periodically

Reference: builds on evo_memory.py (failure side) and the broader DGM
pattern of accumulating wisdom from successful lineages.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

PATTERN_KB_COLLECTION = "evolution_patterns"
PATTERN_STATS_PATH = Path("/app/workspace/pattern_library_stats.json")

_MIN_DELTA_FOR_PATTERN = 0.05    # Mutations must have moved score by 5pp+
_MIN_OBSERVATIONS_FOR_CONSOLIDATION = 2
_PATTERN_SIMILARITY_THRESHOLD = 0.85


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Pattern:
    """A reusable evolution template extracted from successful mutations."""
    pattern_id: str
    description: str           # 1-2 sentence summary of what the pattern does
    template_summary: str      # Short summary of the structural change
    target_categories: tuple[str, ...]  # ["error_handling", "validation", ...]
    avg_delta: float
    times_observed: int
    source_experiments: tuple[str, ...]  # IDs of experiments this was extracted from
    distance: float = 1.0      # Filled by retrieval (lower = more similar)


# ── Pattern extraction (from successful experiments) ─────────────────────────

def _experiment_to_pattern_text(experiment: dict) -> str:
    """Build the searchable text for a pattern from an experiment record."""
    hypothesis = experiment.get("hypothesis", "")[:300]
    detail = experiment.get("detail", "")[:200]
    files = ", ".join(experiment.get("files_changed", [])[:3])
    return f"{hypothesis}\n\nFiles changed: {files}\n\nResult: {detail}"


def _compute_pattern_id(description: str) -> str:
    """Stable ID from description content."""
    return hashlib.sha256(description.lower().strip().encode()).hexdigest()[:16]


def _categorize_experiment(experiment: dict) -> tuple[str, ...]:
    """Tag an experiment with mutation strategy categories.

    Re-uses the same heuristic patterns as failure_taxonomy.py for consistency.
    """
    text = (experiment.get("hypothesis", "") + " " + experiment.get("detail", "")).lower()
    categories = []

    if any(kw in text for kw in ("retry", "timeout", "exception", "validate", "error handling")):
        categories.append("defensive")
    if any(kw in text for kw in ("refactor", "extract", "simplify", "dedupe", "consolidate")):
        categories.append("refactoring")
    if any(kw in text for kw in ("new tool", "new agent", "capability", "endpoint")):
        categories.append("capability")
    if any(kw in text for kw in ("agent hierarchy", "delegation", "context flow")):
        categories.append("architectural")
    if any(kw in text for kw in ("remove", "delete", "prune", "unused")):
        categories.append("removal")
    if any(kw in text for kw in ("cache", "batch", "parallel", "optimize", "latency")):
        categories.append("optimization")

    return tuple(categories) if categories else ("uncategorized",)


def extract_pattern_from_experiment(experiment: dict) -> Pattern | None:
    """Convert one successful experiment into a Pattern.

    Returns None if the experiment doesn't meet the minimum delta threshold.
    """
    delta = abs(experiment.get("delta", 0.0))
    if delta < _MIN_DELTA_FOR_PATTERN:
        return None
    if experiment.get("status") not in ("keep", "deployed"):
        return None

    hypothesis = experiment.get("hypothesis", "")[:300]
    description = hypothesis if hypothesis else experiment.get("detail", "")[:200]
    if not description:
        return None

    template_summary = experiment.get("detail", "")[:200] or hypothesis[:200]

    return Pattern(
        pattern_id=_compute_pattern_id(description),
        description=description,
        template_summary=template_summary,
        target_categories=_categorize_experiment(experiment),
        avg_delta=delta,
        times_observed=1,
        source_experiments=(experiment.get("experiment_id", ""),),
    )


# ── Storage ──────────────────────────────────────────────────────────────────

def store_pattern(pattern: Pattern) -> None:
    """Store a pattern in ChromaDB. Deduplicates by pattern_id.

    If a pattern with the same ID already exists, averages the delta and
    increments times_observed.
    """
    try:
        from app.memory.chromadb_manager import store, retrieve_with_metadata
    except Exception as e:
        logger.debug(f"pattern_library: ChromaDB unavailable: {e}")
        return

    try:
        # Check for existing entry
        existing = retrieve_with_metadata(
            PATTERN_KB_COLLECTION, pattern.description[:200], n=3,
        )

        for doc, meta in existing:
            if meta and meta.get("pattern_id") == pattern.pattern_id:
                # Update existing: incremental average + count
                old_count = meta.get("times_observed", 1)
                old_avg = meta.get("avg_delta", pattern.avg_delta)
                new_count = old_count + 1
                new_avg = (old_avg * old_count + pattern.avg_delta) / new_count

                merged_sources = tuple(set(
                    list(meta.get("source_experiments", "").split(",")) +
                    list(pattern.source_experiments)
                ))[:10]

                store(
                    PATTERN_KB_COLLECTION, pattern.description[:500],
                    metadata={
                        "pattern_id": pattern.pattern_id,
                        "template_summary": pattern.template_summary,
                        "target_categories": ",".join(pattern.target_categories),
                        "avg_delta": round(new_avg, 6),
                        "times_observed": new_count,
                        "source_experiments": ",".join(merged_sources),
                        "ts": time.time(),
                    },
                )
                logger.info(
                    f"pattern_library: updated {pattern.pattern_id} "
                    f"(observed {new_count}x, avg_delta={new_avg:+.4f})"
                )
                return

        # New pattern
        store(
            PATTERN_KB_COLLECTION, pattern.description[:500],
            metadata={
                "pattern_id": pattern.pattern_id,
                "template_summary": pattern.template_summary,
                "target_categories": ",".join(pattern.target_categories),
                "avg_delta": round(pattern.avg_delta, 6),
                "times_observed": pattern.times_observed,
                "source_experiments": ",".join(pattern.source_experiments),
                "ts": time.time(),
            },
        )
        logger.info(
            f"pattern_library: stored new pattern {pattern.pattern_id} "
            f"(delta={pattern.avg_delta:+.4f}, categories={pattern.target_categories})"
        )

    except Exception as e:
        logger.debug(f"pattern_library: store failed: {e}")


# ── Retrieval ────────────────────────────────────────────────────────────────

def find_relevant_patterns(hypothesis: str, n: int = 3) -> list[Pattern]:
    """Search the pattern library for templates relevant to a hypothesis.

    Used by the AVO planner to surface past successful mutations as
    inspiration for new proposals.
    """
    try:
        from app.memory.chromadb_manager import retrieve_with_metadata
    except Exception:
        return []

    try:
        results = retrieve_with_metadata(PATTERN_KB_COLLECTION, hypothesis[:500], n=n * 2)
    except Exception as e:
        logger.debug(f"pattern_library: lookup failed: {e}")
        return []

    patterns = []
    for i, (doc, meta) in enumerate(results[:n]):
        if not meta:
            continue
        try:
            patterns.append(Pattern(
                pattern_id=meta.get("pattern_id", ""),
                description=doc[:300],
                template_summary=meta.get("template_summary", ""),
                target_categories=tuple(
                    c for c in meta.get("target_categories", "").split(",") if c
                ),
                avg_delta=float(meta.get("avg_delta", 0.0)),
                times_observed=int(meta.get("times_observed", 1)),
                source_experiments=tuple(
                    s for s in meta.get("source_experiments", "").split(",") if s
                ),
                distance=meta.get("distance", 1.0 - (1.0 / (i + 2))),
            ))
        except (ValueError, TypeError):
            continue

    return patterns


# ── Bulk extraction (background job) ─────────────────────────────────────────

def extract_patterns_from_history() -> int:
    """Scan results.tsv for successful experiments and extract patterns.

    Designed for idle_scheduler as a HEAVY job (typically <30s).
    Returns count of new/updated patterns.
    """
    try:
        from app.results_ledger import get_recent_results
    except Exception:
        return 0

    recent = get_recent_results(200)
    extracted = 0

    for experiment in recent:
        pattern = extract_pattern_from_experiment(experiment)
        if pattern:
            store_pattern(pattern)
            extracted += 1

    if extracted:
        logger.info(f"pattern_library: extracted/updated {extracted} patterns from history")

    _save_stats({"last_extraction": time.time(), "last_count": extracted})
    return extracted


def _save_stats(stats: dict) -> None:
    try:
        existing = {}
        if PATTERN_STATS_PATH.exists():
            existing = json.loads(PATTERN_STATS_PATH.read_text())
        existing.update(stats)
        PATTERN_STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
        PATTERN_STATS_PATH.write_text(json.dumps(existing, indent=2, default=str))
    except OSError:
        pass


def get_library_stats() -> dict:
    """Aggregate stats for the dashboard."""
    try:
        from app.memory.chromadb_manager import retrieve_with_metadata
        all_patterns = retrieve_with_metadata(PATTERN_KB_COLLECTION, "", n=200)
        total_patterns = len(all_patterns)
        total_observations = sum(
            int(m.get("times_observed", 1)) for _, m in all_patterns if m
        )

        category_counts: dict[str, int] = {}
        for _, m in all_patterns:
            if m:
                for cat in m.get("target_categories", "").split(","):
                    if cat:
                        category_counts[cat] = category_counts.get(cat, 0) + 1

        return {
            "total_patterns": total_patterns,
            "total_observations": total_observations,
            "by_category": category_counts,
        }
    except Exception:
        return {"total_patterns": 0, "total_observations": 0, "by_category": {}}
