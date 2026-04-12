"""
Philosophy Text Ingestion Pipeline
====================================
Reads .md files with YAML frontmatter, chunks with philosophy-optimized
parameters (larger chunks, heading-aware splitting), and ingests into the
dedicated ChromaDB collection.

Metadata schema per chunk:
  source_file, author, tradition, era, title, section
"""

import logging
import re
from pathlib import Path

import yaml

from app.philosophy import config
from app.philosophy.vectorstore import PhilosophyStore, get_store

logger = logging.getLogger(__name__)

# Separator hierarchy: prefer splitting on structural boundaries
SEPARATORS = [
    "\n# ",       # H1 headings
    "\n## ",      # H2 headings
    "\n### ",     # H3 headings
    "\n\n\n",     # Triple newline (major section break)
    "\n\n",       # Paragraph boundary (most common split point)
    "\n",         # Line break (fallback)
    ". ",         # Sentence boundary (last resort)
]

def extract_frontmatter(content: str) -> tuple[dict, str]:
    """Extract YAML frontmatter from markdown content.

    Returns (metadata_dict, remaining_content).
    """
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if match:
        try:
            metadata = yaml.safe_load(match.group(1))
            if not isinstance(metadata, dict):
                metadata = {}
        except yaml.YAMLError as e:
            logger.warning(f"Failed to parse YAML frontmatter: {e}")
            metadata = {}
        return metadata, content[match.end():]
    return {}, content

def _extract_section(text: str, position: int) -> str:
    """Find the nearest Markdown heading before position."""
    preceding = text[:position]
    headings = list(re.finditer(r"^#{1,3}\s+(.+)$", preceding, re.MULTILINE))
    return headings[-1].group(1).strip() if headings else "Introduction"

def chunk_text(
    text: str,
    chunk_size_tokens: int = config.CHUNK_SIZE,
    overlap_tokens: int = config.CHUNK_OVERLAP,
) -> list[tuple[str, int]]:
    """Split text into chunks optimized for philosophical content.

    Uses hierarchical separator strategy: headings → paragraphs → sentences.
    Returns list of (chunk_text, start_char_position) tuples.
    """
    chunk_size_chars = chunk_size_tokens * config.CHARS_PER_TOKEN
    overlap_chars = overlap_tokens * config.CHARS_PER_TOKEN

    if len(text) <= chunk_size_chars:
        return [(text.strip(), 0)] if text.strip() else []

    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size_chars

        if end >= len(text):
            chunk = text[start:].strip()
            if chunk:
                chunks.append((chunk, start))
            break

        # Find best split point using separator hierarchy
        best_split = None
        for separator in SEPARATORS:
            search_start = start + int(chunk_size_chars * 0.7)
            search_region = text[search_start:end]
            sep_pos = search_region.rfind(separator)
            if sep_pos != -1:
                best_split = search_start + sep_pos + len(separator)
                break

        if best_split is None or best_split <= start:
            best_split = end

        chunk = text[start:best_split].strip()
        if chunk:
            chunks.append((chunk, start))

        # Advance start with overlap, ensuring we always move forward
        prev_start = start
        start = best_split - overlap_chars
        if start < 0:
            start = 0
        # CRITICAL: start must advance past previous start to avoid infinite loop
        # This can happen when overlap_chars > chunk_size_chars
        if start <= prev_start:
            start = best_split

    return chunks

def ingest_file(
    filepath: Path,
    store: PhilosophyStore | None = None,
) -> int:
    """Ingest a single .md file into the philosophy vector store.

    Returns number of chunks ingested.
    """
    if store is None:
        store = get_store()

    logger.info(f"Philosophy ingestion: processing {filepath.name}")

    content = filepath.read_text(encoding="utf-8")
    if not content.strip():
        logger.warning(f"Skipping empty file: {filepath.name}")
        return 0

    metadata, body = extract_frontmatter(content)

    base_meta = {
        "source_file": filepath.name,
        "author": str(metadata.get("author", "Unknown")),
        "tradition": str(metadata.get("tradition", "Unknown")),
        "era": str(metadata.get("era", "Unknown")),
        "title": str(metadata.get("title",
                     filepath.stem.replace("_", " ").replace("-", " ").title())),
    }

    raw_chunks = chunk_text(body)
    if not raw_chunks:
        logger.warning(f"No chunks produced from: {filepath.name}")
        return 0

    # Remove existing chunks from this source before re-ingesting
    store.remove_by_source(filepath.name)

    chunks = []
    metadatas = []
    for chunk_content, char_position in raw_chunks:
        section = _extract_section(body, char_position)
        chunk_meta = {**base_meta, "section": section}
        chunks.append(chunk_content)
        metadatas.append(chunk_meta)

    added = store.add_documents(chunks=chunks, metadatas=metadatas)
    logger.info(
        f"  → {added} chunks from {filepath.name} "
        f"({base_meta['author']}, {base_meta['tradition']})"
    )
    return added

def ingest_text(
    text: str,
    filename: str,
    author: str = "Unknown",
    tradition: str = "Unknown",
    era: str = "Unknown",
    title: str = "Unknown",
    store: PhilosophyStore | None = None,
) -> int:
    """Ingest raw markdown text directly (from dashboard upload).

    If the text has YAML frontmatter, it takes precedence over the
    explicit arguments.  Returns number of chunks ingested.
    """
    if store is None:
        store = get_store()

    metadata, body = extract_frontmatter(text)

    base_meta = {
        "source_file": filename,
        "author": str(metadata.get("author", author)),
        "tradition": str(metadata.get("tradition", tradition)),
        "era": str(metadata.get("era", era)),
        "title": str(metadata.get("title", title)),
    }

    raw_chunks = chunk_text(body)
    if not raw_chunks:
        return 0

    store.remove_by_source(filename)

    chunks = []
    metadatas = []
    for chunk_content, char_position in raw_chunks:
        section = _extract_section(body, char_position)
        chunk_meta = {**base_meta, "section": section}
        chunks.append(chunk_content)
        metadatas.append(chunk_meta)

    return store.add_documents(chunks=chunks, metadatas=metadatas)

def ingest_directory(
    texts_dir: Path | None = None,
    store: PhilosophyStore | None = None,
) -> dict:
    """Ingest all .md files from the texts directory.

    Returns summary dict.
    """
    if texts_dir is None:
        texts_dir = Path(config.TEXTS_DIR)
    if store is None:
        store = get_store()

    md_files = sorted(texts_dir.glob("*.md"))
    md_files = [f for f in md_files if f.name.upper() != "README.MD"]

    if not md_files:
        return {"files_processed": 0, "total_chunks": 0, "errors": []}

    logger.info(f"Philosophy ingestion: found {len(md_files)} .md files in {texts_dir}")

    total_chunks = 0
    errors = []

    for filepath in md_files:
        try:
            added = ingest_file(filepath, store)
            total_chunks += added
        except Exception as e:
            msg = f"Failed to ingest {filepath.name}: {e}"
            logger.error(msg)
            errors.append(msg)

    summary = {
        "files_processed": len(md_files) - len(errors),
        "files_failed": len(errors),
        "total_chunks": total_chunks,
        "errors": errors,
    }

    logger.info(
        f"Philosophy ingestion complete: {summary['files_processed']} files, "
        f"{summary['total_chunks']} chunks, {summary['files_failed']} errors"
    )
    return summary
