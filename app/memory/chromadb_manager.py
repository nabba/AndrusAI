"""
chromadb_manager.py — ChromaDB vector memory with Metal-accelerated embeddings.

Embedding strategy (in priority order):
  1. Ollama nomic-embed-text via Metal GPU (~15ms/call, 768-dim)
  2. Refused — CPU fallback disabled to prevent 384→768 dimension corruption.

ALL embeddings system-wide are pinned to 768-dim (nomic-embed-text).
If Ollama is unreachable, embed() raises EmbeddingUnavailableError.
This protects ChromaDB collections from silent data corruption caused by
mixing 384-dim and 768-dim vectors.

IMPORTANT: Never change _EMBED_DIM without migrating ALL ChromaDB collections
AND all pgvector columns (agent_experiences, workspace_items, beliefs).
"""

import chromadb
import functools
import logging
import os
import threading
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

PERSIST_DIR = Path("/app/workspace/memory")
TEAM_COLLECTION = "team_shared"

# ── Embedding backend selection ──────────────────────────────────────────────

# Ollama URL (from inside Docker: host.docker.internal; native: localhost)
_OLLAMA_URL = os.environ.get(
    "OLLAMA_EMBED_URL",
    os.environ.get("LOCAL_LLM_BASE_URL", "http://host.docker.internal:11434"),
)
_OLLAMA_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
_EMBED_DIM = 768  # IMMUTABLE — pinned to Ollama nomic-embed-text dimension.
                   # All ChromaDB collections + pgvector columns depend on this.
_embed_backend = "unknown"  # "ollama" or "unavailable" (cpu fallback removed)
_backend_lock = threading.Lock()


class EmbeddingUnavailableError(RuntimeError):
    """Raised when Ollama embedding backend is unavailable."""
    pass


def _ollama_embed(text: str) -> list[float] | None:
    """Get embedding from Ollama via Metal GPU. Returns None on failure."""
    try:
        import requests
        resp = requests.post(
            f"{_OLLAMA_URL}/api/embeddings",
            json={"model": _OLLAMA_MODEL, "prompt": text},
            timeout=10,
        )
        if resp.status_code == 200:
            emb = resp.json().get("embedding")
            if emb:
                return emb
        return None
    except Exception:
        return None


def _detect_backend() -> tuple[str, int]:
    """Detect the embedding backend. Only Ollama (768-dim) is supported."""
    global _embed_backend
    emb = _ollama_embed("test")
    if emb:
        _embed_backend = "ollama"
        actual_dim = len(emb)
        if actual_dim != _EMBED_DIM:
            logger.error(
                f"CRITICAL: Ollama {_OLLAMA_MODEL} returned {actual_dim}-dim "
                f"but system is pinned to {_EMBED_DIM}-dim. "
                f"Check OLLAMA_EMBED_MODEL setting."
            )
        logger.info(
            f"Embedding backend: Ollama Metal GPU ({_OLLAMA_MODEL}, "
            f"{_EMBED_DIM}-dim, ~15ms/call)"
        )
        return _embed_backend, _EMBED_DIM
    _embed_backend = "unavailable"
    logger.warning(
        f"Embedding backend: UNAVAILABLE — Ollama not reachable at {_OLLAMA_URL}. "
        f"Store/retrieve operations will skip until Ollama is available."
    )
    return _embed_backend, _EMBED_DIM


def _raw_embed(text: str) -> list[float]:
    """Get 768-dim embedding from Ollama.

    Raises EmbeddingUnavailableError if Ollama is down. No CPU fallback —
    mixing 384-dim and 768-dim embeddings silently corrupts vector stores.
    """
    global _embed_backend
    if _embed_backend == "unknown":
        with _backend_lock:
            if _embed_backend == "unknown":
                _detect_backend()
    if _embed_backend == "unavailable":
        # Retry Ollama — it may have come back
        emb = _ollama_embed(text)
        if emb:
            with _backend_lock:
                _embed_backend = "ollama"
            logger.info("Embedding backend recovered: Ollama available again")
            return emb
        raise EmbeddingUnavailableError(
            "Ollama embedding unavailable — all embeddings are pinned to "
            f"768-dim ({_OLLAMA_MODEL}). No CPU fallback."
        )
    # _embed_backend == "ollama"
    emb = _ollama_embed(text)
    if emb:
        return emb
    # Ollama went down mid-session — refuse to produce wrong-dimension vectors
    raise EmbeddingUnavailableError(
        f"Ollama embedding failed mid-session — refusing to produce "
        f"non-{_EMBED_DIM}-dim vectors"
    )


@functools.lru_cache(maxsize=512)
def _embed_cached(text: str) -> tuple:
    """LRU-cached embedding computation.

    Avoids re-encoding the same text multiple times per request.
    Returns tuple for hashability.
    """
    return tuple(_raw_embed(text))


def embed(text: str) -> list[float]:
    """Get embedding for text, using LRU cache + Metal GPU."""
    return list(_embed_cached(text))


def get_embed_dim() -> int:
    """Return the pinned embedding dimension (768 for Ollama nomic-embed-text)."""
    return _EMBED_DIM


# ── ChromaDB client ──────────────────────────────────────────────────────────

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


# E4: Cache collection objects — avoid get_or_create_collection() per operation.
# Also cache count() to avoid O(n) scan on every retrieve call.
_collections: dict[str, object] = {}
_count_cache: dict[str, int] = {}


def _get_col(name: str):
    """Get a ChromaDB collection, caching the object for reuse.

    If the collection's embedding dimension doesn't match the current model,
    recreate it (operational data is ephemeral — skill files and Mem0 persist).
    """
    if name not in _collections:
        client = get_client()
        col = client.get_or_create_collection(name)
        # Check dimension compatibility if collection has data
        try:
            if col.count() > 0:
                # Peek at one embedding to check dimension — must request include=["embeddings"]
                sample = col.peek(1, include=["embeddings"])
                if sample and sample.get("embeddings") and sample["embeddings"][0]:
                    existing_dim = len(sample["embeddings"][0])
                    current_dim = get_embed_dim()
                    if existing_dim != current_dim:
                        logger.warning(
                            f"ChromaDB: dimension mismatch in '{name}' "
                            f"(stored={existing_dim}, model={current_dim}). Recreating."
                        )
                        try:
                            from app.self_awareness.journal import get_journal, JournalEntry, JournalEntryType
                            get_journal().write(JournalEntry(
                                entry_type=JournalEntryType.ERROR,
                                summary=f"ChromaDB '{name}' recreated: dims {existing_dim}→{current_dim}",
                                outcome="degraded",
                            ))
                        except Exception:
                            pass
                        client.delete_collection(name)
                        col = client.get_or_create_collection(name)
        except Exception as e:
            # If peek fails with dimension error, recreate the collection
            if "dimension" in str(e).lower():
                logger.warning(f"Collection '{name}' dimension error — recreating: {e}")
                try:
                    client.delete_collection(name)
                    col = client.get_or_create_collection(name)
                except Exception:
                    pass
        _collections[name] = col
    return _collections[name]


def _get_count(col, name: str) -> int:
    """Get collection count, using cached value when available."""
    if name not in _count_cache:
        _count_cache[name] = col.count()
    return _count_cache[name]


# ── Store / Retrieve operations ──────────────────────────────────────────────

def store(collection_name: str, text: str, metadata: dict = None):
    # H1: Validate content before storage to prevent memory poisoning attacks.
    try:
        from app.sanitize import validate_content
        if not validate_content(text):
            logger.warning(
                f"Memory store BLOCKED — injection pattern detected in "
                f"collection={collection_name}: {text[:80]!r}"
            )
            return
    except ImportError:
        pass
    col = _get_col(collection_name)
    embedding = embed(text)
    try:
        col.add(
            documents=[text],
            embeddings=[embedding],
            metadatas=[metadata or {}],
            ids=[str(uuid.uuid4())],
        )
    except Exception as e:
        # Dimension mismatch: collection has 384-dim but model produces 768-dim
        # Recreate the collection and retry (operational data is ephemeral)
        if "dimension" in str(e).lower():
            logger.warning(f"Dimension mismatch in '{collection_name}' — recreating and retrying")
            _collections.pop(collection_name, None)
            _count_cache.pop(collection_name, None)
            client = get_client()
            client.delete_collection(collection_name)
            col = client.get_or_create_collection(collection_name)
            _collections[collection_name] = col
            col.add(
                documents=[text],
                embeddings=[embedding],
                metadatas=[metadata or {}],
                ids=[str(uuid.uuid4())],
            )
        else:
            raise
    _count_cache.pop(collection_name, None)


def retrieve(collection_name: str, query: str, n: int = 5) -> list[str]:
    n = min(max(1, n), 50)
    col = _get_col(collection_name)
    cnt = _get_count(col, collection_name)
    if cnt == 0:
        return []
    embedding = embed(query)
    try:
        results = col.query(
            query_embeddings=[embedding], n_results=min(n, cnt)
        )
    except Exception as e:
        if "dimension" in str(e).lower():
            logger.warning(
                f"Dimension mismatch in '{collection_name}' during retrieve — "
                f"recreating collection (old data lost): {e}"
            )
            _collections.pop(collection_name, None)
            _count_cache.pop(collection_name, None)
            client = get_client()
            client.delete_collection(collection_name)
            client.get_or_create_collection(collection_name)
            return []
        raise
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
    """Retrieve documents with their metadata and distances."""
    n = min(max(1, n), 50)
    col = _get_col(collection_name)
    cnt = _get_count(col, collection_name)
    if cnt == 0:
        return []
    embedding = embed(query)
    try:
        results = col.query(
            query_embeddings=[embedding],
            n_results=min(n, cnt),
            include=["documents", "metadatas", "distances"],
        )
    except Exception as e:
        if "dimension" in str(e).lower():
            logger.warning(
                f"Dimension mismatch in '{collection_name}' during retrieve — "
                f"recreating collection (old data lost): {e}"
            )
            _collections.pop(collection_name, None)
            _count_cache.pop(collection_name, None)
            client = get_client()
            client.delete_collection(collection_name)
            client.get_or_create_collection(collection_name)
            return []
        raise
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
    """Retrieve documents filtered by a ChromaDB 'where' clause."""
    n = min(max(1, n), 50)
    col = _get_col(collection_name)
    cnt = _get_count(col, collection_name)
    if cnt == 0:
        return []
    embedding = embed(query)
    try:
        results = col.query(
            query_embeddings=[embedding],
            n_results=min(n, cnt),
            where=where,
        )
        return results["documents"][0] if results["documents"] else []
    except Exception:
        return retrieve(collection_name, query, n)
