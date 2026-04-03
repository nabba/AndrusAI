"""
health.py — Health check, dashboard serving, and generated document serving.

Extracted from main.py.
"""

import logging
from pathlib import Path

from fastapi import APIRouter
from starlette.responses import HTMLResponse, FileResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

DOCS_DIR = Path("/app/workspace/output/docs")


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


@router.get("/docs/{filename}")
async def serve_generated_doc(filename: str):
    """Serve generated documents (HTML pages, PDFs, etc.) for Signal URL delivery.

    When agents generate HTML reports, they return a URL like:
    http://localhost:8765/docs/page_20260403_120000.html
    This endpoint serves those files.
    """
    # Security: only serve from the docs output directory, no path traversal
    safe_name = Path(filename).name  # Strip any path components
    filepath = DOCS_DIR / safe_name

    if not filepath.exists():
        return HTMLResponse("<h1>Not Found</h1>", status_code=404)

    try:
        filepath.resolve().relative_to(DOCS_DIR.resolve())
    except ValueError:
        return HTMLResponse("<h1>Forbidden</h1>", status_code=403)

    # Determine content type
    ext = filepath.suffix.lower()
    media_types = {
        ".html": "text/html",
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    media_type = media_types.get(ext, "application/octet-stream")

    return FileResponse(filepath, media_type=media_type, filename=safe_name)


@router.get("/docs")
async def list_generated_docs():
    """List all generated documents."""
    from app.tools.document_generator import list_documents
    return {"documents": list_documents()}
