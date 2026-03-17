from crewai.tools import BaseTool
from pydantic import Field
from app.memory.chromadb_manager import store, retrieve


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


def create_memory_tools(collection: str = "default"):
    """Factory to create a pair of memory tools for a given collection."""
    return [
        MemoryStoreTool(collection=collection),
        MemoryRetrieveTool(collection=collection),
    ]
