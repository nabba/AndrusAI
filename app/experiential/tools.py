"""
experiential/tools.py — CrewAI tools for journal/experiential memory.

JournalSearchTool: Search past experiences (all agents).
JournalWriteTool: Write journal entries (Writer + Commander only).
"""

from __future__ import annotations

import logging
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class JournalSearchInput(BaseModel):
    query: str = Field(
        description=(
            "What experience or reflection are you looking for? "
            "E.g., 'last time we handled a complex writing task', "
            "'errors with the coding crew', 'creative breakthrough moments'."
        )
    )
    entry_type: str | None = Field(
        default=None,
        description=(
            "Optional filter: task_reflection, creative_insight, "
            "error_learning, interaction_narrative, evolution_reflection."
        ),
    )
    n_results: int = Field(
        default=5, description="Number of results (1-10).", ge=1, le=10,
    )


class JournalSearchTool(BaseTool):
    """Search the system's experiential journal for past reflections.

    Use this to learn from past experiences — what worked, what didn't,
    what surprised us, what patterns emerged across tasks.
    """

    name: str = "search_journal"
    description: str = (
        "Search the system's experiential journal — reflected memories of "
        "past tasks, creative insights, error learnings, and interactions. "
        "Unlike operational memory (what happened), journal entries capture "
        "what the experience *meant*. Use to learn from past experiences."
    )
    args_schema: Type[BaseModel] = JournalSearchInput

    def _run(
        self, query: str, entry_type: str | None = None, n_results: int = 5,
    ) -> str:
        from app.experiential.vectorstore import get_store

        try:
            store = get_store()
        except Exception as e:
            return f"Journal unavailable: {e}"

        if store._collection.count() == 0:
            return "The experiential journal is empty — no reflections recorded yet."

        where_filter = None
        if entry_type:
            where_filter = {"entry_type": entry_type}

        try:
            results = store.query_reranked(
                query_text=query, n_results=n_results, where_filter=where_filter,
            )
        except Exception:
            results = store.query(
                query_text=query, n_results=n_results, where_filter=where_filter,
            )

        if not results:
            return f"No relevant journal entries found for: '{query}'."

        parts = [f"Found {len(results)} journal entries:\n"]
        for i, r in enumerate(results, 1):
            meta = r["metadata"]
            parts.append(
                f"--- Entry {i} ({meta.get('entry_type', '?')}, "
                f"{meta.get('emotional_valence', '?')}) ---\n"
                f"Agent: {meta.get('agent', '?')} | "
                f"Date: {meta.get('created_at', '?')[:10]}\n"
                f"{r['text']}\n"
            )
        return "\n".join(parts)


class JournalWriteInput(BaseModel):
    text: str = Field(
        description="The journal entry to write. Be reflective, not just descriptive."
    )
    entry_type: str = Field(
        default="interaction_narrative",
        description=(
            "Type: task_reflection, creative_insight, error_learning, "
            "interaction_narrative, evolution_reflection."
        ),
    )
    emotional_valence: str = Field(
        default="neutral",
        description="Emotional tone: positive, neutral, negative, mixed.",
    )


class JournalWriteTool(BaseTool):
    """Write a reflective journal entry about an experience.

    Only available to Writer and Commander agents.
    """

    name: str = "write_journal_entry"
    description: str = (
        "Write a reflective journal entry about a significant experience, "
        "insight, or learning moment. Journal entries capture not just what "
        "happened but what it meant. Write in first person."
    )
    args_schema: Type[BaseModel] = JournalWriteInput

    # Set by agent setup code to identify the writing agent.
    _agent_name: str = "unknown"

    def _run(
        self,
        text: str,
        entry_type: str = "interaction_narrative",
        emotional_valence: str = "neutral",
    ) -> str:
        from app.experiential.journal_writer import JournalWriter

        writer = JournalWriter()
        ok = writer.write_custom_entry(
            text=text,
            agent=self._agent_name,
            entry_type=entry_type,
            emotional_valence=emotional_valence,
        )
        if ok:
            return "Journal entry recorded."
        return "Failed to write journal entry."


def get_experiential_tools(role: str = "reader") -> list[BaseTool]:
    """Return experiential tools for a given role.

    All agents get search. Writer and Commander also get write.
    """
    tools: list[BaseTool] = [JournalSearchTool()]
    if role in ("writer", "commander"):
        tool = JournalWriteTool()
        tool._agent_name = role
        tools.append(tool)
    return tools
