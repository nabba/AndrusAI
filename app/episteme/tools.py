"""
episteme/tools.py — CrewAI tools for the research/metacognitive knowledge base.

EpistemeSearchTool: Query research papers, architecture decisions, design patterns.
    Assigned to: Self-Improver (read-only), Commander, Researcher.

Safety: Self-Improver gets search only (no ingest) — cannot modify its
own theoretical grounding.  This preserves the evaluation invariant.
"""

from __future__ import annotations

import logging
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class EpistemeSearchInput(BaseModel):
    query: str = Field(
        description=(
            "A question about research, design patterns, architecture decisions, "
            "or methodology. Be specific — e.g., 'cross-encoder re-ranking patterns', "
            "'MAP-Elites quality-diversity algorithm', 'why did we choose ChromaDB'."
        )
    )
    paper_type: str | None = Field(
        default=None,
        description=(
            "Optional: filter by paper type. Values: research_paper, "
            "architecture_decision, design_pattern, failed_experiment, "
            "methodology, survey."
        ),
    )
    n_results: int = Field(
        default=5,
        description="Number of results to return (1-10).",
        ge=1,
        le=10,
    )


class EpistemeSearchTool(BaseTool):
    """Search the research and metacognitive knowledge base.

    Use this when you need:
    - Theoretical backing for a proposed improvement
    - Design patterns from research literature
    - Understanding of why architectural choices were made
    - Knowledge about failed experiments (avoid repeating them)
    """

    name: str = "search_research_knowledge"
    description: str = (
        "Search a curated knowledge base of research papers, architecture "
        "decisions, design patterns, and experimental results. Returns "
        "relevant passages with author, paper type, and domain metadata. "
        "Use for theoretical grounding and principled decision-making."
    )
    args_schema: Type[BaseModel] = EpistemeSearchInput

    def _run(
        self,
        query: str,
        paper_type: str | None = None,
        n_results: int = 5,
    ) -> str:
        from app.episteme.vectorstore import get_store

        try:
            store = get_store()
        except Exception as e:
            logger.error("Episteme store init failed: %s", e)
            return "Research knowledge base is not available."

        if store._collection.count() == 0:
            return (
                "The research knowledge base is empty. "
                "No research texts have been ingested yet."
            )

        where_filter = None
        if paper_type:
            where_filter = {"paper_type": paper_type}

        try:
            results = store.query_reranked(
                query_text=query,
                n_results=n_results,
                where_filter=where_filter,
            )
        except Exception:
            results = store.query(
                query_text=query,
                n_results=n_results,
                where_filter=where_filter,
            )

        if not results:
            return (
                f"No relevant research found for: '{query}'. "
                f"Try broader terms or removing the paper_type filter."
            )

        passages = []
        for i, result in enumerate(results, 1):
            meta = result["metadata"]
            score = result.get("score", 0)
            passage = (
                f"--- Result {i} (Relevance: {score * 100:.0f}%) ---\n"
                f"Source: {meta.get('author', 'Unknown')} — "
                f"{meta.get('title', meta.get('source_file', 'Unknown'))}\n"
                f"Type: {meta.get('paper_type', 'Unknown')} | "
                f"Domain: {meta.get('domain', 'Unknown')} | "
                f"Status: {meta.get('epistemic_status', 'Unknown')}\n\n"
                f"{result['text']}\n"
            )
            passages.append(passage)

        header = f"Retrieved {len(results)} passages from the Research Knowledge Base.\n"
        return header + "\n".join(passages)


def get_episteme_tools() -> list[BaseTool]:
    """Return episteme tools for CrewAI agents (search only — read-only access)."""
    return [EpistemeSearchTool()]
