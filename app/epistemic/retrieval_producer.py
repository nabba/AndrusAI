"""Structural producer for epistemic claims from RAG retrievals.

Closes the activation gap surfaced 2026-05-26: the epistemic system
(``gate_output`` + calibration + verification extension) had every
consumer wired but no producer feeding the claim ledger. With nothing
to score, flipping ``EPISTEMIC_ENABLED=true`` was a behavioural no-op.

This module is the deterministic producer. Every time a context loader
in :mod:`app.agents.commander.context` retrieves passages from a
ChromaDB-backed KB (episteme / knowledge_base / experiential), it calls
:func:`emit_retrieval_claims` to turn each passage into a
:class:`app.epistemic.ledger.Claim`.

Design choices, in order of importance:

1. **Failure-isolated.** Every emit is wrapped in try/except at debug
   level. A producer fault must never disturb the reply path.

2. **Master switch.** ``epistemic_retrieval_producer_enabled`` defaults
   to ``False``. Operators flip it ON when ready to start growing the
   ledger; until then this is dormant code.

3. **VerificationStatus.ASSUMED.** A KB retrieval is honest as "accepted
   from prior claim, memory, or user" — *not* VERIFIED (which implies
   exact-answer evidence) and *not* INFERRED (derived from adjacent
   observation). Using ASSUMED keeps the inference_as_fact detector
   from firing on legitimate retrievals.

4. **Register.INTERNAL.** Retrieved passages aren't presented to the user
   verbatim; they shape the agent's reasoning. INTERNAL keeps them out
   of the user-visible-claim accounting.

5. **load_bearing=False.** We don't know at retrieval time whether the
   agent will actually use the passage. Mark load-bearing later (e.g.
   when a citation appears in the final reply) — out of scope for the
   initial producer.

6. **Per-task dedup.** A passage retrieved twice within the same task
   (e.g. across sub-queries) emits only one claim. The dedup key is
   ``(task_id, kb_name, doc_id)`` held in a small bounded LRU.

7. **Cost.** Zero LLM. Each claim is a Postgres UPSERT (~1ms) plus the
   realtime-detector pass (in-process). At 7 claims/reply × 200
   replies/day ≈ 1,400 inserts/day ≈ 510k/year — well within Postgres
   capacity and inside the per-task soft cap of 500 claims.

8. **Idempotent under retry.** Same passage in the same task is one
   claim, regardless of how many times the loader runs.

Usage::

    from app.epistemic.retrieval_producer import emit_retrieval_claims

    emit_retrieval_claims(
        task_id=task_id,
        kb_name="episteme",
        query=task_text,
        passages=[{"text": "...", "metadata": {...}, "rerank_score": 0.82}, ...],
    )

Operator activation::

    # 1. Flip the master switch (gateway picks up on next save):
    curl -X POST $GW/api/cp/settings \\
         -H 'Content-Type: application/json' \\
         -d '{"epistemic_retrieval_producer_enabled": true}'

    # 2. Watch the ledger grow (table is in postgres):
    psql -c 'SELECT COUNT(*) FROM control_plane.epistemic_claims
             WHERE tags ? "retrieval"'

    # 3. After ~7 days of accumulation, proceed to Stage B (Advisory).
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any, Iterable, Mapping

from app.epistemic.ledger import (
    Claim,
    Evidence,
    Ledger,
    Register,
    VerificationStatus,
)

logger = logging.getLogger(__name__)

# Soft caps on per-passage payload that lands in the claim row.
_MAX_STATEMENT_CHARS = 300
_MAX_EXCERPT_CHARS = 800

# Per-task dedup memory. Bounded LRU so a runaway task can't OOM us.
_DEDUP_LRU: "OrderedDict[tuple[str, str, str], None]" = OrderedDict()
_DEDUP_LRU_MAX = 4096


def _enabled() -> bool:
    """Master switch. False → producer is a no-op."""
    try:
        from app.runtime_settings import get_epistemic_retrieval_producer_enabled
        return bool(get_epistemic_retrieval_producer_enabled())
    except Exception:
        return False


def _seen(task_id: str, kb_name: str, doc_id: str) -> bool:
    """Return True if this passage was already claimed this task. Side
    effect: marks it as seen.

    Uses a single shared LRU across all tasks. Eviction is FIFO at
    ``_DEDUP_LRU_MAX`` entries — sufficient for ~600 concurrent tasks
    with the default per-task cap of ~7 retrievals."""
    key = (task_id, kb_name, doc_id)
    if key in _DEDUP_LRU:
        # Move-to-end keeps the live tasks warm.
        _DEDUP_LRU.move_to_end(key)
        return True
    _DEDUP_LRU[key] = None
    while len(_DEDUP_LRU) > _DEDUP_LRU_MAX:
        _DEDUP_LRU.popitem(last=False)
    return False


def _score_of(passage: Mapping[str, Any]) -> float:
    """Best available retrieval score, clamped to [0.0, 1.0].

    KB loaders normalize on different field names — ``rerank_score`` for
    cross-encoder rerank, ``blended_score`` after business/global merge,
    ``score`` from the vanilla embedding query. We accept any of them
    and clamp; a missing score becomes 0.0 (claim still emits, just with
    low evidence confidence).
    """
    for key in ("rerank_score", "blended_score", "score"):
        if key in passage and passage[key] is not None:
            try:
                v = float(passage[key])
                return max(0.0, min(1.0, v))
            except (TypeError, ValueError):
                continue
    return 0.0


def _doc_id_of(passage: Mapping[str, Any], kb_name: str) -> str:
    """Stable identifier for the retrieved passage.

    Prefers an explicit metadata doc_id; falls back to a content hash so
    dedup still works even when the KB doesn't expose ids."""
    meta = passage.get("metadata") or {}
    for key in ("doc_id", "id", "source", "source_file", "title"):
        v = meta.get(key)
        if v:
            return f"{kb_name}:{str(v)[:120]}"
    text = passage.get("text", "")
    if text:
        import hashlib
        h = hashlib.sha256(text[:1024].encode("utf-8", errors="ignore"))
        return f"{kb_name}:hash:{h.hexdigest()[:16]}"
    return f"{kb_name}:unknown"


def _statement_for(passage: Mapping[str, Any], kb_name: str) -> str:
    """One-line summary of what the system is taking as background.

    Statement is what calibration's bias detectors see, so it must
    *describe* the passage rather than *be* the passage — otherwise the
    detectors would be lexically scanning the source material instead of
    the agent's claim about it."""
    meta = passage.get("metadata") or {}
    title = meta.get("title") or meta.get("source") or meta.get("source_file") or ""
    text = (passage.get("text") or "").strip().replace("\n", " ")
    head = text[: _MAX_STATEMENT_CHARS]
    if title:
        return f"Retrieved from {kb_name} [{title[:80]}]: {head}"
    return f"Retrieved from {kb_name}: {head}"


def emit_retrieval_claims(
    *,
    task_id: str,
    kb_name: str,
    query: str,
    passages: Iterable[Mapping[str, Any]],
    agent_role: str = "retrieval",
) -> int:
    """Convert RAG retrieval results into claims on the per-task ledger.

    Returns the number of claims actually emitted (after dedup + skip
    gates). Failure-isolated end-to-end: any internal exception is
    caught and logged at debug; the caller always sees a return value.

    Arguments:
      task_id:   ContextVar-resolved id for the current request.
                 Empty string disables emission for this call.
      kb_name:   One of ``"episteme"`` / ``"knowledge"`` / ``"experiential"``
                 etc. Used as both source-ref prefix and tag.
      query:     The retrieval query that produced ``passages``. Logged
                 against the claim's tags for advisory-report filtering.
      passages:  Iterable of dicts with at minimum a ``"text"`` field;
                 ``"metadata"`` and a score field (``rerank_score`` /
                 ``blended_score`` / ``score``) are honoured if present.
      agent_role: Free-form actor tag (default ``"retrieval"``).
    """
    if not _enabled():
        return 0
    if not task_id:
        return 0

    try:
        ledger = Ledger(task_id=task_id)
    except Exception:
        logger.debug("retrieval_producer: ledger init failed", exc_info=True)
        return 0

    n_emitted = 0
    for passage in passages:
        try:
            if not isinstance(passage, Mapping):
                continue
            text = (passage.get("text") or "").strip()
            if not text:
                continue
            doc_id = _doc_id_of(passage, kb_name)
            if _seen(task_id, kb_name, doc_id):
                continue
            score = _score_of(passage)
            claim = Claim.new(
                task_id=task_id,
                agent_role=agent_role,
                statement=_statement_for(passage, kb_name),
                status=VerificationStatus.ASSUMED,
                register=Register.INTERNAL,
                evidence=(
                    Evidence(
                        kind="memory_lookup",
                        source_ref=doc_id,
                        excerpt=text[:_MAX_EXCERPT_CHARS],
                        confidence=score,
                    ),
                ),
                load_bearing=False,
                tags=("retrieval", kb_name, f"q:{query[:60]}"),
            )
            ledger.emit(claim)
            n_emitted += 1
        except Exception:
            # Per-claim failure must not break the rest of the batch.
            logger.debug(
                "retrieval_producer: emit failed (kb=%s)", kb_name,
                exc_info=True,
            )
            continue

    if n_emitted:
        logger.debug(
            "retrieval_producer: emitted %d claims (kb=%s task=%s)",
            n_emitted, kb_name, task_id[:16],
        )
    return n_emitted


def reset_dedup_cache() -> None:
    """Clear the per-task dedup LRU. Test-only hook."""
    _DEDUP_LRU.clear()
