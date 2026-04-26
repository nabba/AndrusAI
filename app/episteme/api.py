"""episteme/api.py — FastAPI routes for the research/metacognitive KB."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.episteme import config

logger = logging.getLogger(__name__)

episteme_router = APIRouter(prefix="/episteme", tags=["episteme"])


async def _process_one_upload(
    file: UploadFile,
    author: str,
    paper_type: str,
    domain: str,
    epistemic_status: str,
    date: str,
    title: str,
) -> dict:
    """Process a single uploaded file. Returns a result dict."""
    if not file.filename:
        return {"filename": "?", "error": "No filename", "chunks_created": 0}

    safe_name = re.sub(r"[^\w\-.]", "_", file.filename)
    if not safe_name.endswith((".md", ".txt")):
        safe_name += ".md"

    content = await file.read()
    if len(content) > config.MAX_UPLOAD_SIZE:
        return {"filename": safe_name, "error": "File too large (max 10 MB)", "chunks_created": 0}

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

    try:
        # 2026-04-26: ingest_file is a SYNC operation (chunk + embed +
        # ChromaDB write) that for a large research PDF takes 30-90s.
        # Calling it directly from an `async def` handler freezes the
        # asyncio event loop for the whole duration — every OTHER
        # request on the gateway blocks too, the dashboard's polling
        # KBs see timeouts, and the user-visible result is "504 Gateway
        # timeout" + cascading "Failed to load" cards across all KBs.
        # Philosophy + fiction handlers already use asyncio.to_thread;
        # this fix brings episteme into line.
        from app.episteme.ingestion import ingest_file
        import asyncio as _asyncio
        chunks = await _asyncio.to_thread(ingest_file, dest)
    except Exception as e:
        logger.error("Ingestion failed for %s: %s", safe_name, e)
        return {"filename": safe_name, "error": str(e)[:200], "chunks_created": 0}

    return {
        "filename": safe_name,
        "chunks_created": chunks,
        "characters": len(text),
    }


@episteme_router.post("/upload")
async def upload_texts(
    file: List[UploadFile] = File(...),
    author: str = Form("Unknown"),
    paper_type: str = Form("unknown"),
    domain: str = Form("general"),
    epistemic_status: str = Form("theoretical"),
    date: str = Form(""),
    title: str = Form(""),
):
    """Upload one or more research texts (.md or .txt) to the episteme KB.

    Accepts multiple files in a single request. Metadata fields (author,
    paper_type, etc.) apply as defaults — files with YAML frontmatter
    override them per-file.
    """
    results = []
    for f in file:
        result = await _process_one_upload(
            f, author, paper_type, domain, epistemic_status, date, title,
        )
        results.append(result)

    total_chunks = sum(r.get("chunks_created", 0) for r in results)
    errors = [r for r in results if "error" in r]

    _report_async()

    # Backward-compatible: single file returns flat response
    if len(results) == 1:
        r = results[0]
        if "error" in r:
            raise HTTPException(400, r["error"])
        return {"status": "ok", **r}

    return {
        "status": "ok" if not errors else "partial",
        "files_processed": len(results),
        "total_chunks": total_chunks,
        "results": results,
        "errors": len(errors),
    }


def _report_async() -> None:
    try:
        from app.firebase.publish import report_episteme_kb
        from concurrent.futures import ThreadPoolExecutor
        ThreadPoolExecutor(max_workers=1).submit(report_episteme_kb)
    except Exception:
        pass


@episteme_router.get("/stats")
async def get_stats():
    from app.episteme.vectorstore import get_store
    return get_store().get_stats()


@episteme_router.get("/texts")
@episteme_router.get("/documents")  # alias — frontend symmetry
async def list_texts():
    """Return per-document metadata for the episteme KB.

    2026-04-26: enriched with ``added_at`` (filesystem mtime) and
    ``themes`` (paper_type + domain) to support the dashboard's
    unified document table view.
    """
    import asyncio as _asyncio
    from datetime import datetime, timezone
    from pathlib import Path
    from app.episteme.vectorstore import get_store
    from app.episteme import config as _ep_config
    rows = await _asyncio.to_thread(get_store().list_texts)
    texts_dir = Path(_ep_config.TEXTS_DIR)
    enriched = []
    for r in rows:
        fn = r.get("filename") or ""
        added_at = None
        if fn:
            p = texts_dir / fn
            if p.exists():
                try:
                    added_at = datetime.fromtimestamp(
                        p.stat().st_mtime, tz=timezone.utc,
                    ).isoformat()
                except Exception:
                    pass
        paper_type = r.get("paper_type", "")
        domain = r.get("domain", "")
        themes = [t for t in (paper_type, domain) if t and t != "Unknown"]
        enriched.append({**r, "themes": themes, "added_at": added_at})
    return enriched


@episteme_router.post("/reingest")
async def reingest():
    from app.episteme.ingestion import ingest_directory
    total = ingest_directory()
    return {"status": "ok", "total_chunks": total}
