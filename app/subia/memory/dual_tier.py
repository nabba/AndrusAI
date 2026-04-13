"""
subia.memory.dual_tier — Amendment C.3 differentiated memory access.

Default (agent-facing):
    memory.recall("TikTok API issues")          -> curated tier only
                                                   (fast, high-signal)

Deep (research, investigation):
    memory.recall_deep("TikTok API issues")     -> merged from both,
                                                   deduped by loop_count

Temporal (retrospective):
    memory.recall_around("2026-04-15", days=3)  -> full tier
                                                   (complete)

Curated results are annotated with _memory_tier="curated" so callers
know what they're working with. Full-tier results are similarly
annotated.

The promote_to_curated() method is used by retrospective_promotion
when a full-tier record is re-evaluated and found significant.

No client construction here. Pass in-memory fakes in tests and the
real Mem0 client in production. This module is ONLY about access
semantics.

Infrastructure-level. Not agent-modifiable. See PROGRAM.md Phase 7.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# The "MemoryClient protocol" (duck-typed). Clients must provide
# some subset of:
#   .add(record) -> id or {"id": ...}
#   .search(query, limit=N) -> list[record]
#   .get(record_id) -> record
#   .update(record_id, record) -> any
# All methods are optional; we guard defensively.


class DualTierMemoryAccess:
    """Differentiated access to curated and full memory tiers.

    Args:
        mem0_curated: client for the conscious tier
        mem0_full:    client for the subconscious tier
    """

    def __init__(self, mem0_curated: Any = None, mem0_full: Any = None) -> None:
        self.curated = mem0_curated
        self.full = mem0_full

    # ── Default recall (curated tier only) ──────────────────────

    def recall(self, query: str, limit: int = 5) -> list:
        """Fast recall from the curated tier.

        Returns the list of records annotated with _memory_tier="curated".
        On client failure, returns [].
        """
        results = _safe_search(self.curated, query, limit)
        return [self._annotate(r, "curated") for r in results]

    # ── Deep recall (both tiers, merged + deduped) ──────────────

    def recall_deep(self, query: str, limit: int = 10) -> list:
        """Recall from both tiers, deduplicated by loop_count.

        Order: curated first (higher-signal), then full-tier entries
        that don't collide with a curated loop_count. Each result is
        annotated with its source tier.
        """
        curated_hits = _safe_search(self.curated, query, limit)
        full_hits = _safe_search(self.full, query, limit * 2)

        seen = {
            _loop_count(r) for r in curated_hits
            if _loop_count(r) is not None
        }
        unique_full = [
            r for r in full_hits
            if _loop_count(r) not in seen
            or _loop_count(r) is None
        ]
        merged = (
            [self._annotate(r, "curated") for r in curated_hits]
            + [self._annotate(r, "full") for r in unique_full[:limit]]
        )
        return merged

    # ── Temporal recall (full tier, time-bounded) ───────────────

    def recall_around(
        self, timestamp: str, days: int = 3, limit: int = 20,
    ) -> list:
        """Retrospective temporal recall from the full tier.

        Implementation uses a query string with the timestamp so any
        semantic-search-only backend still gets useful hits. Real
        Mem0 clients with metadata filtering can be extended by
        subclassing.
        """
        query = f"experiences around {timestamp} +/- {days} days"
        results = _safe_search(self.full, query, limit)
        return [self._annotate(r, "full") for r in results]

    # ── Overlooked discovery ────────────────────────────────────

    def find_overlooked(
        self, recent_days: int = 14, significance_threshold: float = 0.3,
    ) -> list:
        """Full-tier below-threshold records that are re-examination
        candidates. Retrospective promotion scans these.
        """
        # We use a broad query because real backends vary in how they
        # filter. Consumers typically narrow further by re-evaluating
        # each candidate's current significance.
        recent = _safe_search(self.full, "recent experiences", 100)
        candidates: list[dict] = []
        for r in recent:
            if not isinstance(r, dict):
                continue
            if r.get("promoted_to_curated"):
                continue
            sig = _safe_float(r.get("significance"), 0.0)
            if sig < significance_threshold:
                # Too weak to bother re-examining at all.
                continue
            # Anything between threshold and curated-threshold is a
            # candidate for retrospective promotion.
            candidates.append(self._annotate(r, "full"))
        return candidates

    # ── Promotion ───────────────────────────────────────────────

    def promote_to_curated(
        self, full_record_id: str, reason: str,
        new_significance: float | None = None,
    ) -> bool:
        """Move a full-tier record into curated.

        Fetches the full record, enriches it with the promotion
        reason, writes to curated, and marks the full-tier record as
        promoted. Returns True on success, False on any failure.
        """
        if self.full is None or self.curated is None:
            return False

        get_fn = getattr(self.full, "get", None)
        if not callable(get_fn):
            return False
        try:
            record = get_fn(full_record_id)
        except Exception:
            logger.debug("dual_tier: get(%s) failed", full_record_id,
                         exc_info=True)
            return False
        if not isinstance(record, dict):
            return False

        enriched = dict(record)
        enriched["type"] = "promoted_episode"
        enriched["promoted_reason"] = str(reason)[:200]
        enriched["original_significance"] = _safe_float(
            record.get("significance"), 0.0,
        )
        enriched["promoted_significance"] = (
            float(new_significance) if new_significance is not None else 0.8
        )

        # Write to curated.
        add_fn = getattr(self.curated, "add", None)
        if not callable(add_fn):
            return False
        try:
            add_fn(enriched)
        except Exception:
            logger.debug("dual_tier: curated.add failed", exc_info=True)
            return False

        # Mark the full-tier record as promoted so find_overlooked
        # stops surfacing it.
        update_fn = getattr(self.full, "update", None)
        if callable(update_fn):
            try:
                marked = dict(record)
                marked["promoted_to_curated"] = True
                marked["promoted_reason"] = enriched["promoted_reason"]
                update_fn(full_record_id, marked)
            except Exception:
                logger.debug("dual_tier: full.update failed",
                             exc_info=True)

        return True

    # ── Internals ───────────────────────────────────────────────

    def _annotate(self, record, tier: str):
        if isinstance(record, dict):
            record = dict(record)
            record["_memory_tier"] = tier
        return record


# ── Module helpers ─────────────────────────────────────────────

def _safe_search(client: Any, query: str, limit: int) -> list:
    if client is None:
        return []
    search = getattr(client, "search", None)
    if not callable(search):
        return []
    try:
        out = search(query, limit=limit)
    except TypeError:
        try:
            out = search(query)
        except Exception:
            return []
    except Exception:
        logger.debug("dual_tier: search raised", exc_info=True)
        return []
    if out is None:
        return []
    try:
        return list(out)
    except Exception:
        return []


def _loop_count(r) -> int | None:
    if isinstance(r, dict):
        v = r.get("loop_count")
        if isinstance(v, (int, float)):
            return int(v)
    return None


def _safe_float(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default
