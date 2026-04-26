"""
Philosophy RAG — FastAPI Routes
=================================
Dashboard endpoints for uploading, managing, and querying philosophical texts.

Mounted in main.py as: app.include_router(philosophy_router)
"""

import asyncio
import logging
import os
import re
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.philosophy import config

logger = logging.getLogger(__name__)

philosophy_router = APIRouter(prefix="/philosophy", tags=["philosophy"])


def _get_store():
    """Lazy import to avoid circular imports at module load time."""
    from app.philosophy.vectorstore import get_store
    return get_store()


def _texts_dir() -> Path:
    p = Path(config.TEXTS_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p


@philosophy_router.post("/upload")
async def upload_philosophy_text(
    file: UploadFile = File(...),
    author: str = Form(""),
    tradition: str = Form(""),
    era: str = Form(""),
    title: str = Form(""),
):
    """Upload a .md file into the philosophy knowledge base.

    The file is saved to workspace/philosophy/texts/ and ingested into ChromaDB.
    If the file has YAML frontmatter, those values take precedence over form fields.
    """
    filename = file.filename or "upload.md"

    # Validate extension
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".md", ".txt"):
        raise HTTPException(
            status_code=400,
            detail=f"Only .md and .txt files are supported, got '{ext}'",
        )

    # Sanitize filename
    safe_name = re.sub(r"[^\w\-.]", "_", filename)
    if not safe_name.endswith((".md", ".txt")):
        safe_name += ".md"

    # Read content
    contents = await file.read()
    if len(contents) > config.MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="File too large (max 10 MB)")
    if len(contents) == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    text = contents.decode("utf-8", errors="replace")

    # If no frontmatter in the file and form fields provided, prepend it
    if not text.lstrip().startswith("---") and any([author, tradition, era, title]):
        frontmatter_lines = ["---"]
        if author:
            frontmatter_lines.append(f"author: {author}")
        if tradition:
            frontmatter_lines.append(f"tradition: {tradition}")
        if era:
            frontmatter_lines.append(f"era: {era}")
        if title:
            frontmatter_lines.append(f"title: {title}")
        frontmatter_lines.append("---\n")
        text = "\n".join(frontmatter_lines) + text

    # Save to texts directory
    dest = _texts_dir() / safe_name
    dest.write_text(text, encoding="utf-8")

    # Ingest
    try:
        from app.philosophy.ingestion import ingest_text
        chunks_added = await asyncio.to_thread(
            ingest_text,
            text=text,
            filename=safe_name,
            author=author or "Unknown",
            tradition=tradition or "Unknown",
            era=era or "Unknown",
            title=title or filename,
        )
    except Exception as exc:
        logger.exception("Philosophy ingestion failed")
        logger.error(f"API error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

    # Report stats to Firebase
    try:
        _report_stats_async()
    except Exception:
        pass

    return {
        "status": "ok",
        "filename": safe_name,
        "chunks_created": chunks_added,
        "characters": len(text),
    }


@philosophy_router.post("/upload-text")
async def upload_philosophy_raw_text(
    text: str = Form(...),
    filename: str = Form("manual_entry.md"),
    author: str = Form("Unknown"),
    tradition: str = Form("Unknown"),
    era: str = Form("Unknown"),
    title: str = Form("Unknown"),
):
    """Submit raw markdown text directly (no file upload needed)."""
    if not text.strip():
        raise HTTPException(status_code=400, detail="Empty text")
    if len(text) > config.MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="Text too large (max 10 MB)")

    safe_name = re.sub(r"[^\w\-.]", "_", filename)
    if not safe_name.endswith((".md", ".txt")):
        safe_name += ".md"

    # Save
    dest = _texts_dir() / safe_name
    dest.write_text(text, encoding="utf-8")

    try:
        from app.philosophy.ingestion import ingest_text
        chunks_added = await asyncio.to_thread(
            ingest_text,
            text=text,
            filename=safe_name,
            author=author,
            tradition=tradition,
            era=era,
            title=title,
        )
    except Exception as exc:
        logger.exception("Philosophy text ingestion failed")
        logger.error(f"API error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

    try:
        _report_stats_async()
    except Exception:
        pass

    return {
        "status": "ok",
        "filename": safe_name,
        "chunks_created": chunks_added,
        "characters": len(text),
    }


@philosophy_router.get("/status")
@philosophy_router.get("/stats")  # alias — dashboard React calls /philosophy/stats
async def philosophy_status():
    """Return philosophy knowledge base statistics.

    Both ``/status`` and ``/stats`` resolve here. The React dashboard
    expects ``/stats`` (consistent with episteme/aesthetics/tensions);
    older callers use ``/status``. Keep both indefinitely — the router
    decorator stack costs zero at request time.
    """
    try:
        store = await asyncio.to_thread(_get_store)
        stats = await asyncio.to_thread(store.get_stats)
        return {"status": "ok", **stats}
    except Exception as exc:
        logger.exception("Philosophy status failed")
        logger.error(f"API error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@philosophy_router.get("/texts")
@philosophy_router.get("/documents")  # alias — frontend symmetry
async def list_philosophy_texts():
    """List all philosophy text files with metadata.

    2026-04-26: response includes ``added_at`` (filesystem mtime) and a
    normalized ``themes`` list combining tradition + era so the dashboard
    can render a unified document table across KBs.
    """
    from datetime import datetime, timezone
    texts_dir = _texts_dir()
    texts = []

    for f in sorted(texts_dir.glob("*.md")):
        if f.name.upper() == "README.MD":
            continue
        try:
            content = f.read_text(encoding="utf-8")
            from app.philosophy.ingestion import extract_frontmatter
            meta, _ = extract_frontmatter(content)
            stat = f.stat()
            tradition = meta.get("tradition", "")
            era = meta.get("era", "")
            themes = [t for t in (tradition, era) if t and t != "Unknown"]
            texts.append({
                "filename": f.name,
                "author": meta.get("author", "Unknown"),
                "tradition": tradition or "Unknown",
                "era": era or "Unknown",
                "title": meta.get("title", f.stem),
                "themes": themes,
                "size_bytes": stat.st_size,
                "added_at": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc,
                ).isoformat(),
            })
        except Exception:
            texts.append({"filename": f.name, "error": "Could not read metadata"})

    return {"status": "ok", "texts": texts, "total": len(texts)}


@philosophy_router.delete("/texts/{filename}")
async def delete_philosophy_text(filename: str):
    """Remove a philosophy text and its chunks from the collection."""
    safe_name = re.sub(r"[^\w\-.]", "_", filename)
    filepath = _texts_dir() / safe_name

    # Remove from ChromaDB
    try:
        store = await asyncio.to_thread(_get_store)
        removed = await asyncio.to_thread(store.remove_by_source, safe_name)
    except Exception as exc:
        logger.exception("Philosophy chunk removal failed")
        logger.error(f"API error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

    # Remove file
    if filepath.exists():
        filepath.unlink()

    try:
        _report_stats_async()
    except Exception:
        pass

    return {"status": "ok", "filename": safe_name, "chunks_removed": removed}


@philosophy_router.post("/reingest")
async def reingest_all():
    """Re-ingest all texts from the texts directory.

    Useful after changing embedding models.
    """
    try:
        store = await asyncio.to_thread(_get_store)
        await asyncio.to_thread(store.reset_collection)

        from app.philosophy.ingestion import ingest_directory
        summary = await asyncio.to_thread(ingest_directory, _texts_dir(), store)
    except Exception as exc:
        logger.exception("Philosophy re-ingestion failed")
        logger.error(f"API error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

    try:
        _report_stats_async()
    except Exception:
        pass

    return {"status": "ok", **summary}


def _report_stats_async():
    """Fire-and-forget stats report to Firebase."""
    try:
        from app.firebase_reporter import report_philosophy_kb
        from concurrent.futures import ThreadPoolExecutor
        _pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="phil-report")
        _pool.submit(report_philosophy_kb)
    except Exception:
        pass
