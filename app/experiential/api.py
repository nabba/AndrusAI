"""experiential/api.py — FastAPI routes for the journal/experiential KB."""

from __future__ import annotations

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

experiential_router = APIRouter(prefix="/experiential", tags=["experiential"])


@experiential_router.get("/stats")
async def get_stats():
    from app.experiential.vectorstore import get_store
    return get_store().get_stats()


@experiential_router.get("/recent")
async def recent_entries(n: int = 10):
    """Return recent journal entries (by created_at)."""
    from app.experiential.vectorstore import get_store
    store = get_store()
    results = store.query(
        query_text="recent experience reflection",
        n_results=n,
    )
    return {"entries": results}
