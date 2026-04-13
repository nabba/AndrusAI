"""
subia.prediction.cache — prediction template cache (Amendment B.4).

Amendment B.4 target: 40-60% hit rate after warm-up, which drops the
hot-path LLM cost on routine predictions from ~400 tokens to 0.

The cache stores a `Prediction` template keyed by an operation
signature (agent_role + operation_type + top-3 scene topics). After
the template has been used `min_uses` times with reasonable accuracy,
subsequent calls return a lightly-adjusted copy instead of invoking
the LLM.

Design choices (from Amendment B.4):
  - Operation signature is coarse so similar tasks share cache rows.
  - `use_count` counts every store() call; we only serve HITs after
    `min_uses` is reached (warm-up).
  - `recent_accuracy` damps the served confidence so the cache cannot
    make the system over-confident on bad predictions.
  - `max_entries` is an LRU cap keyed by last_used time.

This module does not import LLM factories or databases. The cache is
a pure container; callers decide when to consult it and when to store.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional

from app.subia.config import SUBIA_CONFIG
from app.subia.kernel import Prediction

logger = logging.getLogger(__name__)


@dataclass
class _Entry:
    template: Prediction
    use_count: int = 0
    recent_accuracy: float = 0.5
    last_used_at: float = field(default_factory=time.monotonic)
    serve_count: int = 0   # incremented on every HIT served


class PredictionCache:
    """Operation-signature-keyed prediction template cache.

    Usage pattern:
        cache = PredictionCache()
        signature = cache.signature(agent_role, operation_type, topics)
        hit = cache.get(signature)       # -> Prediction | None
        if hit is not None:
            use hit (0 tokens, O(1))
        else:
            live = call_llm(...)
            cache.store(signature, live)

        # After a real outcome:
        cache.update_accuracy(signature, observed_accuracy=1 - error)
    """

    def __init__(
        self,
        *,
        max_entries: int | None = None,
        min_uses: int | None = None,
        confidence_dampener: float = 1.0,
        eviction_floor: float = 0.30,
        eviction_min_uses: int = 5,
    ) -> None:
        self._entries: dict[str, _Entry] = {}
        self.max_entries = int(
            max_entries if max_entries is not None
            else SUBIA_CONFIG["PREDICTION_CACHE_MAX_ENTRIES"]
        )
        self.min_uses = int(
            min_uses if min_uses is not None
            else SUBIA_CONFIG["PREDICTION_CACHE_MIN_USES"]
        )
        self.confidence_dampener = float(confidence_dampener)
        self._eviction_floor = float(eviction_floor)
        self._eviction_min_uses = int(eviction_min_uses)
        self.hit_count = 0
        self.miss_count = 0
        # Phase 6: count of entries evicted due to sustained low accuracy.
        self.accuracy_evictions = 0

    # ── Signature ────────────────────────────────────────────────

    @staticmethod
    def signature(
        agent_role: str,
        operation_type: str,
        scene_topics: Iterable[str] = (),
    ) -> str:
        topics = "|".join(sorted(
            str(t).strip().lower()[:32]
            for t in list(scene_topics)[:3]
        ))
        return f"{agent_role.strip()}::{operation_type.strip()}::{topics}"

    # ── Public API ───────────────────────────────────────────────

    def get(self, signature: str) -> Prediction | None:
        """Return a cached Prediction or None.

        None means MISS (or not-yet-warm). Callers should then call
        the real predict function and `store()` the result.
        """
        entry = self._entries.get(signature)
        if entry is None or entry.use_count < self.min_uses:
            self.miss_count += 1
            return None
        self.hit_count += 1
        entry.last_used_at = time.monotonic()
        entry.serve_count += 1
        return self._instantiate(entry)

    def store(self, signature: str, prediction: Prediction) -> None:
        """Record a live prediction against the signature. Existing
        entries have their use_count incremented; new entries evict
        the oldest if max_entries is exceeded.
        """
        if signature in self._entries:
            entry = self._entries[signature]
            entry.use_count += 1
            entry.last_used_at = time.monotonic()
            # Don't overwrite the template — we want stability. The
            # template is set once; subsequent stores just bump usage.
            return
        # New entry
        if len(self._entries) >= self.max_entries:
            self._evict_oldest()
        self._entries[signature] = _Entry(
            template=_template_copy(prediction),
            use_count=1,
            recent_accuracy=_safe_float(
                getattr(prediction, "confidence", 0.5), 0.5,
            ),
        )

    def update_accuracy(self, signature: str, observed_accuracy: float) -> None:
        """Feed back an observed accuracy (1 - error) for the entry.

        Rolling alpha-0.3 EMA. Kept cheap — called from the
        consolidator or the post-task step. Phase 6: entries whose
        recent_accuracy falls below `bad_accuracy_evict_floor` are
        evicted so the next call goes through the live LLM and
        refreshes the template.
        """
        entry = self._entries.get(signature)
        if entry is None:
            return
        alpha = 0.3
        new_accuracy = max(0.0, min(1.0, float(observed_accuracy)))
        entry.recent_accuracy = (1 - alpha) * entry.recent_accuracy + alpha * new_accuracy

        # Phase 6: accuracy-driven eviction. A cache entry that
        # keeps producing bad predictions must be refreshed from
        # live LLM. Only evict after the entry has been tested
        # enough times to be statistically meaningful.
        if (
            entry.use_count >= self._eviction_min_uses
            and entry.recent_accuracy < self._eviction_floor
        ):
            logger.info(
                "prediction_cache: accuracy-eviction for signature=%s "
                "(recent_accuracy=%.3f < floor=%.3f, use_count=%d)",
                signature, entry.recent_accuracy,
                self._eviction_floor, entry.use_count,
            )
            self._entries.pop(signature, None)
            self.accuracy_evictions = getattr(
                self, "accuracy_evictions", 0,
            ) + 1

    # ── Metrics ──────────────────────────────────────────────────

    @property
    def hit_rate(self) -> float:
        total = self.hit_count + self.miss_count
        if total == 0:
            return 0.0
        return self.hit_count / total

    def stats(self) -> dict:
        return {
            "entries":   len(self._entries),
            "hits":      self.hit_count,
            "misses":    self.miss_count,
            "hit_rate":  round(self.hit_rate, 4),
            "max_entries": self.max_entries,
            "min_uses":  self.min_uses,
            "accuracy_evictions": self.accuracy_evictions,
            "eviction_floor":     self._eviction_floor,
        }

    def clear(self) -> None:
        self._entries.clear()
        self.hit_count = 0
        self.miss_count = 0

    # ── Internals ────────────────────────────────────────────────

    def _instantiate(self, entry: _Entry) -> Prediction:
        """Return a damped copy of the template so the caller gets a
        fresh object with the cached=True flag set.
        """
        template = entry.template
        # Confidence is damped by recent_accuracy and the
        # confidence_dampener to prevent over-confident stale hits.
        damped = (
            float(template.confidence)
            * float(entry.recent_accuracy)
            * float(self.confidence_dampener)
        )
        damped = max(0.0, min(1.0, damped))
        return Prediction(
            id=f"{template.id}-cached-{entry.serve_count}",
            operation=template.operation,
            predicted_outcome=dict(template.predicted_outcome),
            predicted_self_change=dict(template.predicted_self_change),
            predicted_homeostatic_effect=dict(template.predicted_homeostatic_effect),
            confidence=damped,
            created_at=template.created_at,
            resolved=False,
            actual_outcome=None,
            prediction_error=None,
            cached=True,
        )

    def _evict_oldest(self) -> None:
        """Drop the least-recently-used entry."""
        if not self._entries:
            return
        oldest_sig = min(self._entries,
                          key=lambda k: self._entries[k].last_used_at)
        del self._entries[oldest_sig]


def _template_copy(p: Prediction) -> Prediction:
    """Defensive deep-ish copy so template mutations don't leak."""
    return Prediction(
        id=p.id,
        operation=p.operation,
        predicted_outcome=dict(p.predicted_outcome),
        predicted_self_change=dict(p.predicted_self_change),
        predicted_homeostatic_effect=dict(p.predicted_homeostatic_effect),
        confidence=float(p.confidence),
        created_at=p.created_at,
    )


def _safe_float(v: object, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ── Caching wrapper for predict_fn ────────────────────────────────

def cached_predict_fn(
    live_predict_fn,
    cache: Optional[PredictionCache] = None,
):
    """Wrap a predict_fn so hits go through the cache.

    predict_fn receives the ctx dict used by SubIALoop._step_predict.
    The wrapper:
      1. builds a signature from ctx['agent_role'], a derived operation
         type, and scene topic summaries
      2. checks the cache for a served Prediction
      3. on miss, calls the live predict_fn and stores the result
      4. on hit, returns the cached copy (0 LLM tokens)

    This lets SubIALoop stay cache-agnostic; callers opt in by wrapping.
    """
    cache = cache or PredictionCache()

    def _predict(ctx: dict):
        agent_role = str(ctx.get("agent_role", "agent"))
        description = str(ctx.get("task_description", ""))
        op_hint = _derive_operation_hint(description)
        topics = _scene_topics(ctx.get("scene", ()))
        sig = PredictionCache.signature(agent_role, op_hint, topics)

        hit = cache.get(sig)
        if hit is not None:
            return hit

        fresh = live_predict_fn(ctx)
        cache.store(sig, fresh)
        return fresh

    _predict.cache = cache  # expose for stats and tests
    return _predict


def _scene_topics(scene) -> list[str]:
    """Extract top-3 scene summaries as topic keys."""
    topics: list[str] = []
    for item in list(scene)[:5]:
        summary = str(getattr(item, "summary", ""))[:40]
        if summary:
            topics.append(summary)
        if len(topics) >= 3:
            break
    return topics


def _derive_operation_hint(description: str) -> str:
    """Coarse operation-type hint from the task description."""
    lower = description.lower()
    if "ingest" in lower:
        return "ingest"
    if "lint" in lower:
        return "lint"
    if "wiki_read" in lower or "wiki read" in lower:
        return "wiki_read"
    if "plan" in lower:
        return "plan"
    if "research" in lower:
        return "research"
    if "draft" in lower or "write" in lower:
        return "write"
    return "task"
