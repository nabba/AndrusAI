#!/usr/bin/env python3
"""
ingest_wiki_corpus.py — One-shot ingest of wiki/*.md into the 'wiki_corpus'
ChromaDB collection for Torrance originality scoring.

The wiki_corpus serves as the "common/expected response" baseline for
creativity scoring (app/personality/creativity_scoring.py). Semantic
distance from this corpus = originality.

Usage (inside the container):
    python scripts/ingest_wiki_corpus.py

Or from the host:
    docker exec crewai-team-gateway-1 python scripts/ingest_wiki_corpus.py

Idempotent: re-running replaces the collection (each document has a
deterministic ID based on file path + chunk index).
"""
from __future__ import annotations

import hashlib
import logging
import sys
from pathlib import Path

# Ensure app is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

WIKI_DIR = Path("/app/wiki")
# Fallback for running on host (development)
if not WIKI_DIR.exists():
    WIKI_DIR = Path(__file__).resolve().parent.parent / "wiki"

COLLECTION_NAME = "wiki_corpus"
CHUNK_SIZE = 500  # chars per chunk — small to get fine-grained distance
CHUNK_OVERLAP = 50


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks by character count."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunk = text[start:end].strip()
        if len(chunk) >= 50:  # skip tiny fragments
            chunks.append(chunk)
        start = end - overlap
    return chunks


def main() -> None:
    from app.memory.chromadb_manager import store, get_client

    # Gather all wiki markdown
    md_files = sorted(WIKI_DIR.rglob("*.md"))
    if not md_files:
        logger.error(f"No .md files found in {WIKI_DIR}")
        sys.exit(1)

    logger.info(f"Found {len(md_files)} wiki files in {WIKI_DIR}")

    # Clear existing collection for idempotency
    try:
        client = get_client()
        try:
            client.delete_collection(COLLECTION_NAME)
            logger.info(f"Deleted existing '{COLLECTION_NAME}' collection")
        except Exception:
            pass
    except Exception as exc:
        logger.error(f"Cannot connect to ChromaDB: {exc}")
        sys.exit(1)

    total_chunks = 0
    for md_file in md_files:
        try:
            text = md_file.read_text()
        except Exception as exc:
            logger.warning(f"Skipping {md_file}: {exc}")
            continue

        if len(text.strip()) < 100:
            logger.debug(f"Skipping {md_file} (too short)")
            continue

        rel_path = str(md_file.relative_to(WIKI_DIR))
        chunks = chunk_text(text)

        for i, chunk in enumerate(chunks):
            doc_id = hashlib.sha256(f"{rel_path}::{i}".encode()).hexdigest()[:16]
            metadata = {
                "source": rel_path,
                "chunk_index": i,
                "type": "wiki_corpus",
            }
            try:
                store(COLLECTION_NAME, chunk, metadata)
                total_chunks += 1
            except Exception as exc:
                logger.warning(f"Failed to store chunk {i} of {rel_path}: {exc}")

    logger.info(f"Ingested {total_chunks} chunks from {len(md_files)} files into '{COLLECTION_NAME}'")


if __name__ == "__main__":
    main()
