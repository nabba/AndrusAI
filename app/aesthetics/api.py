"""aesthetics/api.py — FastAPI routes for the aesthetic pattern library."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.aesthetics import config

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


@aesthetics_router.post("/upload")
async def upload_pattern(
    text: str = Form(None),
    file: UploadFile | None = File(None),
    pattern_type: str = Form("creative_solution"),
    domain: str = Form("general"),
    quality_score: float = Form(0.8),
    flagged_by: str = Form("user"),
):
    """Upload an aesthetic pattern (text or .md/.txt file).

    Users can submit examples of elegant code, beautiful prose, or
    well-structured arguments to build the system's taste.
    """
    # Get text from either direct input or file.
    content = ""
    if text and text.strip():
        content = text.strip()
    elif file and file.filename:
        raw = await file.read()
        if len(raw) > 5 * 1024 * 1024:  # 5 MB
            raise HTTPException(413, "File too large (max 5 MB)")
        content = raw.decode("utf-8", errors="replace").strip()

    if not content:
        raise HTTPException(400, "Provide text or upload a file")

    if pattern_type not in config.PATTERN_TYPES:
        pattern_type = "creative_solution"

    now = datetime.now(timezone.utc)
    pattern_id = f"aes_{now.strftime('%Y%m%d_%H%M%S')}_{flagged_by}"

    metadata = {
        "pattern_type": pattern_type,
        "domain": domain,
        "flagged_by": flagged_by,
        "quality_score": str(round(min(max(quality_score, 0), 1), 2)),
        "epistemic_status": "evaluative/subjective",
        "created_at": now.isoformat(),
    }

    # 2026-04-26: store.add_pattern does sync embed + ChromaDB write (~1-3s
    # per call). Calling it directly from an async handler blocks the
    # asyncio loop for the whole duration; under concurrent uploads or
    # background polling that surfaces as "504 Gateway timeout" / "Load
    # failed" cards across the dashboard. Same fix as episteme.api.
    import asyncio as _asyncio
    from app.aesthetics.vectorstore import get_store
    store = await _asyncio.to_thread(get_store)
    ok = await _asyncio.to_thread(store.add_pattern, content, metadata, pattern_id)

    if not ok:
        raise HTTPException(500, "Failed to store pattern")

    # Persist to disk.
    try:
        patterns_dir = Path(config.PATTERNS_DIR)
        patterns_dir.mkdir(parents=True, exist_ok=True)
        filepath = patterns_dir / f"{pattern_id}.md"
        filepath.write_text(
            f"---\npattern_type: {pattern_type}\ndomain: {domain}\n"
            f"quality_score: {quality_score}\nflagged_by: {flagged_by}\n"
            f"created_at: {now.isoformat()}\n---\n\n{content}\n",
            encoding="utf-8",
        )
    except Exception:
        pass

    _report_async()
    return {
        "status": "ok",
        "pattern_id": pattern_id,
        "pattern_type": pattern_type,
        "characters": len(content),
    }


@aesthetics_router.delete("/patterns/{pattern_id}")
async def delete_pattern(pattern_id: str):
    from app.aesthetics.vectorstore import get_store
    store = get_store()
    try:
        store._collection.delete(ids=[pattern_id])
        _report_async()
        return {"status": "ok", "deleted": pattern_id}
    except Exception as e:
        raise HTTPException(500, f"Delete failed: {e}")


def _report_async() -> None:
    try:
        from app.firebase.publish import report_aesthetics_kb
        from concurrent.futures import ThreadPoolExecutor
        ThreadPoolExecutor(max_workers=1).submit(report_aesthetics_kb)
    except Exception:
        pass
