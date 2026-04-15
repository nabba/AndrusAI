"""
tensions/tools.py — CrewAI tools for the contradictions/tensions KB.

TensionSearchTool: Find unresolved tensions (Self-Improver, Commander, Critic).
RecordTensionTool: Record a noticed contradiction (any agent).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TensionSearchInput(BaseModel):
    query: str = Field(
        description=(
            "What kind of tension or contradiction are you looking for? "
            "E.g., 'efficiency vs thoroughness', 'safety vs autonomy', "
            "'creativity vs consistency'."
        )
    )
    unresolved_only: bool = Field(
        default=True,
        description="If true, only return unresolved tensions.",
    )
    n_results: int = Field(
        default=5, description="Number of results (1-10).", ge=1, le=10,
    )


class TensionSearchTool(BaseTool):
    """Search for unresolved tensions and contradictions.

    Tensions are growth edges — places where the system hasn't yet
    figured out how to reconcile competing demands.  Use this to find
    areas ripe for improvement or creative resolution.
    """

    name: str = "search_tensions"
    description: str = (
        "Search the tensions knowledge base for unresolved contradictions, "
        "competing principles, and open questions. Tensions are growth edges — "
        "use them to identify areas for improvement or creative synthesis."
    )
    args_schema: Type[BaseModel] = TensionSearchInput

    def _run(
        self, query: str, unresolved_only: bool = True, n_results: int = 5,
    ) -> str:
        from app.tensions.vectorstore import get_store

        try:
            store = get_store()
        except Exception as e:
            return f"Tensions KB unavailable: {e}"

        if store._collection.count() == 0:
            return "No tensions recorded yet — the system hasn't detected contradictions."

        where_filter = None
        if unresolved_only:
            where_filter = {"resolution_status": "unresolved"}

        try:
            results = store.query_reranked(
                query_text=query, n_results=n_results, where_filter=where_filter,
            )
        except Exception:
            results = store.query(
                query_text=query, n_results=n_results, where_filter=where_filter,
            )

        if not results:
            return f"No tensions found for: '{query}'."

        parts = [f"Found {len(results)} tensions:\n"]
        for i, r in enumerate(results, 1):
            meta = r["metadata"]
            parts.append(
                f"--- Tension {i} ({meta.get('tension_type', '?')}) ---\n"
                f"Status: {meta.get('resolution_status', '?')} | "
                f"Detected by: {meta.get('detected_by', '?')}\n"
                f"Pole A: {meta.get('pole_a', '?')}\n"
                f"Pole B: {meta.get('pole_b', '?')}\n"
                f"{r['text'][:400]}\n"
            )
        return "\n".join(parts)


class RecordTensionInput(BaseModel):
    pole_a: str = Field(description="One side of the tension/contradiction.")
    pole_b: str = Field(description="The other side of the tension/contradiction.")
    tension_type: str = Field(
        default="unresolved_question",
        description=(
            "Type: principle_conflict, philosophy_vs_experience, "
            "competing_values, unresolved_question."
        ),
    )
    context: str = Field(
        default="",
        description="What task or situation revealed this tension?",
    )


class RecordTensionTool(BaseTool):
    """Record a noticed contradiction or tension for future reflection.

    Any agent can record tensions — this is how the system identifies
    growth edges.
    """

    name: str = "record_tension"
    description: str = (
        "Record an unresolved tension or contradiction you've noticed. "
        "This feeds the system's growth engine — tensions are revisited "
        "periodically to see if new understanding enables resolution."
    )
    args_schema: Type[BaseModel] = RecordTensionInput

    _agent_name: str = "unknown"

    def _run(
        self,
        pole_a: str,
        pole_b: str,
        tension_type: str = "unresolved_question",
        context: str = "",
    ) -> str:
        now = datetime.now(timezone.utc)
        text = (
            f"Tension between: {pole_a}\n"
            f"And: {pole_b}\n"
            f"Context: {context}" if context else
            f"Tension between: {pole_a}\nAnd: {pole_b}"
        )

        metadata = {
            "tension_type": tension_type,
            "pole_a": pole_a[:200],
            "pole_b": pole_b[:200],
            "detected_by": self._agent_name,
            "context": context[:200],
            "resolution_status": "unresolved",
            "epistemic_status": "unresolved/dialectical",
            "created_at": now.isoformat(),
        }

        try:
            from app.tensions.vectorstore import get_store
            store = get_store()
            tension_id = f"ten_{now.strftime('%Y%m%d_%H%M%S')}_{self._agent_name}"
            ok = store.add_tension(text, metadata, tension_id)
            if ok:
                return "Tension recorded for future reflection."
            return "Failed to record tension."
        except Exception as e:
            return f"Tensions KB unavailable: {e}"


def get_tension_tools(role: str = "reader") -> list[BaseTool]:
    """Return tension tools for a given role."""
    search = TensionSearchTool()
    record = RecordTensionTool()
    record._agent_name = role
    return [search, record]
