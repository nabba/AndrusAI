"""
aesthetics/tools.py — CrewAI tools for the aesthetic pattern library.

AestheticSearchTool: Find quality patterns (Writer, Coder, Critic).
FlagAestheticTool: Flag something as aesthetically notable (any agent).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AestheticSearchInput(BaseModel):
    query: str = Field(
        description=(
            "What kind of quality pattern are you looking for? "
            "E.g., 'elegant error handling', 'concise technical prose', "
            "'well-structured philosophical argument'."
        )
    )
    pattern_type: str | None = Field(
        default=None,
        description=(
            "Optional filter: elegant_code, beautiful_prose, "
            "well_structured_argument, creative_solution."
        ),
    )
    n_results: int = Field(
        default=5, description="Number of results (1-10).", ge=1, le=10,
    )


class AestheticSearchTool(BaseTool):
    """Search the aesthetic pattern library for quality examples.

    Use this when you want to see what "good" looks like — elegant code,
    beautiful prose, well-structured arguments.  Helps calibrate quality
    judgment and maintain high standards.
    """

    name: str = "search_aesthetic_patterns"
    description: str = (
        "Search a curated library of aesthetic patterns — examples of "
        "elegant code, beautiful prose, and well-structured arguments "
        "that have been flagged as notably high quality. Use to calibrate "
        "your quality judgment and find inspiration for craftsmanship."
    )
    args_schema: Type[BaseModel] = AestheticSearchInput

    def _run(
        self, query: str, pattern_type: str | None = None, n_results: int = 5,
    ) -> str:
        from app.aesthetics.vectorstore import get_store

        try:
            store = get_store()
        except Exception as e:
            return f"Aesthetic library unavailable: {e}"

        if store._collection.count() == 0:
            return "The aesthetic pattern library is empty — no patterns flagged yet."

        where_filter = None
        if pattern_type:
            where_filter = {"pattern_type": pattern_type}

        try:
            results = store.query_reranked(
                query_text=query, n_results=n_results, where_filter=where_filter,
            )
        except Exception:
            results = store.query(
                query_text=query, n_results=n_results, where_filter=where_filter,
            )

        if not results:
            return f"No aesthetic patterns found for: '{query}'."

        parts = [f"Found {len(results)} aesthetic patterns:\n"]
        for i, r in enumerate(results, 1):
            meta = r["metadata"]
            parts.append(
                f"--- Pattern {i} ({meta.get('pattern_type', '?')}) ---\n"
                f"Domain: {meta.get('domain', '?')} | "
                f"Quality: {meta.get('quality_score', '?')} | "
                f"Flagged by: {meta.get('flagged_by', '?')}\n"
                f"{r['text'][:500]}\n"
            )
        return "\n".join(parts)


class FlagAestheticInput(BaseModel):
    text: str = Field(
        description="The text/code/argument to flag as aesthetically notable."
    )
    pattern_type: str = Field(
        default="creative_solution",
        description=(
            "Type: elegant_code, beautiful_prose, "
            "well_structured_argument, creative_solution."
        ),
    )
    domain: str = Field(
        default="general",
        description="Domain context (e.g., 'python', 'ecology', 'philosophy').",
    )
    quality_score: float = Field(
        default=0.8,
        description="Quality rating 0-1 (1 = exceptional).",
        ge=0, le=1,
    )


class FlagAestheticTool(BaseTool):
    """Flag something as aesthetically notable for the pattern library.

    Any agent can flag — this is how the system develops taste over time.
    """

    name: str = "flag_aesthetic_pattern"
    description: str = (
        "Flag a piece of code, prose, or argument as aesthetically notable. "
        "This adds it to the pattern library so future work can reference "
        "it as a quality benchmark. Use when you encounter something "
        "particularly elegant or well-crafted."
    )
    args_schema: Type[BaseModel] = FlagAestheticInput

    _agent_name: str = "unknown"

    def _run(
        self,
        text: str,
        pattern_type: str = "creative_solution",
        domain: str = "general",
        quality_score: float = 0.8,
    ) -> str:
        from app.aesthetics.vectorstore import get_store

        now = datetime.now(timezone.utc)
        metadata = {
            "pattern_type": pattern_type,
            "domain": domain,
            "flagged_by": self._agent_name,
            "quality_score": str(round(quality_score, 2)),
            "epistemic_status": "evaluative/subjective",
            "created_at": now.isoformat(),
        }

        try:
            store = get_store()
            ok = store.add_pattern(text, metadata)
            if ok:
                return f"Aesthetic pattern flagged ({pattern_type}, {domain})."
            return "Failed to flag pattern."
        except Exception as e:
            return f"Aesthetic library unavailable: {e}"


def get_aesthetic_tools(role: str = "reader") -> list[BaseTool]:
    """Return aesthetic tools for a given role."""
    search = AestheticSearchTool()
    flag = FlagAestheticTool()
    flag._agent_name = role
    return [search, flag]
