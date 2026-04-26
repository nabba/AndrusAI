"""
kb_dedup.py — Shared duplicate-detection helper for KB upload paths.

User experience goal: re-uploading the same document MUST be a no-op or
an explicit "you already have this" error, never a silent re-ingest
that doubles the chunk count and pollutes search results.

Two layers of detection:

  1. **Filename collision** — fastest signal. If a file with the same
     name already exists (in the KB's texts directory or as a
     ``source_file`` metadata value in its ChromaDB collection),
     it's a duplicate.

  2. **Content hash collision** — catches renames. SHA-256 of the
     uploaded bytes is compared against the hashes of stored files
     and against ``content_hash`` metadata in chunk records (when
     present — older ingests didn't write this field, so the hash
     check only catches duplicates ingested AFTER this module was
     deployed).

Each upload handler calls ``find_duplicate(...)`` before invoking the
ingest function, then either:
  * Raises 409 Conflict with structured detail → user sees a clear
    error and can choose to overwrite explicitly.
  * Honors ``overwrite=true`` → removes the existing record(s) before
    ingesting the new copy.

Going forward, ingest functions also pass ``content_hash`` into chunk
metadata (helper function ``hash_for_metadata``) so future hash-based
checks work even when filenames change.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Hash helpers ──────────────────────────────────────────────────────────

def compute_content_hash(content: bytes) -> str:
    """Return the SHA-256 hex digest of ``content``.

    Stable across runs — same bytes → same hash forever. Used both
    for the duplicate check and for storing in chunk metadata so
    later uploads can be matched by content even after rename.
    """
    return hashlib.sha256(content).hexdigest()


def hash_for_metadata(content: bytes) -> dict[str, str]:
    """Helper for ingest functions: returns the metadata fields to
    embed in every chunk so future dedup checks can match by hash.

    Usage in a chunk-construction loop::

        meta = {
            **hash_for_metadata(file_bytes),
            "source_file": filename,
            ...
        }
    """
    return {"content_hash": compute_content_hash(content)}


# ── Duplicate result shape ────────────────────────────────────────────────

@dataclass
class DuplicateMatch:
    """Structured 409 detail. Serializable to JSON via ``as_detail()``."""
    matched_by: str           # "filename" or "content_hash"
    existing_filename: str
    added_at: str | None      # ISO timestamp if known
    hint: str                 # actionable user message

    def as_detail(self) -> dict[str, Any]:
        return {
            "error": "duplicate",
            **asdict(self),
        }


# ── Duplicate lookup ──────────────────────────────────────────────────────

def find_duplicate(
    *,
    new_content: bytes,
    new_filename: str,
    existing_files_dir: Path | None = None,
    collection: Any = None,
    filename_meta_key: str = "source_file",
) -> DuplicateMatch | None:
    """Look for an existing document that matches the upload.

    Args:
        new_content: bytes of the upload (used to compute hash + compare
            against existing file bodies).
        new_filename: sanitized destination filename (used for the
            filename-collision check).
        existing_files_dir: optional Path to the directory the KB stores
            its files in (philosophy/episteme/fiction). When set, we
            do a fast filesystem-level filename + content check.
        collection: optional ChromaDB Collection (any KB that doesn't
            keep files on disk, like the main /kb/upload path which
            uses a tmpfile + ingest).
        filename_meta_key: which metadata key holds the source filename
            in this collection's chunks. KB store uses "source",
            most others use "source_file".

    Returns a DuplicateMatch when an existing copy is found, else None.
    Errors during lookup are caught and treated as "no duplicate" —
    we never block an upload because the dedup check itself failed.
    """
    if not new_filename or not new_content:
        return None
    new_hash = compute_content_hash(new_content)

    # ── Layer 1: filesystem-level filename check ─────────────────
    if existing_files_dir is not None:
        try:
            existing_path = existing_files_dir / new_filename
            if existing_path.exists():
                try:
                    existing_hash = compute_content_hash(existing_path.read_bytes())
                    matched_by = "content_hash" if existing_hash == new_hash else "filename"
                except Exception:
                    matched_by = "filename"
                added_at = None
                try:
                    added_at = datetime.fromtimestamp(
                        existing_path.stat().st_mtime, tz=timezone.utc,
                    ).isoformat()
                except Exception:
                    pass
                return DuplicateMatch(
                    matched_by=matched_by,
                    existing_filename=new_filename,
                    added_at=added_at,
                    hint="A document with this name already exists. "
                         "Re-upload with overwrite=true to replace, or "
                         "rename the file first.",
                )
        except Exception as exc:
            logger.debug("dedup: filesystem check failed: %s", exc)

    # ── Layer 2: ChromaDB metadata check ──────────────────────────
    if collection is not None:
        try:
            # Filename match — first lookup
            res = collection.get(
                where={filename_meta_key: new_filename},
                limit=1, include=["metadatas"],
            )
            metas = res.get("metadatas") or []
            if metas:
                m = metas[0] or {}
                return DuplicateMatch(
                    matched_by="filename",
                    existing_filename=str(m.get(filename_meta_key, new_filename)),
                    added_at=(
                        m.get("ingested_at")
                        or m.get("created_at")
                        or m.get("added_at")
                    ),
                    hint="A document with this name is already in the "
                         "knowledge base. Re-upload with overwrite=true "
                         "to replace, or rename the file first.",
                )
        except Exception as exc:
            logger.debug("dedup: collection filename lookup failed: %s", exc)

        try:
            # Content-hash match — catches renames (only works for
            # documents ingested after this module landed; older
            # chunks don't have content_hash in metadata).
            res = collection.get(
                where={"content_hash": new_hash},
                limit=1, include=["metadatas"],
            )
            metas = res.get("metadatas") or []
            if metas:
                m = metas[0] or {}
                return DuplicateMatch(
                    matched_by="content_hash",
                    existing_filename=str(
                        m.get(filename_meta_key) or m.get("source") or "(renamed)"
                    ),
                    added_at=(
                        m.get("ingested_at")
                        or m.get("created_at")
                        or m.get("added_at")
                    ),
                    hint="The same content (different filename) is "
                         "already in the knowledge base. Re-upload "
                         "with overwrite=true to replace, or use the "
                         "existing copy.",
                )
        except Exception as exc:
            logger.debug("dedup: collection hash lookup failed: %s", exc)

    return None
