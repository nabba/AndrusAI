"""
retrieval/reranker.py — Cross-encoder re-ranking for two-stage retrieval.

Stage 1 (vector similarity) is fast but imprecise — it matches embeddings.
Stage 2 (cross-encoder) is slower but far more accurate — it reads the
actual query-document pair through a transformer and outputs a relevance
score.

The cross-encoder model is ~60M params and runs on CPU in ~10ms per pair,
so re-ranking 20 candidates adds ~200ms total.  This is acceptable for
knowledge-base queries where precision matters.

Graceful degradation: if the model fails to load (missing dependency,
OOM, etc.), the reranker returns input unchanged with a logged warning.
No crash, no silent data loss.

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.retrieval import config as cfg

logger = logging.getLogger(__name__)

# ── Lazy singleton ──────────────────────────────────────────────────────────

_lock = threading.Lock()
_model: Any | None = None
_model_failed: bool = False


def _get_model():
    """Lazy-load the cross-encoder model (thread-safe singleton)."""
    global _model, _model_failed
    if _model is not None:
        return _model
    if _model_failed:
        return None

    with _lock:
        # Double-check after acquiring lock.
        if _model is not None:
            return _model
        if _model_failed:
            return None
        try:
            from sentence_transformers import CrossEncoder

            _model = CrossEncoder(cfg.RERANKER_MODEL)
            logger.info(
                "retrieval.reranker: loaded cross-encoder '%s'", cfg.RERANKER_MODEL
            )
            return _model
        except Exception as exc:
            _model_failed = True
            logger.warning(
                "retrieval.reranker: failed to load cross-encoder '%s' — "
                "re-ranking disabled (will pass through): %s",
                cfg.RERANKER_MODEL,
                exc,
            )
            return None


# ── Public API ──────────────────────────────────────────────────────────────


def rerank(
    query: str,
    documents: list[dict],
    top_k: int = cfg.RERANK_TOP_K_OUTPUT,
    text_key: str = "text",
) -> list[dict]:
    """Re-rank *documents* by cross-encoder relevance to *query*.

    Each document dict must contain a *text_key* field with the passage
    text.  Returns a new list (sorted descending by rerank_score) with
    a ``rerank_score`` field added to each dict.

    If the cross-encoder is unavailable, returns *documents* unchanged
    (graceful degradation).

    Parameters
    ----------
    query : str
        The user/agent query.
    documents : list[dict]
        First-stage candidates, each containing at least ``text_key``.
    top_k : int
        How many to return after re-ranking.
    text_key : str
        Key in each dict that holds the passage text.
    """
    if not documents:
        return []

    model = _get_model()
    if model is None:
        # Graceful degradation — return as-is, capped to top_k.
        for doc in documents:
            doc.setdefault("rerank_score", doc.get("score", 0.0))
        return documents[:top_k]

    # Build (query, passage) pairs for the cross-encoder.
    pairs = []
    valid_docs = []
    for doc in documents:
        text = doc.get(text_key, "")
        if text:
            pairs.append((query, text))
            valid_docs.append(doc)

    if not pairs:
        return []

    try:
        scores = model.predict(pairs)
    except Exception as exc:
        logger.warning("retrieval.reranker: predict failed: %s", exc)
        for doc in documents:
            doc.setdefault("rerank_score", doc.get("score", 0.0))
        return documents[:top_k]

    # Attach scores and sort.
    for doc, score in zip(valid_docs, scores):
        doc["rerank_score"] = float(score)

    valid_docs.sort(key=lambda d: d["rerank_score"], reverse=True)
    return valid_docs[:top_k]
