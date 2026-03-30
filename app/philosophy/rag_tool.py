"""
Philosophy RAG Tool for CrewAI
================================
A CrewAI-compatible tool that queries the philosophy vector store.
Assign ONLY to agents that need philosophical grounding (Writer, Critic,
Self-Improver).

Safety: This tool is READ-ONLY.  No agent can modify the philosophy corpus.
"""

import logging
from typing import Optional, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class PhilosophyRAGInput(BaseModel):
    """Input schema for the PhilosophyRAGTool."""

    query: str = Field(
        description=(
            "A natural language question about philosophy, ethics, or humanist principles. "
            "Be specific about the concept or thinker you need. "
            "Examples: 'What does Aristotle say about practical wisdom?', "
            "'How do Stoics approach emotional regulation?', "
            "'What are Kant\\'s arguments for human dignity?'"
        )
    )
    tradition: Optional[str] = Field(
        default=None,
        description=(
            "Optional: filter by philosophical tradition. "
            "Values: Stoicism, Existentialism, Pragmatism, Humanism, "
            "Enlightenment, Confucianism, Phenomenology, Virtue Ethics, etc."
        ),
    )
    n_results: int = Field(
        default=5,
        description="Number of relevant passages to retrieve (1-10).",
        ge=1,
        le=10,
    )


class PhilosophyRAGTool(BaseTool):
    """
    Retrieves relevant passages from the humanist philosophical knowledge base.

    Use this tool when you need to:
    - Ground a response in philosophical principles
    - Reference a specific thinker's arguments
    - Find ethical frameworks relevant to a decision
    - Support reasoning with humanist tradition

    Do NOT use this tool for:
    - General factual lookups (use web_search instead)
    - Technical documentation (use knowledge_search instead)
    - Operational or business questions
    """

    name: str = "philosophy_knowledge_base"
    description: str = (
        "Search a curated knowledge base of humanist philosophical texts. "
        "Returns relevant passages from thinkers like Aristotle, Seneca, Kant, "
        "Mill, Arendt, and others. Use when you need to ground reasoning in "
        "philosophical principles or reference specific arguments. "
        "Supports filtering by philosophical tradition."
    )
    args_schema: Type[BaseModel] = PhilosophyRAGInput

    def _run(
        self,
        query: str,
        tradition: Optional[str] = None,
        n_results: int = 5,
    ) -> str:
        """Execute a retrieval query against the philosophy knowledge base."""
        from app.philosophy.vectorstore import get_store

        try:
            store = get_store()
        except Exception as e:
            logger.error(f"Philosophy store init failed: {e}")
            return "Philosophy knowledge base is not available."

        if store._collection.count() == 0:
            return (
                "The philosophy knowledge base is empty. "
                "No philosophical texts have been ingested yet."
            )

        where_filter = None
        if tradition:
            where_filter = {"tradition": tradition}

        results = store.query(
            query_text=query,
            n_results=n_results,
            where_filter=where_filter,
        )

        if not results:
            return (
                f"No relevant philosophical passages found for: '{query}'. "
                f"Try broadening your query or removing the tradition filter."
            )

        # Format results for the agent
        passages = []
        for i, result in enumerate(results, 1):
            meta = result["metadata"]
            score = result.get("score", 0)
            relevance = f"{score * 100:.0f}%"

            passage = (
                f"--- Passage {i} (Relevance: {relevance}) ---\n"
                f"Source: {meta.get('author', 'Unknown')} — "
                f"{meta.get('title', meta.get('source_file', 'Unknown'))}\n"
                f"Tradition: {meta.get('tradition', 'Unknown')} | "
                f"Era: {meta.get('era', 'Unknown')}\n"
                f"Section: {meta.get('section', 'N/A')}\n\n"
                f"{result['text']}\n"
            )
            passages.append(passage)

        header = (
            f"Retrieved {len(results)} passages from the Philosophy Knowledge Base.\n"
            f"Query: '{query}'"
        )
        if tradition:
            header += f" | Tradition filter: {tradition}"
        header += "\n\n"

        return header + "\n".join(passages)
