"""
session_search_tool.py — FTS5 full-text search over conversation history.

Registered via the tool plugin registry in base_crew.py.
Backed by app.conversation_store.search_messages().
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def create_session_search_tools() -> list:
    """Build CrewAI BaseTool instances for session search. Returns [] if FTS5 unavailable."""
    try:
        from crewai.tools import BaseTool
        from pydantic import BaseModel, Field
    except ImportError:
        return []

    class _SearchInput(BaseModel):
        query: str = Field(description="Full-text search query across past conversation messages")
        limit: int = Field(default=10, description="Maximum number of results (default 10, max 50)")

    class SessionSearchTool(BaseTool):
        name: str = "session_search"
        description: str = (
            "Search past Signal conversations using full-text search (FTS5). "
            "Returns role, snippet with matches highlighted as >>>match<<<, and timestamp."
        )
        args_schema: type = _SearchInput

        def _run(self, query: str, limit: int = 10) -> str:
            try:
                from app.conversation_store import search_messages
            except Exception:
                return "Session search not available."
            try:
                n = int(limit)
            except (TypeError, ValueError):
                n = 10
            # Clamp to [1, 50] — respect caller's 0 as "minimum allowed"
            limit = max(1, min(n if n > 0 else 1, 50))
            results = search_messages(query, limit=limit)
            if not results:
                return f"No past messages matched: {query!r}"
            lines = [f"Found {len(results)} matches for {query!r}:"]
            for r in results:
                lines.append(
                    f"- [{r['ts'][:19]}] ({r['role']}): {r['content_snippet']}"
                )
            return "\n".join(lines)

    return [SessionSearchTool()]
