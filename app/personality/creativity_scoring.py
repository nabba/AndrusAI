"""
creativity_scoring.py — Torrance-style creativity metrics (Mechanism 7).

Lives at infrastructure level. NOT agent-modifiable: per CLAUDE.md safety
invariant, the Self-Improver agent cannot modify its own evaluation criteria,
and that extends to creativity evaluation.

Metrics (Torrance Tests of Creative Thinking, adapted for LLM output):
    fluency      — number of distinct ideas generated
    flexibility  — number of distinct categories (via embedding clustering)
    originality  — blended semantic distance from wiki corpus + Mem0 history
    elaboration  — detail/development depth (token-length heuristic + detail markers)

Originality blend weight is controlled by `app.creative_mode.get_originality_wiki_weight()`
— dashboard-adjustable per user request.

Degradation: when embeddings or corpora are unavailable, returns zeroed
subscores rather than raising. The caller can distinguish with the
`diagnostics` field in CreativityScores.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Iterable

logger = logging.getLogger(__name__)


# Delimiters that suggest an "idea" boundary in free-form LLM output.
# Numbered lists and bullet points are the common structural signals.
_IDEA_SPLIT_RE = re.compile(r"(?m)^\s*(?:\d+[.)]|[-*•])\s+")


@dataclass
class CreativityScores:
    """Torrance-style metrics plus diagnostic info for observability."""

    fluency: int
    flexibility: int
    originality: float
    elaboration: float
    diagnostics: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def extract_ideas(text: str) -> list[str]:
    """Split an output into discrete ideas.

    Uses list-marker heuristics first (numbered, bulleted). Falls back to
    paragraph splits if no markers are found. Very short fragments (<20 chars)
    are dropped as noise.
    """
    if not text or not text.strip():
        return []
    # Try marker-based split
    parts = _IDEA_SPLIT_RE.split(text)
    # _IDEA_SPLIT_RE.split puts the text BEFORE the first marker as parts[0],
    # then each marker-prefixed section. Drop the header prose.
    if len(parts) > 2:
        candidates = [p.strip() for p in parts[1:]]
    else:
        # Fall back to blank-line paragraph split
        candidates = [p.strip() for p in text.split("\n\n")]
    return [c for c in candidates if len(c) >= 20]


def _cosine_distance(a: list[float], b: list[float]) -> float:
    """Cosine distance in [0, 2]. Zeros when either vector is empty."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return 1.0 - (dot / (na * nb))


def _safe_embed(text: str) -> list[float] | None:
    try:
        from app.memory.chromadb_manager import embed
        return embed(text)
    except Exception as exc:
        logger.debug(f"creativity_scoring: embed failed: {exc}")
        return None


def _wiki_originality(idea: str, diagnostics: dict) -> float:
    """Mean distance from nearest wiki passages (higher = more original)."""
    try:
        from app.memory.chromadb_manager import retrieve
        # `retrieve` returns up to n matching strings ordered by similarity.
        # We flip similarity → distance via a fresh cosine on the idea vs. hit.
        hits = retrieve("wiki_corpus", idea, n=3)
    except Exception as exc:
        diagnostics["wiki_retrieve_error"] = str(exc)[:120]
        return 0.0
    if not hits:
        diagnostics["wiki_hits"] = 0
        return 1.0  # nothing to compare against → treat as maximally original
    idea_vec = _safe_embed(idea)
    if idea_vec is None:
        diagnostics["embed_error"] = "idea embed unavailable"
        return 0.0
    distances = []
    for h in hits:
        h_vec = _safe_embed(h)
        if h_vec is None:
            continue
        distances.append(_cosine_distance(idea_vec, h_vec))
    if not distances:
        return 0.0
    diagnostics["wiki_hits"] = len(distances)
    return sum(distances) / len(distances)


def _mem0_originality(idea: str, agent_role: str, diagnostics: dict) -> float:
    """Mean distance from the agent's own recent outputs via Mem0."""
    try:
        from app.memory.mem0_manager import get_manager
        mgr = get_manager()
    except Exception as exc:
        diagnostics["mem0_error"] = str(exc)[:120]
        return 0.0
    try:
        # Mem0 manager APIs vary; try the common "search" surface.
        results = mgr.search(query=idea, user_id=agent_role, limit=3)  # type: ignore[attr-defined]
    except Exception as exc:
        diagnostics["mem0_search_error"] = str(exc)[:120]
        return 0.0
    if not results:
        diagnostics["mem0_hits"] = 0
        return 1.0
    idea_vec = _safe_embed(idea)
    if idea_vec is None:
        return 0.0
    distances = []
    for r in results:
        text = r.get("memory", r.get("text", "")) if isinstance(r, dict) else str(r)
        if not text:
            continue
        r_vec = _safe_embed(text)
        if r_vec is None:
            continue
        distances.append(_cosine_distance(idea_vec, r_vec))
    if not distances:
        return 0.0
    diagnostics["mem0_hits"] = len(distances)
    return sum(distances) / len(distances)


def _originality(ideas: Iterable[str], agent_role: str, diagnostics: dict) -> float:
    """Blended wiki+Mem0 originality. Weight controlled at runtime."""
    from app.creative_mode import get_originality_wiki_weight
    w = get_originality_wiki_weight()
    scores = []
    for idea in ideas:
        wiki_d = _wiki_originality(idea, diagnostics)
        mem0_d = _mem0_originality(idea, agent_role, diagnostics)
        scores.append(w * wiki_d + (1.0 - w) * mem0_d)
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def _flexibility(ideas: list[str]) -> int:
    """Distinct categories via greedy embedding clustering.

    Threshold 0.35 cosine distance is a pragmatic default — two ideas closer
    than that are treated as the same category. This is coarse; we surface
    diagnostic info so it can be tuned empirically against tagged runs.
    """
    if not ideas:
        return 0
    vectors: list[list[float]] = []
    for idea in ideas:
        v = _safe_embed(idea)
        if v is not None:
            vectors.append(v)
    if not vectors:
        return len(ideas)  # no embeddings → over-count, don't under-count
    clusters: list[list[float]] = []
    for v in vectors:
        matched = False
        for c in clusters:
            if _cosine_distance(v, c) < 0.35:
                matched = True
                break
        if not matched:
            clusters.append(v)
    return len(clusters)


_DETAIL_MARKERS = (
    "because", "therefore", "for example", "specifically",
    "in particular", "such as", "e.g.", "i.e.",
)


def _elaboration(ideas: list[str]) -> float:
    """Average detail score per idea in [0, 1].

    Combines length signal (saturating at ~400 chars) with explicit detail
    markers (explanations, examples, justifications).
    """
    if not ideas:
        return 0.0
    scores = []
    for idea in ideas:
        length_score = min(len(idea) / 400.0, 1.0)
        lower = idea.lower()
        marker_hits = sum(1 for m in _DETAIL_MARKERS if m in lower)
        marker_score = min(marker_hits / 3.0, 1.0)
        scores.append(0.6 * length_score + 0.4 * marker_score)
    return sum(scores) / len(scores)


def score_output(text: str, agent_role: str = "creative_crew") -> CreativityScores:
    """Compute Torrance-style scores for a creative output.

    Non-raising: infrastructure failures (missing corpora, embedding errors)
    degrade to zeroed subscores and are reported in `diagnostics`.
    """
    diagnostics: dict = {}
    ideas = extract_ideas(text)
    diagnostics["idea_count"] = len(ideas)

    fluency = len(ideas)
    flexibility = _flexibility(ideas) if ideas else 0
    originality = _originality(ideas, agent_role, diagnostics) if ideas else 0.0
    elaboration = _elaboration(ideas)

    return CreativityScores(
        fluency=fluency,
        flexibility=flexibility,
        originality=round(originality, 4),
        elaboration=round(elaboration, 4),
        diagnostics=diagnostics,
    )
