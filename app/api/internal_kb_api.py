"""Internal KB read-API — serving/compute split (Increment 2, 2026-06-07).

A read-only endpoint the WORKER process calls so it never opens ChromaDB itself
(single-writer; §55). The query runs gateway-side via ``kb_read.query_local``
(the gateway is the sole ChromaDB process) and returns the same hit dicts the
in-process path would.

Auth: Bearer ``GATEWAY_SECRET`` (the worker shares the gateway's env). When no
secret is configured (laptop dev) the check is skipped. The route lives under
``/internal/*`` (not ``/api/cp/*``), so it is NOT exposed by the public
dashboard auth surface — it's a container-to-container call on the compose
network.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.memory import kb_read

logger = logging.getLogger(__name__)

router = APIRouter()


class KBQueryRequest(BaseModel):
    kb: str
    query_text: str
    n_results: int = 5
    where: Optional[dict] = None


def _check_auth(authorization: Optional[str]) -> None:
    secret = os.environ.get("GATEWAY_SECRET", "")
    if not secret:
        return  # dev mode — no secret configured
    if authorization != f"Bearer {secret}":
        raise HTTPException(status_code=401, detail="unauthorized")


@router.post("/internal/kb/query")
def kb_query(
    req: KBQueryRequest,
    authorization: Optional[str] = Header(default=None),
) -> dict:
    """Run a KB RAG query gateway-side for a worker caller."""
    _check_auth(authorization)
    try:
        hits = kb_read.query_local(
            req.kb, req.query_text, req.n_results, where=req.where
        )
    except ValueError as exc:  # unknown kb
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        logger.warning("internal kb query failed for kb=%s", req.kb, exc_info=True)
        # Fail-soft: an empty result degrades the worker's RAG context but never
        # 500s the worker job. (The worker also catches transport errors.)
        return {"hits": []}
    return {"hits": hits}


# ── Collection-level proxy endpoints (Increment 3) ───────────────────────────
# The worker's kb_proxy forwards raw chromadb collection ops here so it never
# opens ChromaDB itself. Gateway-side these run against the real collection
# (get_kb_client roots at the same /app/workspace/<kb> dir the worker's store
# would have opened). All sync `def` → FastAPI threadpool → never blocks the
# event loop (so replay embedding can't re-create the idle wedge).
def _real_collection(kb: str, collection: str):
    from app.memory import chromadb_manager
    return chromadb_manager.get_kb_client(kb).get_or_create_collection(collection)


class CollectionQuery(BaseModel):
    kb: str
    collection: str
    query_embeddings: Optional[list] = None
    query_texts: Optional[list] = None
    n_results: int = 10
    where: Optional[dict] = None
    include: Optional[list] = None


@router.post("/internal/kb/collection/query")
def kb_collection_query(req: CollectionQuery, authorization: Optional[str] = Header(default=None)) -> dict:
    _check_auth(authorization)
    try:
        col = _real_collection(req.kb, req.collection)
        kwargs: dict = {
            "n_results": int(req.n_results),
            "include": req.include or ["documents", "metadatas", "distances"],
        }
        if req.query_embeddings is not None:
            kwargs["query_embeddings"] = req.query_embeddings
        if req.query_texts is not None:
            kwargs["query_texts"] = req.query_texts
        if req.where:
            kwargs["where"] = req.where
        return {"result": col.query(**kwargs)}
    except Exception:
        logger.warning("internal collection.query failed for %s/%s", req.kb, req.collection, exc_info=True)
        return {"result": None}


class CollectionGet(BaseModel):
    kb: str
    collection: str
    ids: Optional[list] = None
    where: Optional[dict] = None
    limit: Optional[int] = None
    offset: Optional[int] = None
    include: Optional[list] = None


@router.post("/internal/kb/collection/get")
def kb_collection_get(req: CollectionGet, authorization: Optional[str] = Header(default=None)) -> dict:
    _check_auth(authorization)
    try:
        col = _real_collection(req.kb, req.collection)
        kwargs: dict = {}
        if req.ids is not None:
            kwargs["ids"] = req.ids
        if req.where:
            kwargs["where"] = req.where
        if req.limit is not None:
            kwargs["limit"] = req.limit
        if req.offset is not None:
            kwargs["offset"] = req.offset
        if req.include is not None:
            kwargs["include"] = req.include
        return {"result": col.get(**kwargs)}
    except Exception:
        logger.warning("internal collection.get failed for %s/%s", req.kb, req.collection, exc_info=True)
        return {"result": None}


class CollectionRef(BaseModel):
    kb: str
    collection: str


@router.post("/internal/kb/collection/count")
def kb_collection_count(req: CollectionRef, authorization: Optional[str] = Header(default=None)) -> dict:
    _check_auth(authorization)
    try:
        return {"count": int(_real_collection(req.kb, req.collection).count())}
    except Exception:
        logger.warning("internal collection.count failed for %s/%s", req.kb, req.collection, exc_info=True)
        return {"count": 0}


class CollectionPeek(BaseModel):
    kb: str
    collection: str
    n: int = 10


@router.post("/internal/kb/collection/peek")
def kb_collection_peek(req: CollectionPeek, authorization: Optional[str] = Header(default=None)) -> dict:
    _check_auth(authorization)
    try:
        return {"result": _real_collection(req.kb, req.collection).peek(int(req.n))}
    except Exception:
        return {"result": None}


class ReplayReq(BaseModel):
    kb: str
    since_ts: Optional[float] = None


_REPLAY_WINDOW_S = 600.0  # prompt-sync replays only the recent ledger tail


@router.post("/internal/kb/replay")
def kb_replay(req: ReplayReq, authorization: Optional[str] = Header(default=None)) -> dict:
    """Prompt-sync: replay the recent ledger tail for a KB into ChromaDB so a
    worker's ledger-first write becomes visible quickly. Incremental + idempotent
    (upsert per doc_id); defaults to the last few minutes when since_ts is unset
    so it never does a full rebuild. Sync def → FastAPI threadpool → the embed +
    upsert work never blocks the gateway event loop."""
    _check_auth(authorization)
    import time

    since = req.since_ts if req.since_ts is not None else (time.time() - _REPLAY_WINDOW_S)
    try:
        from app.memory import source_ledger

        res = source_ledger.replay_kb(req.kb, since_ts=since)
        return {"result": res.to_dict() if hasattr(res, "to_dict") else str(res)}
    except Exception:
        logger.warning("internal kb replay failed for kb=%s", req.kb, exc_info=True)
        return {"result": None}
