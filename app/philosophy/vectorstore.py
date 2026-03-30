"""
Philosophy Vector Store — Dedicated ChromaDB collection for philosophical texts.

Separate from the main knowledge base and operational memory.  Uses the same
Ollama Metal GPU embedding pipeline as the rest of the system for consistency.

Usage:
    store = PhilosophyStore()
    store.add_documents(chunks, metadatas)
    results = store.query("What does Aristotle say about virtue?")
"""

import logging
from pathlib import Path
from typing import Optional

import chromadb

from app.philosophy import config

logger = logging.getLogger(__name__)


class PhilosophyStore:
    """
    Persistent vector store for humanist philosophical texts.

    Key differences from enterprise KB:
    - Separate collection (`philosophy_humanist`)
    - Larger chunks optimized for argumentative coherence
    - Metadata schema: author, tradition, era, title, section
    - Read-heavy, write-rare (ingest via dashboard, query via agents)
    """

    def __init__(
        self,
        persist_dir: str = config.CHROMA_PERSIST_DIR,
        collection_name: str = config.COLLECTION_NAME,
    ):
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection_name

        self._client = chromadb.PersistentClient(path=str(self.persist_dir))

        col = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

        # Handle embedding dimension mismatch (e.g. switching between
        # Ollama nomic-embed-text 768-dim and MiniLM 384-dim fallback).
        from app.memory.chromadb_manager import get_embed_dim
        try:
            if col.count() > 0:
                sample = col.peek(1, include=["embeddings"])
                if sample and sample.get("embeddings") and sample["embeddings"][0]:
                    existing_dim = len(sample["embeddings"][0])
                    current_dim = get_embed_dim()
                    if existing_dim != current_dim:
                        logger.warning(
                            f"PhilosophyStore: dimension mismatch ({existing_dim} vs "
                            f"{current_dim}) — recreating collection (re-ingest needed)"
                        )
                        self._client.delete_collection(self.collection_name)
                        col = self._client.get_or_create_collection(
                            name=self.collection_name,
                            metadata={"hnsw:space": "cosine"},
                        )
        except Exception as e:
            if "dimension" in str(e).lower():
                logger.warning(f"PhilosophyStore: dimension error — recreating: {e}")
                try:
                    self._client.delete_collection(self.collection_name)
                    col = self._client.get_or_create_collection(
                        name=self.collection_name,
                        metadata={"hnsw:space": "cosine"},
                    )
                except Exception:
                    pass

        self._collection = col
        logger.info(
            f"PhilosophyStore initialized: {self._collection.count()} chunks "
            f"in '{self.collection_name}' at '{persist_dir}'"
        )

    def add_documents(
        self,
        chunks: list[str],
        metadatas: list[dict],
        ids: Optional[list[str]] = None,
    ) -> int:
        """Add document chunks with metadata to the philosophy collection.

        Returns number of chunks added.
        """
        if not chunks:
            return 0

        if len(chunks) != len(metadatas):
            raise ValueError(
                f"chunks ({len(chunks)}) and metadatas ({len(metadatas)}) must match"
            )

        if ids is None:
            existing = self._collection.count()
            ids = [f"phil_{existing + i:06d}" for i in range(len(chunks))]

        from app.memory.chromadb_manager import embed

        batch_size = 50
        total_added = 0

        for i in range(0, len(chunks), batch_size):
            batch_chunks = chunks[i:i + batch_size]
            batch_meta = metadatas[i:i + batch_size]
            batch_ids = ids[i:i + batch_size]
            batch_embeddings = [embed(c) for c in batch_chunks]

            self._collection.add(
                documents=batch_chunks,
                metadatas=batch_meta,
                embeddings=batch_embeddings,
                ids=batch_ids,
            )
            total_added += len(batch_chunks)

        logger.info(
            f"Ingested {total_added} chunks. Collection total: {self._collection.count()}"
        )
        return total_added

    def query(
        self,
        query_text: str,
        n_results: int = config.DEFAULT_TOP_K,
        where_filter: Optional[dict] = None,
        min_score: float = config.MIN_RELEVANCE_SCORE,
    ) -> list[dict]:
        """Query the philosophy collection.

        Returns list of result dicts with keys: text, metadata, score, id
        """
        count = self._collection.count()
        if count == 0:
            return []

        from app.memory.chromadb_manager import embed

        query_params = {
            "query_embeddings": [embed(query_text)],
            "n_results": min(n_results, count),
            "include": ["documents", "metadatas", "distances"],
        }
        if where_filter:
            query_params["where"] = where_filter

        try:
            results = self._collection.query(**query_params)
        except Exception as e:
            logger.error(f"Philosophy query failed: {e}")
            return []

        formatted = []
        if results and results["documents"] and results["documents"][0]:
            for i, doc in enumerate(results["documents"][0]):
                distance = results["distances"][0][i] if results["distances"] else 1.0
                score = 1.0 - distance
                if score < min_score:
                    continue

                formatted.append({
                    "text": doc,
                    "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                    "score": round(score, 4),
                    "id": results["ids"][0][i] if results["ids"] else None,
                })

        return formatted

    def remove_by_source(self, source_file: str) -> int:
        """Remove all chunks from a specific source file."""
        try:
            existing = self._collection.get(
                where={"source_file": source_file},
            )
            if existing and existing["ids"]:
                self._collection.delete(ids=existing["ids"])
                count = len(existing["ids"])
                logger.info(f"Removed {count} chunks from '{source_file}'")
                return count
        except Exception as e:
            logger.error(f"Failed to remove '{source_file}': {e}")
        return 0

    def reset_collection(self) -> None:
        """Drop and recreate the collection."""
        logger.warning(f"Resetting philosophy collection '{self.collection_name}'!")
        self._client.delete_collection(self.collection_name)
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def get_stats(self) -> dict:
        """Return collection statistics."""
        count = self._collection.count()

        traditions: set[str] = set()
        authors: set[str] = set()
        titles: set[str] = set()
        source_files: set[str] = set()

        if count > 0:
            # Get all metadata (philosophy KB is small enough)
            all_data = self._collection.get(include=["metadatas"])
            if all_data and all_data["metadatas"]:
                for meta in all_data["metadatas"]:
                    traditions.add(meta.get("tradition", "Unknown"))
                    authors.add(meta.get("author", "Unknown"))
                    titles.add(meta.get("title", "Unknown"))
                    source_files.add(meta.get("source_file", "Unknown"))

        return {
            "collection_name": self.collection_name,
            "total_chunks": count,
            "total_texts": len(source_files - {"Unknown"}),
            "traditions": sorted(traditions - {"Unknown"}),
            "authors": sorted(authors - {"Unknown"}),
            "titles": sorted(titles - {"Unknown"}),
            "persist_dir": str(self.persist_dir),
        }


# ── Singleton accessor ────────────────────────────────────────────────────────
_store: Optional[PhilosophyStore] = None


def get_store() -> PhilosophyStore:
    """Lazy-singleton accessor for the philosophy store."""
    global _store
    if _store is None:
        _store = PhilosophyStore()
    return _store
