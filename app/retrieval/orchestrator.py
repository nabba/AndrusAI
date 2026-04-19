"""
retrieval/orchestrator.py — Unified cross-KB retrieval with two-stage ranking.

The orchestrator composes all retrieval primitives:
  1. Query decomposition (complex queries → sub-queries)
  2. Parallel multi-collection vector retrieval
  3. Deduplication across sub-queries and collections
  4. Temporal freshness weighting (opt-in per collection)
  5. Cross-encoder re-ranking
  6. Provenance tagging (which KB, which sub-query, all scores)

Usage:
    from app.retrieval import RetrievalOrchestrator, RetrievalConfig

    orch = RetrievalOrchestrator(RetrievalConfig(temporal_enabled=True))
    results = orch.retrieve(
        query="ethics of autonomous AI with Stoic principles",
        collections=["enterprise_knowledge", "philosophy_humanist"],
        top_k=5,
    )
    for r in results:
        print(r.provenance["collection"], r.score, r.text[:80])

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from app.retrieval import config as cfg
from app.retrieval.decomposer import decompose_query
from app.retrieval.reranker import rerank
from app.retrieval.temporal import apply_temporal_decay

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """A single retrieval result with full provenance."""

    text: str
    score: float
    metadata: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)


class RetrievalOrchestrator:
    """Two-stage retrieval orchestrator for all knowledge bases.

    Backward-compatible — using this is opt-in.  Existing KB code
    (``KnowledgeStore.query()``, ``PhilosophyStore.query()``, etc.)
    continues to work unchanged.
    """

    def __init__(self, config: cfg.RetrievalConfig | None = None):
        self.config = config or cfg.RetrievalConfig()
        self._pool = ThreadPoolExecutor(
            max_workers=self.config.max_parallel,
            thread_name_prefix="retrieval-orch",
        )

    # ── Public API ──────────────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        collections: list[str],
        top_k: int = cfg.RERANK_TOP_K_OUTPUT,
        where_filter: dict | None = None,
        min_score: float = 0.25,
        task_id: str | None = None,
    ) -> list[RetrievalResult]:
        """Retrieve from one or more ChromaDB collections with full pipeline.

        Parameters
        ----------
        query : str
            The natural-language query.
        collections : list[str]
            ChromaDB collection names to search.
        top_k : int
            Final number of results to return.
        where_filter : dict | None
            ChromaDB ``where`` clause applied to every collection.
        min_score : float
            Minimum semantic similarity score (pre-rerank filter).
        task_id : str | None
            If set, this retrieval is on behalf of a real user task.
            A weak result (no hits, or top score below the gap-detector
            threshold) emits a `RETRIEVAL_MISS` LearningGap.  Internal
            callers (Novelty Gate, etc.) should leave this None to avoid
            self-referential emission.
        """
        if not query or not collections:
            if task_id is not None:
                self._emit_miss_safe(query, 0.0, collections, task_id)
            return []

        # Step 1: Decompose query if enabled and query is complex.
        if self.config.decomposition_enabled:
            sub_queries = decompose_query(
                query,
                max_subqueries=self.config.max_subqueries,
                min_length=self.config.decomposition_min_length,
            )
        else:
            sub_queries = [query]

        # Step 2: Parallel retrieval across (sub_query × collection) pairs.
        n_first_stage = (
            self.config.rerank_top_k_input if self.config.rerank_enabled else top_k
        )
        raw_results = self._parallel_retrieve(
            sub_queries, collections, n_first_stage, where_filter, min_score
        )

        if not raw_results:
            return []

        # Step 3: Deduplicate by text content hash.
        deduped = self._deduplicate(raw_results)

        # Step 4: Temporal decay (if enabled).
        if self.config.temporal_enabled:
            deduped = apply_temporal_decay(
                deduped,
                timestamp_field=self.config.temporal_field,
                half_life_hours=self.config.temporal_half_life_hours,
                weight=self.config.temporal_weight,
            )

        # Step 5: Cross-encoder re-ranking.
        if self.config.rerank_enabled:
            ranked = rerank(query, deduped, top_k=top_k)
        else:
            ranked = sorted(
                deduped, key=lambda d: d.get("blended_score", d.get("score", 0)), reverse=True
            )[:top_k]

        # Step 6: Convert to RetrievalResult.
        results = [self._to_result(doc) for doc in ranked]

        # Self-Improvement gap signal: if this retrieval was on behalf of a
        # real task and the top score is weak, emit a RETRIEVAL_MISS gap.
        if task_id is not None:
            top_score = max((r.score for r in results), default=0.0)
            self._emit_miss_safe(query, top_score, collections, task_id)
            # Evaluator hit-tracking: count SkillRecord ids in results as hits.
            self._record_hits_safe(results)

        return results

    def _emit_miss_safe(
        self,
        query: str,
        top_score: float,
        collections: list[str],
        task_id: str,
    ) -> None:
        """Best-effort RETRIEVAL_MISS emission.  Never raises."""
        try:
            from app.self_improvement.gap_detector import emit_retrieval_miss
            emit_retrieval_miss(
                query=query, top_score=float(top_score),
                collections=list(collections), task_id=task_id,
            )
        except Exception:
            pass  # gap detector unavailable — silent

    def _record_hits_safe(self, results) -> None:
        """Best-effort Evaluator hit-tracking. Never raises.

        Inspects each result's metadata for `skill_record_id`; accumulates
        hits on those records so the Evaluator can update usage_count.
        """
        try:
            from app.self_improvement.evaluator import record_hits
            rids: list[str] = []
            for r in results:
                md = getattr(r, "metadata", None) or {}
                rid = md.get("skill_record_id") or md.get("id", "")
                if isinstance(rid, str) and rid.startswith("skill_"):
                    rids.append(rid)
            if rids:
                record_hits(rids)
        except Exception:
            pass

    def retrieve_single(
        self,
        query: str,
        collection: str,
        top_k: int = cfg.RERANK_TOP_K_OUTPUT,
        where_filter: dict | None = None,
        min_score: float = 0.25,
    ) -> list[RetrievalResult]:
        """Convenience: retrieve from a single collection."""
        return self.retrieve(
            query=query,
            collections=[collection],
            top_k=top_k,
            where_filter=where_filter,
            min_score=min_score,
        )

    # ── Task-conditional retrieval (arXiv:2603.10600) ──────────────────
    #
    # Additive helper — delegates to `retrieve()` with a composed
    # `where_filter`. Purely opt-in: existing callers keep the prior
    # behaviour byte-for-byte. The filter keys (`tip_type`, `agent_role`)
    # are the same metadata fields the Integrator writes in its
    # `_write_to_kb` adapter, so filtering is free (ChromaDB handles it
    # natively without extra traversal).

    def retrieve_task_conditional(
        self,
        query: str,
        collections: list[str],
        *,
        agent_role: str = "",
        predicted_failure_mode: str = "",
        tip_types: list[str] | None = None,
        top_k: int = cfg.RERANK_TOP_K_OUTPUT,
        min_score: float = 0.25,
        task_id: str | None = None,
        extra_where: dict | None = None,
    ) -> list[RetrievalResult]:
        """Retrieve with a task-context metadata filter.

        Parameters
        ----------
        query : str
            Natural-language query (same as ``retrieve``).
        collections : list[str]
            Collections to search.
        agent_role : str, optional
            If set, restricts to SkillRecords tagged with this agent_role
            (written by the Integrator from SkillDraft.agent_role).
        predicted_failure_mode : str, optional
            Observer's current prediction. When the mode is ``fix_spiral``
            and no explicit ``tip_types`` is supplied, the filter is
            narrowed to ``tip_type="recovery"`` — this is the paper's key
            recovery-tip injection.
        tip_types : list[str], optional
            Restrict to a specific set of tip_type values. When None and
            no predicted_failure_mode narrowing applies, all tip_types
            pass (including records with no tip_type, i.e. external-topic
            skills).
        extra_where : dict, optional
            Additional where-clause merged with the constructed filter.
            Caller-provided keys override the defaults.

        Notes
        -----
        The filter is only applied when at least one condition is active;
        otherwise we call ``retrieve()`` without a ``where_filter`` so
        existing callers with ``retrieve_task_conditional(...)`` still
        get unfiltered results as their fallback.
        """
        # Narrow to recovery tips when the Observer predicted fix_spiral
        # and the caller didn't pin tip_types explicitly.
        if tip_types is None and predicted_failure_mode == "fix_spiral":
            tip_types = ["recovery"]

        where: dict = {}
        conditions: list[dict] = []
        if tip_types:
            conditions.append({"tip_type": {"$in": list(tip_types)}})
        if agent_role:
            conditions.append({"agent_role": agent_role})

        if len(conditions) == 1:
            where = conditions[0]
        elif len(conditions) > 1:
            where = {"$and": conditions}

        if extra_where:
            if where:
                where = {"$and": [where, dict(extra_where)]}
            else:
                where = dict(extra_where)

        return self.retrieve(
            query=query,
            collections=collections,
            top_k=top_k,
            where_filter=(where or None),
            min_score=min_score,
            task_id=task_id,
        )

    # ── Internals ───────────────────────────────────────────────────────

    def _parallel_retrieve(
        self,
        sub_queries: list[str],
        collections: list[str],
        n: int,
        where_filter: dict | None,
        min_score: float,
    ) -> list[dict]:
        """Fetch candidates from all (sub_query, collection) pairs in parallel.

        Stage 4.2 fixes: graceful degradation on overall timeout (previously
        `as_completed` raising TimeoutError crashed the retrieve() call and
        returned nothing). Now we collect whatever completed within the
        deadline and cancel the rest.
        """
        from concurrent.futures import TimeoutError as _FutTimeout
        futures = {}
        for sq in sub_queries:
            for col_name in collections:
                fut = self._pool.submit(
                    self._retrieve_one, sq, col_name, n, where_filter, min_score
                )
                futures[fut] = (sq, col_name)

        results = []
        try:
            for fut in as_completed(futures, timeout=self.config.timeout_s):
                sq, col_name = futures[fut]
                try:
                    docs = fut.result(timeout=0.1)
                    for doc in docs:
                        doc.setdefault("provenance", {})
                        doc["provenance"]["collection"] = col_name
                        doc["provenance"]["sub_query"] = sq
                    results.extend(docs)
                except Exception as exc:
                    logger.debug(
                        "retrieval.orchestrator: failed for (%s, %s): %s",
                        sq[:50], col_name, exc,
                    )
        except _FutTimeout:
            # Global deadline hit — return partial results instead of crashing.
            n_done = sum(1 for f in futures if f.done())
            logger.debug(
                "retrieval.orchestrator: deadline %.1fs reached, %d/%d pairs done",
                self.config.timeout_s, n_done, len(futures),
            )
            for f in futures:
                if not f.done():
                    f.cancel()

        return results

    @staticmethod
    def _retrieve_one(
        query: str,
        collection_name: str,
        n: int,
        where_filter: dict | None,
        min_score: float,
    ) -> list[dict]:
        """Retrieve from a single ChromaDB collection."""
        try:
            from app.memory.chromadb_manager import retrieve_with_metadata

            raw = retrieve_with_metadata(collection_name, query, n=n)
            if not raw:
                return []

            results = []
            for item in raw:
                distance = item.get("distance", 1.0)
                score = max(0.0, 1.0 - distance)
                if score < min_score:
                    continue
                results.append({
                    "text": item.get("document", ""),
                    "score": round(score, 4),
                    "metadata": item.get("metadata", {}),
                    "provenance": {"semantic_score": round(score, 4)},
                })
            return results
        except Exception as exc:
            logger.debug(
                "retrieval.orchestrator._retrieve_one(%s): %s", collection_name, exc
            )
            return []

    @staticmethod
    def _deduplicate(results: list[dict]) -> list[dict]:
        """Remove duplicate passages by text hash, keeping the highest-scored."""
        seen: dict[str, dict] = {}
        for doc in results:
            text = doc.get("text", "")
            h = hashlib.md5(text.encode()).hexdigest()
            existing = seen.get(h)
            if existing is None or doc.get("score", 0) > existing.get("score", 0):
                seen[h] = doc
        return list(seen.values())

    @staticmethod
    def _to_result(doc: dict) -> RetrievalResult:
        """Convert internal dict to public RetrievalResult."""
        # Pick the best available score.
        score = doc.get("rerank_score", doc.get("blended_score", doc.get("score", 0.0)))
        prov = doc.get("provenance", {})
        # Annotate all available scores into provenance.
        if "rerank_score" in doc:
            prov["rerank_score"] = doc["rerank_score"]
        if "blended_score" in doc:
            prov["blended_score"] = doc["blended_score"]
        if "temporal_score" in doc:
            prov["temporal_score"] = doc["temporal_score"]

        return RetrievalResult(
            text=doc.get("text", ""),
            score=round(float(score), 4),
            metadata=doc.get("metadata", {}),
            provenance=prov,
        )
