"""
subia.memory.spontaneous — Amendment C.4 associative memory surfacing.

When the scene's topics match a curated-tier episode with high
similarity, the episode spontaneously enters the scene as a new
SceneItem with source="memory". This is INVOLUNTARY recall: the
system is "reminded" by association, not by deliberate search.

Amendment C is explicit: ONLY curated memories can spontaneously
surface. Full-tier memories require deliberate recall_deep().
That is what makes curation "the conscious tier" — its entries are
available for automatic scene injection.

API:

    check_spontaneous_memories(scene_topics, mem0_curated, *,
                                limit=3, threshold=0.7) -> list[SceneItem]

The returned items are ready to be fed to CompetitiveGate.evaluate().
The salience is damped (0.7× relevance) so a memory only out-competes
current focal items when it's genuinely more relevant than the
least-salient existing focal item.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable

from app.subia.kernel import SceneItem

logger = logging.getLogger(__name__)


# Default associative-relevance threshold. Below this, the memory is
# not strongly enough related to enter the scene.
_DEFAULT_RELEVANCE_THRESHOLD = 0.7

# Salience damping factor: a memory always enters at salience =
# relevance * this factor, keeping live signals at a slight edge.
_MEMORY_SALIENCE_FACTOR = 0.7


def check_spontaneous_memories(
    scene_topics: Iterable[str],
    mem0_curated: Any,
    *,
    limit: int = 3,
    threshold: float = _DEFAULT_RELEVANCE_THRESHOLD,
) -> list[SceneItem]:
    """Return candidate SceneItems associatively surfaced from the
    curated memory tier.

    Args:
        scene_topics:  short strings describing what's currently in
                       focus — typically the top-3 focal-item
                       summaries.
        mem0_curated:  curated-tier memory client (duck-typed .search).
        limit:         max candidate memories to search for.
        threshold:     minimum similarity_score to qualify for surfacing.

    Returns a list of SceneItem (possibly empty). Never raises.
    """
    topics = [t for t in scene_topics if t]
    if not topics:
        return []
    if mem0_curated is None:
        return []

    query = " ".join(topics[:3])
    memories = _safe_search(mem0_curated, query, limit)

    candidates: list[SceneItem] = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for memory in memories:
        if not isinstance(memory, dict):
            continue
        relevance = _safe_float(
            memory.get("similarity_score")
            or memory.get("score")
            or memory.get("relevance"),
            0.0,
        )
        if relevance < threshold:
            continue
        rec_id = str(memory.get("id") or memory.get("record_id") or "")
        summary_src = (
            memory.get("result_summary")
            or memory.get("summary")
            or ""
        )
        candidates.append(SceneItem(
            id=f"mem-{rec_id[:36]}",
            source="memory",
            content_ref=f"mem0_curated:{rec_id}",
            summary=f"[Memory] {str(summary_src)[:60]}",
            salience=round(relevance * _MEMORY_SALIENCE_FACTOR, 3),
            entered_at=now_iso,
            ownership="self",
            valence=0.0,
            dominant_affect="neutral",
        ))
    return candidates


# ── Helpers ────────────────────────────────────────────────────

def _safe_search(client: Any, query: str, limit: int) -> list:
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
        logger.debug("spontaneous: search raised", exc_info=True)
        return []
    return list(out or [])


def _safe_float(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default
