"""Tests for the shared retrieval orchestrator (Phase 0)."""

import os
import pytest
from datetime import datetime, timezone, timedelta

_LOW_MEM = os.environ.get("LOW_MEM_TESTS", "1") == "1"


# ── Temporal decay tests ────────────────────────────────────────────────────

def test_temporal_decay_basic():
    """Newer results should score higher than older ones."""
    from app.retrieval.temporal import apply_temporal_decay

    now = datetime.now(timezone.utc)
    results = [
        {"text": "old", "score": 0.8, "metadata": {"ingested_at": (now - timedelta(days=14)).isoformat()}},
        {"text": "new", "score": 0.8, "metadata": {"ingested_at": (now - timedelta(hours=1)).isoformat()}},
    ]
    out = apply_temporal_decay(results, now=now)
    assert len(out) == 2
    # New should have higher blended score.
    assert out[0]["text"] == "new"
    assert out[0]["blended_score"] > out[1]["blended_score"]


def test_temporal_decay_no_timestamps():
    """Results without timestamps get neutral temporal score."""
    from app.retrieval.temporal import apply_temporal_decay

    results = [
        {"text": "a", "score": 0.7, "metadata": {}},
        {"text": "b", "score": 0.9, "metadata": {}},
    ]
    out = apply_temporal_decay(results)
    # Without temporal signal, order follows semantic score.
    assert out[0]["text"] == "b"


def test_temporal_decay_zero_weight():
    """Zero weight should produce identical blended and semantic scores."""
    from app.retrieval.temporal import apply_temporal_decay

    now = datetime.now(timezone.utc)
    results = [
        {"text": "a", "score": 0.5, "metadata": {"ingested_at": now.isoformat()}},
    ]
    out = apply_temporal_decay(results, weight=0.0, now=now)
    assert out[0]["blended_score"] == 0.5


# ── Decomposer tests ───────────────────────────────────────────────────────

def test_decomposer_short_query():
    """Short queries bypass decomposition entirely."""
    from app.retrieval.decomposer import decompose_query

    result = decompose_query("hello", min_length=50)
    assert result == ["hello"]


def test_decomposer_disabled():
    """When disabled, always returns original query."""
    from app.retrieval import config as cfg
    original = cfg.DECOMPOSITION_ENABLED
    try:
        cfg.DECOMPOSITION_ENABLED = False
        from app.retrieval.decomposer import decompose_query
        result = decompose_query("a" * 200, min_length=10)
        assert result == ["a" * 200]
    finally:
        cfg.DECOMPOSITION_ENABLED = original


# ── RetrievalResult tests ──────────────────────────────────────────────────

def test_retrieval_result_dataclass():
    from app.retrieval.orchestrator import RetrievalResult
    r = RetrievalResult(text="hello", score=0.9, metadata={"source": "test"}, provenance={"collection": "c"})
    assert r.text == "hello"
    assert r.score == 0.9
    assert r.provenance["collection"] == "c"


# ── Config tests ───────────────────────────────────────────────────────────

def test_retrieval_config_defaults():
    from app.retrieval.config import RetrievalConfig
    cfg = RetrievalConfig()
    assert cfg.rerank_enabled is True
    assert cfg.rerank_top_k_input == 20
    assert cfg.temporal_enabled is False
    assert cfg.decomposition_enabled is True


# ── Reranker graceful degradation ──────────────────────────────────────────

def test_reranker_empty_input():
    from app.retrieval.reranker import rerank
    assert rerank("query", []) == []


def test_reranker_passthrough_on_missing_model():
    """If model fails, results returned unchanged."""
    from app.retrieval import reranker
    # Temporarily force model to fail.
    original_failed = reranker._model_failed
    original_model = reranker._model
    reranker._model_failed = True
    reranker._model = None
    try:
        docs = [{"text": "hello", "score": 0.5}, {"text": "world", "score": 0.8}]
        result = reranker.rerank("query", docs, top_k=1)
        assert len(result) == 1
        assert "rerank_score" in result[0]
    finally:
        reranker._model_failed = original_failed
        reranker._model = original_model
