"""Tests for the result-cache invalidation discipline.

Contract:
  * ``cache_store()`` still writes whatever we pass — it's a dumb write
    API.  The *decision* to cache lives at the caller (orchestrator).
  * ``invalidate_by_task(task, crew_name=...)`` removes cache entries
    whose stored task is near-identical to the given task.
  * Strict similarity threshold (0.98) ensures unrelated tasks are
    NEVER evicted.
  * Works whether a crew filter is supplied or not.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from app import result_cache


# ══════════════════════════════════════════════════════════════════════
# Mock ChromaDB
# ══════════════════════════════════════════════════════════════════════

class FakeCollection:
    """Minimal in-memory stand-in for chromadb collection used by
    result_cache.  Stores (id → (document, metadata, embedding))."""

    def __init__(self):
        self._rows: dict[str, tuple[str, dict, list[float]]] = {}

    def add(self, *, documents, embeddings, metadatas, ids):
        for i, d, m, e in zip(ids, documents, metadatas, embeddings):
            self._rows[i] = (d, m, e)

    def query(self, *, query_embeddings, n_results=3, where=None,
              include=None):
        qemb = query_embeddings[0]
        results = []
        for id_, (doc, meta, emb) in self._rows.items():
            if where:
                if any(meta.get(k) != v for k, v in where.items()):
                    continue
            # Cosine-ish distance — for testing we just use an L1 diff
            # magnitude scaled into 0..2.  Tests craft embeddings so
            # near-identicals end up near 0 distance.
            dist = sum(abs(a - b) for a, b in zip(qemb, emb)) / max(len(qemb), 1)
            dist = min(dist, 2.0)
            results.append((id_, doc, meta, dist))
        results.sort(key=lambda x: x[3])
        top = results[:n_results]
        return {
            "ids": [[r[0] for r in top]],
            "documents": [[r[1] for r in top]],
            "metadatas": [[r[2] for r in top]],
            "distances": [[r[3] for r in top]],
        }

    def delete(self, *, ids):
        for i in ids:
            self._rows.pop(i, None)

    def get(self, *, where=None, include=None):
        out = []
        for id_, (doc, meta, emb) in self._rows.items():
            if where and any(meta.get(k) != v for k, v in where.items()):
                continue
            out.append((id_, meta))
        return {
            "ids": [i for i, _ in out],
            "metadatas": [m for _, m in out],
        }

    def count(self):
        return len(self._rows)


@pytest.fixture
def fake_col(monkeypatch):
    col = FakeCollection()
    monkeypatch.setattr(result_cache, "_get_collection", lambda: col)
    return col


@pytest.fixture
def deterministic_embed(monkeypatch):
    """Embedder that maps a string's first byte to a 1-D vector.
    Identical strings → identical vectors.  Slightly different strings
    → near-identical vectors.  Unrelated strings → distant vectors."""
    def _embed(text: str):
        # Use ord of first 3 chars as a 3-dim embedding — enough for
        # distinguishing "exact match" vs "totally unrelated" in tests.
        padded = (text or "").lower().ljust(3, ".")
        return [float(ord(c)) / 255.0 for c in padded[:3]]
    monkeypatch.setattr(result_cache, "_embed", _embed)
    return _embed


# ══════════════════════════════════════════════════════════════════════
# invalidate_by_task — the new API
# ══════════════════════════════════════════════════════════════════════

class TestInvalidateByTask:

    def test_exact_match_deletes(self, fake_col, deterministic_embed):
        result_cache.store(
            "research",
            "please merge the attached PSP list",
            "the real result",
        )
        assert fake_col.count() == 1

        n = result_cache.invalidate_by_task("please merge the attached PSP list")
        assert n == 1
        assert fake_col.count() == 0

    def test_near_identical_match_deletes(self, fake_col, deterministic_embed):
        # Both embed to the same vector under our fake embedder (same
        # first-3-chars): both start with "ple" → exact identical emb.
        result_cache.store("research", "please do X now", "result")
        # Invalidate with slightly different wording but same first 3 chars.
        n = result_cache.invalidate_by_task("please finish Y later")
        assert n == 1

    def test_unrelated_task_preserved(self, fake_col, deterministic_embed):
        """An invalidation call for an UNRELATED task must not evict
        legitimate entries."""
        result_cache.store(
            "research",
            "send me the weather forecast",  # starts with 'sen'
            "sunny",
        )
        assert fake_col.count() == 1

        # 'how do I cook pasta' starts with 'how' — very different embedding.
        n = result_cache.invalidate_by_task("how do I cook pasta")
        assert n == 0
        assert fake_col.count() == 1

    def test_crew_filter_isolates(self, fake_col, deterministic_embed):
        """Same task across two crews — invalidate_by_task with
        crew_name only touches one."""
        # Both entries embed the same (same first-3 chars), but different crews.
        result_cache.store("research", "merge the PSPs please", "research result")
        result_cache.store("coding",   "merge the PSPs please", "coding result")
        assert fake_col.count() == 2

        n = result_cache.invalidate_by_task(
            "merge the PSPs please", crew_name="research",
        )
        assert n == 1
        assert fake_col.count() == 1
        # The remaining row is the coding one
        remaining = list(fake_col._rows.values())[0]
        assert remaining[1]["crew"] == "coding"

    def test_empty_task_noop(self, fake_col, deterministic_embed):
        result_cache.store("research", "something cached", "result")
        assert result_cache.invalidate_by_task("") == 0
        assert result_cache.invalidate_by_task("  ") == 0
        assert result_cache.invalidate_by_task("xx") == 0  # too short
        assert fake_col.count() == 1  # nothing deleted

    def test_empty_cache_noop(self, fake_col, deterministic_embed):
        assert fake_col.count() == 0
        assert result_cache.invalidate_by_task("anything at all works") == 0


# ══════════════════════════════════════════════════════════════════════
# Resilience — never raises
# ══════════════════════════════════════════════════════════════════════

class TestFailSoft:

    def test_chromadb_error_returns_zero(self, monkeypatch):
        def _boom():
            raise RuntimeError("chromadb down")
        monkeypatch.setattr(result_cache, "_get_collection", _boom)

        # Must not raise — callers in abort paths rely on this.
        assert result_cache.invalidate_by_task("some task text") == 0

    def test_embed_error_returns_zero(self, fake_col, monkeypatch):
        def _boom(text):
            raise RuntimeError("embedder down")
        monkeypatch.setattr(result_cache, "_embed", _boom)
        # fake_col has count=0 so we never reach _embed — seed one row
        fake_col.add(
            documents=["x"],
            embeddings=[[0.1, 0.1, 0.1]],
            metadatas=[{"crew": "research", "cached_at": time.time()}],
            ids=["id1"],
        )
        assert result_cache.invalidate_by_task("some task") == 0
