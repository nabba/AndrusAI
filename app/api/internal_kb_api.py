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
