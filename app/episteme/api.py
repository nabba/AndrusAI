"""episteme/api.py — FastAPI routes for the research/metacognitive KB."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.episteme import config

logger = logging.getLogger(__name__)

episteme_router = APIRouter(prefix="/episteme", tags=["episteme"])


@episteme_router.post("/upload")
async def upload_text(
    file: UploadFile = File(...),
    author: str = Form("Unknown"),
    paper_type: str = Form("unknown"),
    domain: str = Form("general"),
    epistemic_status: str = Form("theoretical"),
    date: str = Form(""),
    title: str = Form(""),
):
    """Upload a research text (.md or .txt) to the episteme KB."""
    if not file.filename:
        raise HTTPException(400, "No filename")

    safe_name = re.sub(r"[^\w\-.]", "_", file.filename)
    if not safe_name.endswith((".md", ".txt")):
        safe_name += ".md"

    content = await file.read()
    if len(content) > config.MAX_UPLOAD_SIZE:
        raise HTTPException(413, "File too large (max 10 MB)")

    texts_dir = Path(config.TEXTS_DIR)
    texts_dir.mkdir(parents=True, exist_ok=True)

    dest = texts_dir / safe_name
    text = content.decode("utf-8", errors="replace")

    if not text.startswith("---"):
        frontmatter = (
            f"---\ntitle: \"{title or safe_name}\"\n"
            f"author: \"{author}\"\npaper_type: {paper_type}\n"
            f"domain: {domain}\nepistemic_status: {epistemic_status}\n"
            f"date: \"{date}\"\n---\n\n"
        )
        text = frontmatter + text

    dest.write_text(text, encoding="utf-8")

    from app.episteme.ingestion import ingest_file
    chunks = ingest_file(dest)

    return {
        "status": "ok",
        "filename": safe_name,
        "chunks_created": chunks,
        "characters": len(text),
    }


@episteme_router.get("/stats")
async def get_stats():
    from app.episteme.vectorstore import get_store
    return get_store().get_stats()


@episteme_router.get("/texts")
async def list_texts():
    from app.episteme.vectorstore import get_store
    return get_store().list_texts()


@episteme_router.post("/reingest")
async def reingest():
    from app.episteme.ingestion import ingest_directory
    total = ingest_directory()
    return {"status": "ok", "total_chunks": total}
