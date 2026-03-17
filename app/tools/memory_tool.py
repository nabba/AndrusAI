from crewai.tools import tool
from app.memory.chromadb_manager import store, retrieve


class MemoryTool:
    """ChromaDB-backed memory tool for a specific agent collection."""

    def __init__(self, collection: str = "default"):
        self.collection = collection

    @tool("memory_store")
    def memory_store(self, text: str, metadata: str = "") -> str:
        """
        Store information in team memory.
        text: the content to store
        metadata: optional comma-separated key=value pairs (e.g., 'source=web,topic=AI')
        """
        meta = {}
        if metadata:
            for pair in metadata.split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    meta[k.strip()] = v.strip()
        store(self.collection, text, meta)
        return f"Stored in memory ({self.collection}): {text[:100]}..."

    @tool("memory_retrieve")
    def memory_retrieve(self, query: str, n_results: int = 5) -> str:
        """
        Retrieve relevant information from team memory.
        query: search query
        n_results: number of results to return (default 5)
        """
        results = retrieve(self.collection, query, n=n_results)
        if not results:
            return "No relevant memories found."
        return "\n\n---\n\n".join(results)
