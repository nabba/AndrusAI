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
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Stage 2: shared httpx client for Ollama embeddings ──────────────────────
# Module-level Client with connection pooling + keepalive. Saves 3-8ms per
# embed by reusing the TCP/TLS connection across the hundreds of embed calls
# per user request. Lazy-init so imports don't fail if httpx isn't installed.
_ollama_http_client = None
_ollama_http_lock = threading.Lock()


def _get_ollama_http():
    global _ollama_http_client
    if _ollama_http_client is not None:
        return _ollama_http_client
    with _ollama_http_lock:
        if _ollama_http_client is not None:
            return _ollama_http_client
        try:
            import httpx
            _ollama_http_client = httpx.Client(
                timeout=10.0,
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=40),
            )
        except ImportError:
            _ollama_http_client = False  # sentinel: httpx unavailable
        return _ollama_http_client

# Physical chroma data dir for the "memory" KB. Routed through
# app.paths.chroma_kb_dir so the derived chromadb files can live on a named
# volume (CHROMA_DATA_ROOT) while ledgers/snapshots stay on the workspace
# bind mount. Falls back to the legacy literal if app.paths is unavailable
# (matches the defensive import style used in get_kb_client below).
try:
    from app.paths import chroma_kb_dir as _chroma_kb_dir
    PERSIST_DIR = _chroma_kb_dir("memory")
except Exception:
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
    """Get embedding from Ollama via Metal GPU. Returns None on failure.

    Uses a shared pooled httpx.Client for TCP keepalive (~3-8ms/call saved).
    Falls back to the legacy `requests.post` path if httpx is unavailable.
    """
    client = _get_ollama_http()
    try:
        if client and client is not False:
            resp = client.post(
                f"{_OLLAMA_URL}/api/embeddings",
                json={"model": _OLLAMA_MODEL, "prompt": text},
            )
            if resp.status_code == 200:
                emb = resp.json().get("embedding")
                if emb:
                    return emb
            return None
        # Fallback: httpx not installed
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
    # Empty body — usually Ollama briefly unavailable while swapping a model
    # in/out under VRAM pressure. One retry with 1.5s backoff covers the
    # typical swap window without paying the cost on the happy path.
    time.sleep(1.5)
    emb = _ollama_embed(text)
    if emb:
        return emb
    raise EmbeddingUnavailableError(
        f"Ollama returned empty body for {_OLLAMA_MODEL} (2 attempts, 1.5s "
        f"backoff). Refusing to substitute — {_EMBED_DIM}-dim invariant "
        f"requires Ollama to respond."
    )


@functools.lru_cache(maxsize=4096)
def _embed_cached(text: str) -> tuple:
    """LRU-cached embedding computation (L1, in-proc).

    Avoids re-encoding the same text multiple times per request. Size bumped
    from 512 → 4096 in Stage 3 since sentience runs many embeds per request.
    Returns tuple for hashability.
    """
    # L2: check disk cache first — survives container restart.
    try:
        from app.memory import disk_cache as _dc
        cached = _dc.embed_get(text)
        if cached is not None and len(cached) == _EMBED_DIM:
            return tuple(cached)
    except Exception:
        pass

    vec = _raw_embed(text)
    # Write-through to L2 (fire-and-forget).
    try:
        from app.memory import disk_cache as _dc
        _dc.embed_put(text, list(vec))
    except Exception:
        pass
    return tuple(vec)


def embed(text: str) -> list[float]:
    """Get embedding for text, using LRU cache + Metal GPU."""
    return list(_embed_cached(text))


def get_embed_dim() -> int:
    """Return the pinned embedding dimension (768 for Ollama nomic-embed-text)."""
    return _EMBED_DIM


# ── ChromaDB client ──────────────────────────────────────────────────────────

_client = None
_client_lock = threading.Lock()

# Q3.1 (2026-05-11) — KB-rooted client registry. The default ``get_client()``
# points at PERSIST_DIR (the ``memory`` KB). Knowledge bases other than
# ``memory`` (philosophy, episteme, knowledge, experiential, tensions,
# aesthetics, …) live under their own workspace subdirectories. Callers
# that need to operate on those KBs — embedding-migration dual-write,
# cutover, the chromadb_rebuild CLI — use ``get_kb_client(kb_name)`` so
# they reach the right persist dir instead of silently writing into
# ``workspace/memory``.
_kb_clients: dict[str, object] = {}


def _guard_worker() -> None:
    """Fail-closed: the idle WORKER process must NEVER open ChromaDB.

    ChromaDB embedded is single-writer; a second writer corrupts the KBs
    (§55). In the serving/compute split (``IDLE_SCHEDULER_ROLE=worker``) heavy
    idle jobs run in a separate process — they route KB writes through the
    source ledger (the gateway reconciles) and reads via the gateway RAG API,
    never opening ChromaDB here. Raising turns a misclassified worker job into a
    loud, SAFE failure instead of silent corruption. The gateway
    (role=all/gateway, the default) is unaffected.
    """
    import os
    if os.environ.get("IDLE_SCHEDULER_ROLE", "all").strip().lower() == "worker":
        raise RuntimeError(
            "ChromaDB access forbidden in the idle worker process "
            "(single-writer safety, §55). Route writes via the source ledger "
            "and reads via the gateway RAG API."
        )


def _worker_proxy_or_none(kb: str):
    """In the idle WORKER process, return a read+write proxy bound to ``kb``
    instead of opening ChromaDB (§55): reads route to the gateway RAG API,
    writes go ledger-first to the source ledger (the gateway reconciles). On the
    gateway (role=all/gateway, the default) returns None so the caller opens
    ChromaDB normally. Supersedes _guard_worker's raise for these 3 accessors."""
    import os
    if os.environ.get("IDLE_SCHEDULER_ROLE", "all").strip().lower() != "worker":
        return None
    from app.memory import kb_proxy
    return kb_proxy.proxy_client_for_kb(kb)


def get_client():
    _p = _worker_proxy_or_none("memory")
    if _p is not None:
        return _p
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            _client = chromadb.PersistentClient(path=str(PERSIST_DIR))
    return _client


def get_kb_client(kb_name: str):
    """Return the ChromaDB client rooted at ``workspace/<kb_name>``.

    For ``kb_name == "memory"`` (the legacy default), this is the same
    singleton ``get_client()`` returns. For other KBs (philosophy /
    episteme / knowledge / experiential / tensions / aesthetics), this
    opens a separate PersistentClient and caches it.

    All clients live until process exit; cache is process-local.
    """
    _p = _worker_proxy_or_none((kb_name or "memory").strip() or "memory")
    if _p is not None:
        return _p
    name = (kb_name or "").strip()
    if not name or name == "memory":
        return get_client()
    cached = _kb_clients.get(name)
    if cached is not None:
        return cached
    with _client_lock:
        cached = _kb_clients.get(name)
        if cached is not None:
            return cached
        try:
            from app.paths import chroma_kb_dir
            persist_dir = chroma_kb_dir(name)
        except Exception:
            persist_dir = Path("/app/workspace") / name
        client = chromadb.PersistentClient(path=str(persist_dir))
        _kb_clients[name] = client
        return client


# Path-keyed client cache (2026-06-01). The per-KB vectorstores
# (KnowledgeStore / EpistemeStore / …) each live at their own
# ``workspace/<kb>`` directory, so they can't share the ``memory`` singleton.
# But ChromaDB 1.5.x is RUST-backed: every PersistentClient spawns a tokio
# runtime (~2x cores worker threads) + an sqlx sqlite pool. KnowledgeStore()
# is built at 14 hot-path call sites with NO singleton, so opening a fresh
# client per instance leaked those Rust runtimes (threads + memory) until the
# 8 GB cgroup OOM. Caching by resolved path makes every store reuse ONE client
# per KB. Recycle-aware: ``recycle_client`` clears this cache too.
_clients_by_path: dict[str, object] = {}


def get_client_for_path(persist_dir) -> object:
    """Return a process-cached ``PersistentClient`` for a KB directory.

    Use this instead of ``chromadb.PersistentClient(path=...)`` anywhere a
    long-lived store opens its own KB directory, so repeated instantiation
    reuses one Rust runtime instead of leaking one per call.
    """
    _p = _worker_proxy_or_none(Path(persist_dir).resolve().name)
    if _p is not None:
        return _p
    key = str(Path(persist_dir).resolve())
    cached = _clients_by_path.get(key)
    if cached is not None:
        return cached
    with _client_lock:
        cached = _clients_by_path.get(key)
        if cached is not None:
            return cached
        client = chromadb.PersistentClient(path=key)
        _clients_by_path[key] = client
        return client


# E4: Cache collection objects — avoid get_or_create_collection() per operation.
# Also cache count() to avoid O(n) scan on every retrieve call.
_collections: dict[str, object] = {}
_count_cache: dict[str, int] = {}


def recycle_client(kb_name: str | None = None) -> dict:
    """Drop the cached ``PersistentClient`` for ``kb_name`` so the next
    ``get_client()`` / ``get_kb_client()`` opens a fresh one.

    The 2026-05-22 wedge incident showed that the long-running in-process
    client can get stuck returning ``SQLite code 26 / "file is not a
    database"`` on ``get_or_create_collection`` even while ``list_collections``
    works and the on-disk file passes ``PRAGMA integrity_check``. A fresh
    ``PersistentClient`` on the same file recovers. This function is the
    in-process recovery action; the caller decides when to invoke it
    (see ``source_ledger_daemon``).

    Calls ``client.close()`` first when the chromadb version exposes it
    (1.5.x does). Per chromadb's own docs, ``close()`` decrements the
    System reference count and is "particularly important for
    PersistentClient to avoid SQLite file locking issues" — i.e. the
    exact failure mode this recycle is recovering from. If ``close()``
    raises or is absent we still drop the cached reference so a fresh
    client is created on next access.

    Args:
        kb_name: ``None`` / ``""`` / ``"memory"`` recycles the default
            client and clears the collection/count caches (those are
            scoped to the default client only). Any other value recycles
            just that entry in ``_kb_clients``.

    Returns ``{"recycled": str, "collections_cleared": int, "closed": bool}``.
    ``closed`` is True when ``client.close()`` ran cleanly, False when
    the method was missing or raised (best-effort behavior).
    """
    global _client
    target = (kb_name or "").strip()
    with _client_lock:
        # Also drop any path-cached clients (get_client_for_path) — the per-KB
        # vectorstores use those, so recycle must clear them too or a wedged
        # client survives. Rare recovery action → nuke all (re-created on next
        # access, each cleanly closed first).
        for _pc in _clients_by_path.values():
            _safe_close(_pc)
        _clients_by_path.clear()
        if not target or target == "memory":
            closed = _safe_close(_client)
            _client = None
            cleared = len(_collections)
            _collections.clear()
            _count_cache.clear()
            return {
                "recycled": "memory",
                "collections_cleared": cleared,
                "closed": closed,
            }
        closed = _safe_close(_kb_clients.pop(target, None))
        return {
            "recycled": target,
            "collections_cleared": 0,
            "closed": closed,
        }


def _safe_close(client) -> bool:
    """Best-effort ``client.close()``. Returns True when close ran without
    raising. False covers three cases: client was None, has no ``close``
    attribute (older chromadb), or close() raised. Failure must never
    block the recycle — the cached reference still gets dropped.
    """
    if client is None:
        return False
    closer = getattr(client, "close", None)
    if closer is None:
        return False
    try:
        closer()
        return True
    except Exception:
        logger.debug("recycle_client: close() raised", exc_info=True)
        return False


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
                sample = col.peek(1)  # returns embeddings by default in chromadb 1.x
                embs = sample.get("embeddings") if sample else None
                if embs is not None and len(embs) > 0 and embs[0] is not None and len(embs[0]) > 0:
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
    # Generate ONE id so source + shadow share it (dual-write hook).
    doc_id = str(uuid.uuid4())
    try:
        col.add(
            documents=[text],
            embeddings=[embedding],
            metadatas=[metadata or {}],
            ids=[doc_id],
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
                ids=[doc_id],
            )
        else:
            raise
    _count_cache.pop(collection_name, None)
    # PROGRAM §40 Item 12 — best-effort shadow write. Hook is a no-op
    # unless the migration master switch is on AND the state machine
    # is in a phase that wants shadow writes. Failures swallowed —
    # never block the source write path.
    try:
        from app.memory.embedding_migration.dual_write import maybe_dual_write
        maybe_dual_write(collection_name, doc_id, text, metadata)
    except Exception:
        logger.debug("chromadb_manager: dual_write hook failed", exc_info=True)
    # PROGRAM §56 (2026-05-17) — source-ledger dual-write. Append the
    # row to workspace/<kb>/.source_ledger.jsonl so the KB stays
    # reconstructable even if chroma.sqlite3 + HNSW segment dirs are
    # lost entirely. Failure-isolated end-to-end; never blocks the
    # live write path. ``memory`` is the default KB for this module's
    # PersistentClient (see PERSIST_DIR); other KBs go through
    # scoped_memory which has its own hook. See
    # ``app/memory/source_ledger.py`` for the protocol.
    try:
        from app.memory.source_ledger import append_row
        append_row("memory", collection_name, doc_id, text, metadata or {})
    except Exception:
        logger.debug("chromadb_manager: source_ledger hook failed", exc_info=True)


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
    # PROGRAM §40 Item 12 — best-effort shadow-read divergence sample.
    # Hook is a no-op unless the migration master switch is on AND the
    # state machine is in SHADOW_READ / READY. Sampling is internal.
    try:
        from app.memory.embedding_migration.shadow_read import maybe_shadow_read
        observed_ids = (results.get("ids") or [[]])[0]
        maybe_shadow_read(
            collection_name, query, list(observed_ids), n_results=n,
        )
    except Exception:
        logger.debug("chromadb_manager: shadow_read hook failed", exc_info=True)
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
