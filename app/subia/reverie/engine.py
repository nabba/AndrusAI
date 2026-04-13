"""Reverie Engine orchestration (Proposal 1 §1.2).

ALL external dependencies are injected via ReverieAdapters so this
module is unit-testable without ChromaDB / Neo4j / OpenRouter / Mem0.
The engine itself is deterministic given the adapters' return values.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class ReverieAdapters:
    """Pluggable bindings to existing infrastructure.

    Each adapter MUST be safe to call repeatedly. Failures should
    return empty results, not raise — the engine treats absence as
    'no resonance found this cycle'.
    """
    pick_random_wiki_page: Callable[[], dict]                       # () → {id, title, section}
    walk_neo4j: Callable[[str, int], list]                          # (start_id, steps) → [node]
    fiction_search: Callable[[str], list]                           # (query) → [chunk]
    philosophical_search: Callable[[str], list]                     # (query) → [chunk]
    mem0_full_search: Callable[[str, int], list]                    # (query, limit) → [memory]
    llm_resonance: Callable[[str, str], str]                        # (concept_a, concept_b) → "no resonance" | sentence
    llm_synthesis: Callable[[list], str]                            # (concepts) → text
    write_reverie_page: Callable[[str, str, dict], str]             # (slug, body, frontmatter) → wiki_path
    write_neo4j_analogy: Callable[[str, str, dict], None] = lambda *a, **k: None


@dataclass
class ReverieResult:
    cycle_id: str
    walk_path: list
    resonances: list
    fiction_used: bool
    philosophical_used: bool
    surfaced_memories: list
    synthesis_page: Optional[str]   # wiki path written, or None
    tokens_spent: int


class ReverieEngine:
    def __init__(
        self,
        adapters: ReverieAdapters,
        *,
        walk_steps: int = 4,
        fiction_probability: float = 0.3,
        philosophical_probability: float = 0.2,
        rng: Optional[random.Random] = None,
    ) -> None:
        self.adapters = adapters
        self.walk_steps = walk_steps
        self.fiction_probability = fiction_probability
        self.philosophical_probability = philosophical_probability
        self._rng = rng or random.Random()

    # ── Public API ───────────────────────────────────────────────────
    def run_cycle(self) -> ReverieResult:
        """Execute one reverie cycle and return what happened."""
        cycle_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        tokens = 0
        try:
            start = self.adapters.pick_random_wiki_page() or {}
        except Exception:
            start = {}

        walk = self._walk(start)
        resonances = self._eval_resonances(walk)
        tokens += 100 * max(1, len(walk))

        fiction_used = False
        philosophical_used = False
        if self._rng.random() < self.fiction_probability:
            fiction_used = self._collide_with("fiction", walk)
            tokens += 300 if fiction_used else 0
        if self._rng.random() < self.philosophical_probability:
            philosophical_used = self._collide_with("philosophical", walk)
            tokens += 300 if philosophical_used else 0

        surfaced = self._surface_memories(walk)

        page_path: Optional[str] = None
        if resonances:
            page_path = self._write_synthesis(cycle_id, walk, resonances,
                                              fiction_used, philosophical_used)
            tokens += 500

        return ReverieResult(
            cycle_id=cycle_id,
            walk_path=walk,
            resonances=resonances,
            fiction_used=fiction_used,
            philosophical_used=philosophical_used,
            surfaced_memories=surfaced,
            synthesis_page=page_path,
            tokens_spent=tokens,
        )

    # ── Internals ────────────────────────────────────────────────────
    def _walk(self, start: dict) -> list:
        if not start:
            return []
        try:
            return self.adapters.walk_neo4j(
                start.get("id", ""), self.walk_steps
            ) or [start]
        except Exception:
            return [start]

    def _eval_resonances(self, walk: list) -> list:
        out = []
        for i in range(len(walk) - 1):
            a = (walk[i] or {}).get("title", "")
            b = (walk[i + 1] or {}).get("title", "")
            if not (a and b):
                continue
            try:
                verdict = self.adapters.llm_resonance(a, b) or ""
            except Exception:
                verdict = ""
            if verdict and "no resonance" not in verdict.lower():
                out.append({"a": a, "b": b, "note": verdict})
        return out

    def _collide_with(self, source: str, walk: list) -> bool:
        topics = " ".join((n or {}).get("title", "") for n in walk)
        try:
            if source == "fiction":
                hits = self.adapters.fiction_search(topics) or []
            else:
                hits = self.adapters.philosophical_search(topics) or []
        except Exception:
            hits = []
        return bool(hits)

    def _surface_memories(self, walk: list) -> list:
        topics = " ".join((n or {}).get("title", "") for n in walk)
        if not topics:
            return []
        try:
            return self.adapters.mem0_full_search(topics, 3) or []
        except Exception:
            return []

    def _write_synthesis(
        self,
        cycle_id: str,
        walk: list,
        resonances: list,
        fiction_used: bool,
        philosophical_used: bool,
    ) -> Optional[str]:
        try:
            body = self.adapters.llm_synthesis(
                [{"node": n, "resonances": resonances} for n in walk]
            )
        except Exception:
            body = ""
        if not body:
            return None
        slug = "-".join(
            ((walk[0] or {}).get("title", "untitled")).lower().split()[:3]
        ) or "synthesis"
        frontmatter = {
            "title": f"Reverie: {(walk[0] or {}).get('title', 'untitled')}",
            "section": "meta/reverie",
            "page_type": "synthesis",
            "epistemic_status": "speculative",
            "created_by": "reverie-engine",
            "cycle_id": cycle_id,
            "fiction_used": fiction_used,
            "philosophical_used": philosophical_used,
            "ownership": {"owned_by": "self"},
            "sources": [
                (n or {}).get("id", "") for n in walk if (n or {}).get("id")
            ],
        }
        try:
            return self.adapters.write_reverie_page(slug, body, frontmatter)
        except Exception:
            return None
