"""
kb.py — Knowledge Base API endpoints.

Extracted from main.py. Handles file upload, ingestion, status, removal, and reset.
"""

import asyncio
import logging
import os
import re
import threading

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

logger = logging.getLogger(__name__)

router = APIRouter(tags=["knowledge-base"])

# Lazy singleton for KnowledgeStore (heavy init — loads embedding model)
_kb_store = None
_kb_store_lock = threading.Lock()

ALLOWED_EXTENSIONS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".csv",
    ".txt", ".md", ".html", ".htm", ".json",
}
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB


def _get_kb_store():
    global _kb_store
    if _kb_store is None:
        with _kb_store_lock:
            if _kb_store is None:
                from app.knowledge_base.vectorstore import KnowledgeStore
                _kb_store = KnowledgeStore()
    return _kb_store


@router.post("/upload")
async def kb_upload(
    file: UploadFile = File(...),
    category: str = Form("general"),
):
    """Ingest an uploaded file into the knowledge base."""
    import tempfile

    filename = file.filename or "upload"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {sorted(ALLOWED_EXTENSIONS)}",
        )

    category = re.sub(r"[^a-zA-Z0-9_\-]", "", category or "general") or "general"

    tmp_path = None
    try:
        contents = await file.read()
        if len(contents) > MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=413, detail="File too large (max 50 MB)")
        if len(contents) == 0:
            raise HTTPException(status_code=400, detail="Empty file")

        with tempfile.NamedTemporaryFile(delete=False, suffix=ext, prefix="kb_upload_") as tmp:
            tmp.write(contents)
            tmp_path = tmp.name

        store = await asyncio.to_thread(_get_kb_store)
        result = await asyncio.to_thread(store.add_document, tmp_path, category=category)

        if not result.success:
            raise HTTPException(status_code=422, detail=result.error or "Ingestion failed")

        return {
            "status": "ok",
            "source": result.source,
            "format": result.format,
            "chunks_created": result.chunks_created,
            "total_characters": result.total_characters,
            "document_id": result.document_id,
            "category": category,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"KB upload error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


@router.get("/status")
async def kb_status():
    """Return knowledge base statistics."""
    try:
        store = await asyncio.to_thread(_get_kb_store)
        stats = await asyncio.to_thread(store.stats)
        return {"status": "ok", **stats}
    except Exception as exc:
        logger.error(f"KB status error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/remove")
async def kb_remove(request: Request):
    """Remove a document by source_path."""
    try:
        body = await request.json()
        source_path = body.get("source_path", "")
        if not source_path:
            raise HTTPException(status_code=400, detail="source_path required")
        store = await asyncio.to_thread(_get_kb_store)
        count = await asyncio.to_thread(store.remove_document, source_path)
        try:
            from app.firebase_reporter import report_knowledge_base
            report_knowledge_base()
        except Exception:
            pass
        return {"status": "ok", "removed": count, "source_path": source_path}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"KB remove error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/reset")
async def kb_reset_endpoint():
    """Reset the entire knowledge base."""
    try:
        store = await asyncio.to_thread(_get_kb_store)
        await asyncio.to_thread(store.reset)
        try:
            from app.firebase_reporter import report_knowledge_base
            report_knowledge_base()
        except Exception:
            pass
        return {"status": "ok", "message": "Knowledge base has been reset"}
    except Exception as exc:
        logger.error(f"KB reset error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
