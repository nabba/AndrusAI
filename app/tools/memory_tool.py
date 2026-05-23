from crewai.tools import BaseTool
from pydantic import Field
from app.memory.chromadb_manager import store, retrieve, store_team, retrieve_team


class MemoryStoreTool(BaseTool):
    name: str = "memory_store"
    description: str = (
        "Store information in team memory. "
        "Args: text (str) - the content to store, "
        "metadata (str) - optional comma-separated key=value pairs."
    )
    collection: str = Field(default="default")

    def _run(self, text: str, metadata: str = "") -> str:
        meta = {}
        if metadata:
            for pair in metadata.split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    meta[k.strip()] = v.strip()
        store(self.collection, text, meta)
        return f"Stored in memory ({self.collection}): {text[:100]}..."


class MemoryRetrieveTool(BaseTool):
    name: str = "memory_retrieve"
    description: str = (
        "Retrieve relevant information from team memory. "
        "Args: query (str) - search query."
    )
    collection: str = Field(default="default")

    def _run(self, query: str, n_results: int = 5) -> str:
        results = retrieve(self.collection, query, n=n_results)
        if not results:
            return "No relevant memories found."
        return "\n\n---\n\n".join(results)


class TeamMemoryStoreTool(BaseTool):
    name: str = "team_memory_store"
    description: str = (
        "Store information in SHARED team memory accessible by ALL agents and crews. "
        "Use this when findings should be visible to other crews working in parallel. "
        "Args: text (str) - the content to store."
    )

    def _run(self, text: str, metadata: str = "") -> str:
        meta = {}
        if metadata:
            for pair in metadata.split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    meta[k.strip()] = v.strip()
        store_team(text, meta)
        return f"Stored in shared team memory: {text[:100]}..."


class TeamMemoryRetrieveTool(BaseTool):
    name: str = "team_memory_retrieve"
    description: str = (
        "Retrieve information from SHARED team memory written by any agent or crew. "
        "Use this to find research or context from other parallel crews. "
        "Args: query (str) - search query."
    )

    def _run(self, query: str, n_results: int = 5) -> str:
        results = retrieve_team(query, n=n_results)
        if not results:
            return "No shared team memories found."
        return "\n\n---\n\n".join(results)


def create_memory_tools(collection: str = "default"):
    """Factory to create memory tools: per-crew pair + shared team pair."""
    return [
        MemoryStoreTool(collection=collection),
        MemoryRetrieveTool(collection=collection),
        TeamMemoryStoreTool(),
        TeamMemoryRetrieveTool(),
    ]


# ── Tool-registry annotations (2026-05-20) ──────────────────────────────
# Discovery-side registration via ``@register_tool``. Passive — the
# legacy ``create_memory_tools`` factory above continues to be used by
# every existing call site. These decorators only surface the tools in
# the registry index so ``tool_search`` and the React tool catalog can
# find them by capability tag.
#
# Lifecycle:
#   * memory_store / memory_retrieve   — SINGLETON (default collection)
#   * team_memory_store / team_memory_retrieve — SINGLETON (no per-agent
#     state; team store is shared by all agents)
#
# Wrapped in try/except ImportError because some stripped-down test
# contexts skip tool_registry import — matches the file_manager pattern.

try:
    from app.tool_registry import register_tool, Tier, Lifecycle

    @register_tool(
        name="memory_store",
        capabilities=["writes-agent-memory"],
        description=(
            "Store information in the agent's per-collection memory store. "
            "Args: text (str) — the content to store; metadata (str) — "
            "optional comma-separated key=value pairs. Persisted in the "
            "ChromaDB-backed mem0 collection named at construction time."
        ),
        tier=Tier.PRODUCTION,
        lifecycle=Lifecycle.SINGLETON,
    )
    def _memory_store_registry_factory():
        return MemoryStoreTool()

    @register_tool(
        name="memory_retrieve",
        capabilities=["reads-agent-memory"],
        description=(
            "Retrieve relevant information from the agent's per-collection "
            "memory store. Args: query (str) — search query. Returns "
            "top-N semantically similar entries."
        ),
        tier=Tier.PRODUCTION,
        lifecycle=Lifecycle.SINGLETON,
    )
    def _memory_retrieve_registry_factory():
        return MemoryRetrieveTool()

    @register_tool(
        name="team_memory_store",
        capabilities=["writes-team-belief"],
        description=(
            "Store information in SHARED team memory accessible by ALL "
            "agents and crews. Use this when findings should be visible "
            "to other crews working in parallel. Args: text (str) — the "
            "content to store; metadata (str) — optional comma-separated "
            "key=value pairs."
        ),
        tier=Tier.PRODUCTION,
        lifecycle=Lifecycle.SINGLETON,
    )
    def _team_memory_store_registry_factory():
        return TeamMemoryStoreTool()

    @register_tool(
        name="team_memory_retrieve",
        capabilities=["reads-team-belief"],
        description=(
            "Retrieve information from SHARED team memory written by any "
            "agent or crew. Use this to find research or context from "
            "other parallel crews. Args: query (str) — search query."
        ),
        tier=Tier.PRODUCTION,
        lifecycle=Lifecycle.SINGLETON,
    )
    def _team_memory_retrieve_registry_factory():
        return TeamMemoryRetrieveTool()

except ImportError:
    # tool_registry not importable in stripped-down test contexts —
    # legacy ``create_memory_tools`` continues to work.
    pass
