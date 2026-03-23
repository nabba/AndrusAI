import chromadb
import functools
import threading
import uuid
from sentence_transformers import SentenceTransformer
from pathlib import Path

PERSIST_DIR = Path("/app/workspace/memory")
TEAM_COLLECTION = "team_shared"

_model = SentenceTransformer("all-MiniLM-L6-v2")  # Runs locally, no API


@functools.lru_cache(maxsize=512)
def _embed_cached(text: str) -> tuple:
    """LRU-cached embedding computation.

    Avoids re-encoding the same text multiple times per request
    (e.g., result_cache lookup + context fetch + cache store all
    embedding the same task string).  Returns tuple for hashability.
    """
    return tuple(_model.encode(text).tolist())


def embed(text: str) -> list[float]:
    """Get embedding for text, using LRU cache."""
    return list(_embed_cached(text))

# Thread-safe singleton — prevents lock contention when multiple threads
# each try to create their own PersistentClient pointing to the same dir.
_client = None
_client_lock = threading.Lock()


def get_client():
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            _client = chromadb.PersistentClient(path=str(PERSIST_DIR))
    return _client


def store(collection_name: str, text: str, metadata: dict = None):
    # H1: Validate content before storage to prevent memory poisoning attacks.
    # Injection patterns in stored memory can affect all future agent queries.
    try:
        from app.sanitize import validate_content
        if not validate_content(text):
            import logging
            logging.getLogger(__name__).warning(
                f"Memory store BLOCKED — injection pattern detected in "
                f"collection={collection_name}: {text[:80]!r}"
            )
            return  # reject poisoned content silently
    except ImportError:
        pass  # sanitize not available in test environment
    client = get_client()
    col = client.get_or_create_collection(collection_name)
    embedding = embed(text)
    col.add(
        documents=[text],
        embeddings=[embedding],
        metadatas=[metadata or {}],
        ids=[str(uuid.uuid4())],
    )


def retrieve(collection_name: str, query: str, n: int = 5) -> list[str]:
    n = min(max(1, n), 50)  # cap between 1 and 50 results
    client = get_client()
    col = client.get_or_create_collection(collection_name)
    if col.count() == 0:
        return []
    embedding = embed(query)
    results = col.query(
        query_embeddings=[embedding], n_results=min(n, col.count())
    )
    return results["documents"][0]


def store_team(text: str, metadata: dict = None):
    """Store in the shared team-wide collection (cross-crew sharing)."""
    store(TEAM_COLLECTION, text, metadata)


def retrieve_team(query: str, n: int = 5) -> list[str]:
    """Retrieve from the shared team-wide collection."""
    return retrieve(TEAM_COLLECTION, query, n)


def retrieve_with_metadata(
    collection_name: str, query: str, n: int = 5
) -> list[dict]:
    """Retrieve documents with their metadata and distances.

    Returns list of {"document": str, "metadata": dict, "distance": float}.
    """
    n = min(max(1, n), 50)
    client = get_client()
    col = client.get_or_create_collection(collection_name)
    if col.count() == 0:
        return []
    embedding = _model.encode(query).tolist()
    results = col.query(
        query_embeddings=[embedding],
        n_results=min(n, col.count()),
        include=["documents", "metadatas", "distances"],
    )
    items = []
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]
    for doc, meta, dist in zip(docs, metas, dists):
        items.append({"document": doc, "metadata": meta or {}, "distance": dist})
    return items


def retrieve_filtered(
    collection_name: str, query: str, where: dict, n: int = 5
) -> list[str]:
    """Retrieve documents filtered by a ChromaDB 'where' clause.

    Example: retrieve_filtered("scope_policies", "research", {"importance": "high"})
    """
    n = min(max(1, n), 50)
    client = get_client()
    col = client.get_or_create_collection(collection_name)
    if col.count() == 0:
        return []
    embedding = _model.encode(query).tolist()
    try:
        results = col.query(
            query_embeddings=[embedding],
            n_results=min(n, col.count()),
            where=where,
        )
        return results["documents"][0] if results["documents"] else []
    except Exception:
        # Fallback: if where clause fails (e.g., no matching metadata keys),
        # return unfiltered results
        return retrieve(collection_name, query, n)
