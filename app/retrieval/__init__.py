"""
retrieval — Shared retrieval orchestrator for all knowledge bases.

Provides two-stage retrieval (vector + cross-encoder re-rank), LLM-powered
query decomposition, temporal freshness weighting, and cross-KB merging.

Backward-compatible: existing KB code works unchanged.  The orchestrator
is an opt-in upgrade layer.

IMMUTABLE — infrastructure-level module.
"""

from app.retrieval.config import RetrievalConfig
from app.retrieval.decomposer import decompose_query
from app.retrieval.orchestrator import RetrievalOrchestrator, RetrievalResult
from app.retrieval.reranker import rerank
from app.retrieval.temporal import apply_temporal_decay

__all__ = [
    "RetrievalOrchestrator",
    "RetrievalResult",
    "RetrievalConfig",
    "rerank",
    "decompose_query",
    "apply_temporal_decay",
]
