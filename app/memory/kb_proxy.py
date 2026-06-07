"""Worker-mode ChromaDB proxy — serving/compute split (Increment 3, 2026-06-07).

In the WORKER process (``IDLE_SCHEDULER_ROLE=worker``) ChromaDB must never be
opened (single-writer; §55). Instead of *raising* (Increment 1's interim
fail-closed), the three ``chromadb_manager`` accessors return one of these
proxy clients so existing KB code — the per-KB stores, ``chromadb_manager``
store/retrieve, the integrator, scoped_memory, and the LLM-tool-mediated
``learn-queue`` — runs UNCHANGED in the worker:

* READS (query/get/count/peek) → HTTP to the gateway's ``/internal/kb/...``
  endpoints (the gateway is the sole ChromaDB process). Fail-soft: a transport
  error degrades to an empty result, never crashes the idle job.
* WRITES (add/upsert/update/delete) → the §56 source ledger via
  ``source_ledger.hook_collection_*``. The ledger is a LOCAL file append
  serialized cross-process by §68's flock, so a worker write succeeds even if
  the gateway is down and can never produce a second SQLite writer. The
  gateway's ``source_ledger_daemon`` replays ledger→ChromaDB; this module also
  fires a best-effort prompt-sync replay so the write is visible quickly.

INVARIANT: this module never opens a ChromaDB client (no ``PersistentClient``).
It only does local file I/O (ledger) + HTTP (reads/replay-trigger).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Empty chromadb-shaped results for fail-soft reads (callers index [0]).
_EMPTY_QUERY = {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]], "embeddings": None}
_EMPTY_GET = {"ids": [], "documents": [], "metadatas": [], "embeddings": None}


def _is_worker() -> bool:
    return os.environ.get("IDLE_SCHEDULER_ROLE", "all").strip().lower() == "worker"


def _gateway_url() -> str:
    return os.environ.get("GATEWAY_INTERNAL_URL", "http://gateway:8765").rstrip("/")


def _internal_post(path: str, body: dict, *, timeout: int = 30) -> Optional[dict]:
    """POST to a gateway /internal endpoint. Returns the JSON dict or None on
    any failure (caller decides fail-soft vs raise)."""
    data = json.dumps(body).encode("utf-8")
    secret = os.environ.get("GATEWAY_SECRET", "")
    req = urllib.request.Request(
        _gateway_url() + path,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {secret}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def _trigger_replay(kb: str) -> None:
    """Fire-and-forget prompt-sync: ask the gateway to replay the ledger tail
    for ``kb`` into ChromaDB so a worker write becomes visible promptly. The
    write is already durable in the ledger; the daily daemon is the backstop, so
    any failure here is swallowed."""
    def _go() -> None:
        try:
            _internal_post("/internal/kb/replay", {"kb": kb}, timeout=120)
        except Exception:
            logger.debug("kb_proxy: prompt-sync replay failed for kb=%s", kb, exc_info=True)

    try:
        threading.Thread(target=_go, name=f"kb-replay-{kb}", daemon=True).start()
    except Exception:
        logger.debug("kb_proxy: could not spawn replay thread for kb=%s", kb, exc_info=True)


def _as_list(x: Any) -> list:
    if x is None:
        return []
    return list(x) if isinstance(x, (list, tuple)) else [x]


class _ProxyCollection:
    """Stand-in for a chromadb Collection in the worker. Reads → gateway HTTP;
    writes → source ledger. Implements exactly the methods the codebase calls."""

    def __init__(self, kb: str, name: str):
        self._kb = kb
        self.name = name

    # ── reads (fail-soft to empty) ────────────────────────────────────────
    def query(self, *, query_embeddings=None, query_texts=None, n_results: int = 10,
              where: Optional[dict] = None, include=None, **_ignored) -> dict:
        body = {
            "kb": self._kb, "collection": self.name,
            "query_embeddings": query_embeddings, "query_texts": query_texts,
            "n_results": int(n_results), "where": where, "include": include,
        }
        try:
            out = _internal_post("/internal/kb/collection/query", body)
            return (out or {}).get("result") or _EMPTY_QUERY
        except Exception:
            logger.warning("kb_proxy.query fail-soft for %s/%s", self._kb, self.name, exc_info=True)
            return _EMPTY_QUERY

    def get(self, *, ids=None, where: Optional[dict] = None, limit=None, offset=None,
            include=None, **_ignored) -> dict:
        body = {
            "kb": self._kb, "collection": self.name, "ids": ids, "where": where,
            "limit": limit, "offset": offset, "include": include,
        }
        try:
            out = _internal_post("/internal/kb/collection/get", body)
            return (out or {}).get("result") or _EMPTY_GET
        except Exception:
            logger.warning("kb_proxy.get fail-soft for %s/%s", self._kb, self.name, exc_info=True)
            return dict(_EMPTY_GET)

    def count(self) -> int:
        try:
            out = _internal_post("/internal/kb/collection/count", {"kb": self._kb, "collection": self.name})
            return int((out or {}).get("count") or 0)
        except Exception:
            logger.warning("kb_proxy.count fail-soft for %s/%s", self._kb, self.name, exc_info=True)
            return 0

    def peek(self, n: int = 10) -> dict:
        # Fail-soft to an empty (no-embeddings) peek so a cold gateway never
        # blocks worker store construction's dimension probe — and the probe's
        # recreate branch (delete_collection) is a no-op in the worker anyway.
        try:
            out = _internal_post("/internal/kb/collection/peek", {"kb": self._kb, "collection": self.name, "n": int(n)})
            return (out or {}).get("result") or dict(_EMPTY_GET)
        except Exception:
            return dict(_EMPTY_GET)

    # ── writes (ledger-first; local + durable; never silently drop) ───────
    def add(self, *, ids=None, documents=None, metadatas=None, embeddings=None, **_ignored):
        from app.memory import source_ledger
        source_ledger.hook_collection_add(self._kb, self.name, _as_list(ids), _as_list(documents), metadatas)
        _trigger_replay(self._kb)

    def upsert(self, *, ids=None, documents=None, metadatas=None, embeddings=None, **_ignored):
        # Recorded as an add row: replay folds latest-add-wins per (collection,
        # doc_id) and re-applies via col.upsert, so the end state matches a
        # chromadb upsert. (chromadb upsert REPLACES, does not merge — unchanged.)
        from app.memory import source_ledger
        source_ledger.hook_collection_add(self._kb, self.name, _as_list(ids), _as_list(documents), metadatas)
        _trigger_replay(self._kb)

    def update(self, *, ids=None, documents=None, metadatas=None, **_ignored):
        from app.memory import source_ledger
        source_ledger.hook_collection_update(self._kb, self.name, _as_list(ids), documents, metadatas)
        _trigger_replay(self._kb)

    def delete(self, *, ids=None, where: Optional[dict] = None, **_ignored):
        from app.memory import source_ledger
        target_ids = _as_list(ids)
        if not target_ids and where:
            # Resolve ids gateway-side first, then tombstone. A dropped delete
            # would resurrect data on the next replay, so RAISE on failure
            # rather than silently no-op (this is the one gateway-dependent
            # write; all others are local).
            out = _internal_post("/internal/kb/collection/get",
                                 {"kb": self._kb, "collection": self.name, "where": where, "include": []})
            target_ids = list(((out or {}).get("result") or {}).get("ids") or [])
            if not target_ids:
                return  # nothing matched
        if target_ids:
            source_ledger.hook_collection_delete(self._kb, self.name, target_ids)
            _trigger_replay(self._kb)


class _ProxyClient:
    """Stand-in for a chromadb PersistentClient in the worker, bound to one KB."""

    def __init__(self, kb: str):
        self._kb = kb

    def get_or_create_collection(self, name: str, **_ignored) -> _ProxyCollection:
        return _ProxyCollection(self._kb, name)

    # alias — DR/rebuild tools call get_collection; safe to treat as the same
    def get_collection(self, name: str, **_ignored) -> _ProxyCollection:
        return _ProxyCollection(self._kb, name)

    def delete_collection(self, name: str, **_ignored) -> None:
        # A worker must NOT destroy the physical store (§55 — only the gateway
        # mutates ChromaDB). The only callers are the dimension-mismatch recreate
        # branches, which are gateway concerns. No-op + warn.
        logger.warning("kb_proxy: delete_collection(%s/%s) suppressed in worker mode", self._kb, name)

    def list_collections(self) -> list:
        return []  # worker has no local physical store to enumerate

    def close(self) -> None:  # recycle_client compatibility
        return None


_proxy_clients: dict[str, _ProxyClient] = {}
_lock = threading.Lock()


def proxy_client_for_kb(kb: str) -> _ProxyClient:
    """Process-cached proxy client for a KB name (mirrors chromadb_manager's
    own per-KB client cache)."""
    key = (kb or "memory").strip() or "memory"
    with _lock:
        client = _proxy_clients.get(key)
        if client is None:
            client = _ProxyClient(key)
            _proxy_clients[key] = client
        return client
