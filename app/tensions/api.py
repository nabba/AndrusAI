"""tensions/api.py — FastAPI routes for the tensions/contradictions KB."""

from __future__ import annotations

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

tensions_router = APIRouter(prefix="/tensions", tags=["tensions"])


@tensions_router.get("/stats")
async def get_stats():
    from app.tensions.vectorstore import get_store
    return get_store().get_stats()


@tensions_router.get("/unresolved")
async def unresolved(n: int = 10):
    """Return currently unresolved tensions (growth edges)."""
    from app.tensions.vectorstore import get_store
    store = get_store()
    results = store.get_unresolved(n=n)
    return {"tensions": results}
