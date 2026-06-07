"""Worker-safe KB read facade — serving/compute split (Increment 2, 2026-06-07).

In the gateway process (``IDLE_SCHEDULER_ROLE`` = ``all``/``gateway``) a KB RAG
read opens ChromaDB directly via the KB's store. In the WORKER process
(``IDLE_SCHEDULER_ROLE=worker``) ChromaDB is forbidden (single-writer; §55), so
reads route over HTTP to the gateway's ``POST /internal/kb/query`` endpoint,
which runs the SAME store query gateway-side and returns identical hit dicts.

This is the read half of the split. Writes do NOT go through here — they go
ledger-first (the §56 source ledger is cross-process-safe via flock §68, and the
gateway's ``source_ledger_daemon`` replays them into ChromaDB).

Consumers call :func:`query` and get a list of ``{text, metadata, score, id}``
hits regardless of which process they run in. The gateway endpoint calls
:func:`query_local` (never the routing facade, so it can't recurse to remote).
Both read paths are fail-soft: a transport/looked-up error degrades the RAG
context to ``[]`` rather than crashing the calling job.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

# kb name -> module exposing get_store(). Local dispatch reuses each KB's real
# store.query, so worker results are byte-identical to the in-process path.
_QUERY_KBS: dict[str, str] = {
    "episteme": "app.episteme.vectorstore",
    "experiential": "app.experiential.vectorstore",
    "philosophy": "app.philosophy.vectorstore",
    "aesthetics": "app.aesthetics.vectorstore",
    "tensions": "app.tensions.vectorstore",
    "knowledge": "app.knowledge_base.vectorstore",
}


def _is_worker() -> bool:
    return os.environ.get("IDLE_SCHEDULER_ROLE", "all").strip().lower() == "worker"


def _gateway_url() -> str:
    # Container-to-container: the worker reaches the gateway by compose service
    # name on the shared network. Override with GATEWAY_INTERNAL_URL if needed.
    return os.environ.get("GATEWAY_INTERNAL_URL", "http://gateway:8765").rstrip("/")


def query(
    kb: str,
    query_text: str,
    n_results: int = 5,
    *,
    where: Optional[dict] = None,
) -> list[dict]:
    """RAG-query a KB; return a list of ``{text, metadata, score, id}`` hits.

    Routes to the gateway over HTTP when running in the worker process,
    otherwise queries ChromaDB locally. Never raises — returns ``[]`` on any
    failure (a degraded RAG context must not crash an idle job)."""
    try:
        if _is_worker():
            return _query_remote(kb, query_text, n_results, where=where)
        return query_local(kb, query_text, n_results, where=where)
    except Exception:
        logger.warning("kb_read.query failed for kb=%s (returning [])", kb, exc_info=True)
        return []


def query_local(
    kb: str,
    query_text: str,
    n_results: int = 5,
    *,
    where: Optional[dict] = None,
) -> list[dict]:
    """Gateway-side dispatch: run the KB's own store.query (opens ChromaDB).

    This is what the ``/internal/kb/query`` endpoint calls. It must NEVER be
    reached in the worker process (it would trip the chromadb_manager guard)."""
    mod = _QUERY_KBS.get(kb)
    if not mod:
        raise ValueError(f"kb_read: unknown kb {kb!r} (known: {sorted(_QUERY_KBS)})")
    import importlib

    store = importlib.import_module(mod).get_store()
    kwargs: dict = {"query_text": query_text, "n_results": int(n_results)}
    if where:
        kwargs["where_filter"] = where
    hits = store.query(**kwargs)
    return list(hits or [])


def _query_remote(
    kb: str,
    query_text: str,
    n_results: int,
    *,
    where: Optional[dict] = None,
) -> list[dict]:
    body = json.dumps(
        {"kb": kb, "query_text": query_text, "n_results": int(n_results), "where": where}
    ).encode("utf-8")
    secret = os.environ.get("GATEWAY_SECRET", "")
    req = urllib.request.Request(
        _gateway_url() + "/internal/kb/query",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {secret}",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read() or b"{}")
    return list(data.get("hits") or [])
