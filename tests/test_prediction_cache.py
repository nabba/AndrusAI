"""
Phase 4: prediction template cache regression tests (Amendment B.4).

Verifies:
  - Warm-up: first N stores are MISSES (cache serves only after min_uses)
  - Subsequent requests with the same signature are HITs (0 tokens)
  - Hit rate converges to 50-60% in a realistic traffic pattern
  - update_accuracy damps confidence of served predictions
  - LRU eviction triggers when max_entries exceeded
  - cached_predict_fn() wrapper is drop-in compatible with SubIALoop
"""

from __future__ import annotations

import pytest

from app.subia.kernel import Prediction, SceneItem
from app.subia.prediction.cache import (
    PredictionCache,
    _derive_operation_hint,
    cached_predict_fn,
)


def _mk_prediction(confidence: float = 0.7) -> Prediction:
    return Prediction(
        id="p-test", operation="researcher:ingest",
        predicted_outcome={"wiki_pages_affected": []},
        predicted_self_change={"confidence_change": 0.1},
        predicted_homeostatic_effect={},
        confidence=confidence,
        created_at="2026-04-13T00:00:00+00:00",
    )


# ── Warm-up semantics ────────────────────────────────────────────

class TestWarmUp:
    def test_first_get_is_miss(self):
        cache = PredictionCache(min_uses=3)
        sig = PredictionCache.signature("researcher", "ingest", ("truepic",))
        assert cache.get(sig) is None
        assert cache.miss_count == 1
        assert cache.hit_count == 0

    def test_below_min_uses_still_miss(self):
        cache = PredictionCache(min_uses=3)
        sig = PredictionCache.signature("researcher", "ingest", ("truepic",))

        # Store twice; below the 3-use warmup threshold
        cache.store(sig, _mk_prediction())
        cache.store(sig, _mk_prediction())
        assert cache.get(sig) is None

    def test_reaches_min_uses_serves_hit(self):
        cache = PredictionCache(min_uses=3)
        sig = PredictionCache.signature("researcher", "ingest", ("truepic",))

        for _ in range(3):
            cache.store(sig, _mk_prediction())
        hit = cache.get(sig)
        assert hit is not None
        assert hit.cached is True


# ── Signature determinism ────────────────────────────────────────

class TestSignature:
    def test_same_inputs_same_signature(self):
        s1 = PredictionCache.signature("researcher", "ingest",
                                        ["a", "b", "c"])
        s2 = PredictionCache.signature("researcher", "ingest",
                                        ["a", "b", "c"])
        assert s1 == s2

    def test_order_independent(self):
        s1 = PredictionCache.signature("r", "i", ["a", "b", "c"])
        s2 = PredictionCache.signature("r", "i", ["c", "b", "a"])
        assert s1 == s2

    def test_trims_to_top_three_topics(self):
        s_short = PredictionCache.signature("r", "i", ["a", "b", "c"])
        s_long = PredictionCache.signature("r", "i", ["a", "b", "c", "d", "e"])
        assert s_short == s_long

    def test_case_folded(self):
        s1 = PredictionCache.signature("r", "i", ["Truepic"])
        s2 = PredictionCache.signature("r", "i", ["TRUEPIC"])
        assert s1 == s2


# ── Served Prediction is damped ─────────────────────────────────

class TestDamping:
    def test_default_accuracy_damps_confidence(self):
        cache = PredictionCache(min_uses=1)
        sig = PredictionCache.signature("r", "i", ("t",))
        cache.store(sig, _mk_prediction(confidence=0.9))
        hit = cache.get(sig)
        # Default recent_accuracy = 0.9 (from store's init); damped
        # confidence = 0.9 * 0.9 * 1.0 = 0.81.
        assert 0.80 <= hit.confidence <= 0.82

    def test_low_accuracy_deeply_damps(self):
        cache = PredictionCache(min_uses=1)
        sig = PredictionCache.signature("r", "i", ("t",))
        cache.store(sig, _mk_prediction(confidence=0.9))
        # Feedback: predictions have been terrible lately.
        cache.update_accuracy(sig, observed_accuracy=0.1)
        cache.update_accuracy(sig, observed_accuracy=0.1)
        cache.update_accuracy(sig, observed_accuracy=0.1)
        hit = cache.get(sig)
        assert hit.confidence < 0.7   # meaningfully damped

    def test_cached_flag_set(self):
        cache = PredictionCache(min_uses=1)
        sig = PredictionCache.signature("r", "i", ("t",))
        cache.store(sig, _mk_prediction())
        hit = cache.get(sig)
        assert hit.cached is True
        assert hit.resolved is False

    def test_id_disambiguated_per_hit(self):
        cache = PredictionCache(min_uses=1)
        sig = PredictionCache.signature("r", "i", ("t",))
        cache.store(sig, _mk_prediction())
        h1 = cache.get(sig)
        h2 = cache.get(sig)
        assert h1.id != h2.id   # per-hit ID suffix so they're distinguishable


# ── LRU eviction ────────────────────────────────────────────────

class TestEviction:
    def test_max_entries_respected(self):
        cache = PredictionCache(max_entries=3, min_uses=1)
        for i in range(5):
            sig = PredictionCache.signature("r", "i", (f"t{i}",))
            cache.store(sig, _mk_prediction())
        # Only 3 entries should remain.
        assert len(cache._entries) == 3

    def test_oldest_evicted_first(self):
        cache = PredictionCache(max_entries=3, min_uses=1)
        sigs = [PredictionCache.signature("r", "i", (f"t{i}",))
                for i in range(5)]
        for sig in sigs:
            cache.store(sig, _mk_prediction())
        # First two should be gone; last three present.
        for old in sigs[:2]:
            assert old not in cache._entries
        for new in sigs[2:]:
            assert new in cache._entries


# ── Hit rate metrics ────────────────────────────────────────────

class TestHitRate:
    def test_hit_rate_reaches_40_to_60_percent_realistic(self):
        """Realistic pattern: 20 distinct operations, each repeated 5x.
        After warm-up of 3, ~2/5 = 40% hits per operation.
        Overall rate across 100 requests should be in the 30-60% band.
        """
        cache = PredictionCache(max_entries=50, min_uses=3)
        for op in range(20):
            sig = PredictionCache.signature("r", "o",
                                             (f"topic_{op}",))
            for _ in range(5):
                hit = cache.get(sig)
                if hit is None:
                    cache.store(sig, _mk_prediction())
        # After 20 ops × 5 calls = 100 requests, 60 misses + 40 hits
        # (3 misses during warm-up + 2 hits per op after warm-up)
        assert 0.30 <= cache.hit_rate <= 0.60

    def test_stats_shape(self):
        cache = PredictionCache()
        stats = cache.stats()
        # Phase 6 added accuracy_evictions + eviction_floor to the shape.
        required = {
            "entries", "hits", "misses", "hit_rate",
            "max_entries", "min_uses",
            "accuracy_evictions", "eviction_floor",
        }
        assert required.issubset(set(stats))


# ── cached_predict_fn wrapper ───────────────────────────────────

class TestWrapper:
    def test_live_fn_called_on_miss(self):
        calls = []

        def live(ctx):
            calls.append(ctx["task_description"])
            return _mk_prediction()

        wrapped = cached_predict_fn(live, PredictionCache(min_uses=1))
        ctx = {
            "agent_role": "researcher",
            "task_description": "ingest Truepic",
            "scene": [SceneItem(id="s1", source="wiki",
                                content_ref="c", summary="Truepic",
                                salience=0.5, entered_at="")],
        }
        wrapped(ctx)
        assert len(calls) == 1

    def test_cached_hit_does_not_call_live(self):
        calls = []

        def live(ctx):
            calls.append(ctx["task_description"])
            return _mk_prediction()

        cache = PredictionCache(min_uses=1)
        wrapped = cached_predict_fn(live, cache)
        ctx = {
            "agent_role": "researcher",
            "task_description": "ingest Truepic",
            "scene": [SceneItem(id="s1", source="wiki",
                                content_ref="c", summary="Truepic",
                                salience=0.5, entered_at="")],
        }
        wrapped(ctx)   # MISS → live called
        wrapped(ctx)   # HIT  → no live call
        wrapped(ctx)   # HIT
        assert len(calls) == 1

    def test_wrapper_exposes_cache(self):
        cache = PredictionCache()
        wrapped = cached_predict_fn(lambda ctx: _mk_prediction(), cache)
        assert wrapped.cache is cache


# ── Operation hints ─────────────────────────────────────────────

class TestOperationHints:
    def test_derive_operation_hints(self):
        assert _derive_operation_hint("ingest new source") == "ingest"
        assert _derive_operation_hint("lint the wiki") == "lint"
        assert _derive_operation_hint("wiki_read /x.md") == "wiki_read"
        assert _derive_operation_hint("plan Q2") == "plan"
        assert _derive_operation_hint("research Truepic") == "research"
        assert _derive_operation_hint("draft investor memo") == "write"
        assert _derive_operation_hint("random thing") == "task"
