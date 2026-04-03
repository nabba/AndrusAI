"""
health.py — Health check and dashboard serving endpoints.

Extracted from main.py.
"""

import logging
from pathlib import Path

from fastapi import APIRouter
from starlette.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/dashboard")
async def serve_dashboard():
    """Serve the dashboard HTML from the container filesystem."""
    try:
        html = Path("/app/dashboard/index.html").read_text(encoding="utf-8")
        return HTMLResponse(html)
    except Exception as exc:
        logger.error(f"Dashboard error: {exc}", exc_info=True)
        return HTMLResponse("<h1>Error</h1><p>An internal error occurred.</p>", status_code=500)
