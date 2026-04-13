"""Understanding Layer orchestration (Proposal 2 §2.2).

A pass over a single wiki page that:
  1. Extracts a 2–3-level causal chain via Tier-2 LLM
  2. Mines 2–3 non-obvious implications
  3. Detects structural analogies against semantically similar pages
  4. Registers deep questions (conceptual, not informational)

Outputs an UnderstandingDepth that the Wonder Register consumes.
Side-effects (wiki frontmatter update, Neo4j relation writes,
re-embedding) are delegated to adapters so the orchestration stays
testable.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from app.subia.wonder.detector import UnderstandingDepth

logger = logging.getLogger(__name__)


@dataclass
class UnderstandingAdapters:
    """Pluggable side-effect bindings."""
    read_wiki_page: Callable[[str], dict]                          # path → {body, frontmatter}
    raw_chunks_for: Callable[[str], list]                          # query → [chunk]
    similar_pages: Callable[[str], list]                           # causal-chain text → [page_id]
    neo4j_traverse: Callable[[list, int], list]                    # (entities, hops) → [edge]
    llm_causal_chain: Callable[[str], dict]                        # body → {chain, levels, root}
    llm_implications: Callable[[str, dict], list]                  # (body, chain) → [implication]
    llm_analogy: Callable[[dict, dict], Optional[str]]             # (chain_a, chain_b) → name | None
    write_wiki_update: Callable[[str, str, dict], None]            # (path, why_section, frontmatter) → None
    write_neo4j_relation: Callable[[str, str, str, dict], None] = lambda *a, **k: None


@dataclass
class UnderstandingPassResult:
    page_path: str
    depth: UnderstandingDepth
    why_section_text: str
    implications: list
    analogies_found: list
    deep_questions: list
    tokens_spent: int


class UnderstandingPassRunner:
    def __init__(self, adapters: UnderstandingAdapters) -> None:
        self.adapters = adapters

    def run_pass(self, page_path: str) -> UnderstandingPassResult:
        tokens = 0
        try:
            page = self.adapters.read_wiki_page(page_path) or {}
        except Exception:
            page = {}
        body = page.get("body", "")
        if not body:
            return UnderstandingPassResult(
                page_path=page_path,
                depth=UnderstandingDepth(),
                why_section_text="",
                implications=[],
                analogies_found=[],
                deep_questions=[],
                tokens_spent=0,
            )

        # 1. Causal chain
        try:
            chain = self.adapters.llm_causal_chain(body) or {}
        except Exception:
            chain = {}
        levels = int(chain.get("levels", 0))
        tokens += 800

        # 2. Implications
        try:
            implications = self.adapters.llm_implications(body, chain) or []
        except Exception:
            implications = []
        tokens += 500

        # 3. Structural analogies
        analogies = []
        try:
            similars = self.adapters.similar_pages(
                chain.get("text", body[:500])
            ) or []
            for sim in similars[:3]:
                sim_body = (
                    self.adapters.read_wiki_page(sim).get("body", "")
                    if isinstance(sim, str) else ""
                )
                if not sim_body:
                    continue
                sim_chain = self.adapters.llm_causal_chain(sim_body) or {}
                name = self.adapters.llm_analogy(chain, sim_chain)
                if name:
                    analogies.append({"with": sim, "pattern": name})
                    tokens += 300
        except Exception:
            pass

        # 4. Deep questions = items in chain marked unresolved
        deep_questions = list(chain.get("open_questions", []))[:5]

        depth = UnderstandingDepth(
            causal_levels=levels,
            cross_references=len(page.get("frontmatter", {}).get("related_pages", [])),
            implications_generated=len(implications),
            structural_analogies=len(analogies),
            deep_questions=len(deep_questions),
            cross_domain_contradictions=int(chain.get("contradictions", 0)),
            recursive_structure_detected=bool(chain.get("recursive", False)),
            epistemic_statuses=list(
                page.get("frontmatter", {}).get("epistemic_status_set", [])
            ) or [page.get("frontmatter", {}).get("epistemic_status", "factual")],
        )

        # Side-effects: write back to wiki + Neo4j
        why_text = chain.get("text", "")
        try:
            from datetime import datetime, timezone
            self.adapters.write_wiki_update(
                page_path,
                why_text,
                {
                    "understanding_depth": {
                        "causal_levels": depth.causal_levels,
                        "implications_generated": depth.implications_generated,
                        "structural_analogies": depth.structural_analogies,
                        "deep_questions": depth.deep_questions,
                        "last_understanding_pass": datetime.now(timezone.utc).isoformat(),
                    }
                },
            )
        except Exception:
            pass
        for an in analogies:
            try:
                self.adapters.write_neo4j_relation(
                    page_path, an["with"], "STRUCTURAL_ANALOGY",
                    {"pattern": an["pattern"], "discovered_by": "understanding-layer"},
                )
            except Exception:
                pass

        return UnderstandingPassResult(
            page_path=page_path,
            depth=depth,
            why_section_text=why_text,
            implications=implications,
            analogies_found=analogies,
            deep_questions=deep_questions,
            tokens_spent=tokens,
        )
