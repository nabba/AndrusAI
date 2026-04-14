"""
blend_tool.py — Conceptual Blending Tool (Mechanism 6).

Operationalizes Fauconnier & Turner's Conceptual Blending Theory as a
CrewAI tool. Pulls one concept from the philosophy RAG and one from fiction
inspiration, then asks the agent to blend them per Sato (2025)'s template.

Output carries explicit epistemic tags:
    [PIT] — Prompt-Induced Transition: novel structural connection that
            survives cross-domain scrutiny.
    [PIH] — Prompt-Induced Hallucination: plausible but ungrounded fusion;
            useful for ideation, unsafe as factual claim.

Downstream agents should treat [PIH]-tagged material as ideation fuel only
and never quote it as fact.
"""
from __future__ import annotations

import logging
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ConceptBlendInput(BaseModel):
    """Input schema for ConceptBlendTool."""

    concept_a: str = Field(
        description=(
            "First concept, usually from a philosophical/humanist domain. "
            "Example: 'Aristotelian phronesis', 'Stoic dichotomy of control'."
        )
    )
    concept_b: str = Field(
        description=(
            "Second concept, usually from a fictional, mathematical, natural "
            "or craft domain. Example: 'Penrose tiling', 'kintsugi repair', "
            "'Ursula Le Guin's ansible'."
        )
    )
    n_passages: int = Field(
        default=2,
        description="Passages to retrieve per source (1-5).",
        ge=1,
        le=5,
    )


BLEND_TEMPLATE = """\
# Conceptual Blend Request

You are being asked to perform conceptual blending between two concepts
drawn from different domains. Follow this exact structure in your response.

## Concept A
{concept_a}

### Retrieved passages from philosophy corpus
{philosophy_passages}

## Concept B
{concept_b}

### Retrieved material from fiction/inspiration corpus
{fiction_passages}

## Your task

1. **Structural mapping** — identify the relational skeleton shared by A and B.
   What structure do both exemplify, when surface features are stripped away?

2. **Emergent properties** — describe 1-3 properties that exist in the BLEND
   but in neither input alone. These are the creative payload.

3. **Three novel ideas** — generate three applications or framings that
   exploit the emergent properties. Each must be a concrete proposition,
   not an abstraction.

4. **Epistemic tagging** — label each idea:
   - `[PIT]` if the connection survives scrutiny from within BOTH source
     domains (structurally coherent in each).
   - `[PIH]` if the fusion is generative but cannot be grounded in either
     source — useful for ideation, unsafe as a factual claim.

Return the structured response. Do not summarize. Do not caveat before
completing the four steps.
"""


class ConceptBlendTool(BaseTool):
    """Blend a philosophical concept with a fictional/natural/mathematical one.

    Retrieves grounding passages from both RAG layers and composes a blending
    prompt that the calling agent then executes. The RETRIEVAL happens in the
    tool; the BLENDING happens in the agent's next reasoning step, preserving
    the agent's reasoning-method preamble (Mechanism 1).
    """

    name: str = "conceptual_blend"
    description: str = (
        "Retrieves grounding material for two concepts from different domains "
        "(philosophy + fiction/inspiration) and returns a structured blending "
        "prompt. Use when the task calls for genuine novelty rather than "
        "lookup. Output includes [PIT]/[PIH] epistemic tags that downstream "
        "consumers must preserve."
    )
    args_schema: Type[BaseModel] = ConceptBlendInput

    def _run(
        self,
        concept_a: str,
        concept_b: str,
        n_passages: int = 2,
    ) -> str:
        philosophy_passages = _retrieve_philosophy(concept_a, n_passages)
        fiction_passages = _retrieve_fiction(concept_b, n_passages)
        return BLEND_TEMPLATE.format(
            concept_a=concept_a.strip(),
            concept_b=concept_b.strip(),
            philosophy_passages=philosophy_passages,
            fiction_passages=fiction_passages,
        )


def _retrieve_philosophy(query: str, n: int) -> str:
    """Best-effort retrieval from the philosophy vector store."""
    try:
        from app.philosophy.vectorstore import get_store
        store = get_store()
        if store._collection.count() == 0:
            return "(philosophy corpus empty — use concept A as-stated)"
        results = store.query(query_text=query, n_results=n, where_filter=None)
        if not results:
            return "(no passages retrieved — use concept A as-stated)"
        return "\n---\n".join(
            f"[{r['metadata'].get('author', '?')}, "
            f"{r['metadata'].get('title', r['metadata'].get('source_file', '?'))}]: "
            f"{r['text']}"
            for r in results
        )
    except Exception as exc:
        logger.warning(f"blend_tool: philosophy retrieval failed: {exc}")
        return "(philosophy retrieval failed — use concept A as-stated)"


def _retrieve_fiction(query: str, n: int) -> str:
    """Best-effort retrieval from the fiction inspiration corpus."""
    try:
        from app.fiction_inspiration import search_fiction
    except Exception as exc:
        logger.warning(f"blend_tool: fiction module import failed: {exc}")
        return f"(fiction module unavailable — use concept B as-stated: {query})"

    try:
        result = search_fiction(query, n_results=n)
        if not result or "No " in result[:5]:
            return f"(no fiction passages retrieved — use concept B as-stated: {query})"
        return result
    except Exception as exc:
        logger.warning(f"blend_tool: fiction retrieval failed: {exc}")
        return f"(fiction retrieval failed — use concept B as-stated: {query})"
