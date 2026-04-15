"""aesthetics/api.py — FastAPI routes for the aesthetic pattern library."""

from __future__ import annotations

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

aesthetics_router = APIRouter(prefix="/aesthetics", tags=["aesthetics"])


@aesthetics_router.get("/stats")
async def get_stats():
    from app.aesthetics.vectorstore import get_store
    return get_store().get_stats()


@aesthetics_router.get("/patterns")
async def search_patterns(query: str = "elegant", n: int = 5):
    from app.aesthetics.vectorstore import get_store
    store = get_store()
    results = store.query(query_text=query, n_results=n)
    return {"patterns": results}
