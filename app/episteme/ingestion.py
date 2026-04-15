"""
episteme/ingestion.py — Ingestion pipeline for research/metacognitive texts.

Reads .md files with YAML frontmatter, chunks with research-optimized
parameters (larger chunks for argumentative coherence), and ingests into
the dedicated ChromaDB collection.

Frontmatter schema:
    ---
    title: "Attention Is All You Need"
    author: "Vaswani et al."
    paper_type: research_paper
    domain: transformer_architecture
    epistemic_status: empirical
    date: "2017-06-12"
    ---
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from app.episteme import config
from app.episteme.vectorstore import EpistemeStore, get_store

logger = logging.getLogger(__name__)


def extract_frontmatter(content: str) -> tuple[dict, str]:
    """Extract YAML front-matter from .md file."""
    if not content.startswith("---"):
        return {}, content

    end_match = re.search(r"\n---\s*\n", content[3:])
    if not end_match:
        return {}, content

    fm_str = content[3: 3 + end_match.start()]
    body = content[3 + end_match.end():]

    try:
        metadata = yaml.safe_load(fm_str) or {}
    except Exception:
        metadata = {}

    return metadata, body


def chunk_text(
    text: str,
    chunk_size: int = config.CHUNK_SIZE,
    chunk_overlap: int = config.CHUNK_OVERLAP,
    chars_per_token: int = config.CHARS_PER_TOKEN,
) -> list[tuple[str, int]]:
    """Split text into overlapping chunks, respecting heading boundaries.

    Returns list of (chunk_text, char_position) tuples.
    """
    max_chars = chunk_size * chars_per_token
    overlap_chars = chunk_overlap * chars_per_token

    separators = [
        re.compile(r"\n#{1,2}\s"),     # H1, H2
        re.compile(r"\n#{3}\s"),        # H3
        re.compile(r"\n\n\n"),          # Triple newline
        re.compile(r"\n\n"),            # Paragraph break
        re.compile(r"\n"),              # Line break
        re.compile(r"\.\s"),            # Sentence boundary
    ]

    chunks: list[tuple[str, int]] = []
    start = 0

    while start < len(text):
        end = min(start + max_chars, len(text))

        if end < len(text):
            best_split = end
            for sep in separators:
                candidates = list(sep.finditer(text, start + max_chars // 2, end))
                if candidates:
                    best_split = candidates[-1].start()
                    break
            end = best_split

        chunk = text[start:end].strip()
        if chunk:
            chunks.append((chunk, start))

        start = max(start + 1, end - overlap_chars)

    return chunks


def _extract_section(body: str, char_position: int) -> str:
    """Find the nearest preceding heading for a given character position."""
    heading_pattern = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
    last_heading = "Introduction"
    for match in heading_pattern.finditer(body):
        if match.start() > char_position:
            break
        last_heading = match.group(2).strip()
    return last_heading


def ingest_file(
    filepath: Path,
    store: EpistemeStore | None = None,
) -> int:
    """Ingest a single .md file into the episteme vector store."""
    if store is None:
        store = get_store()

    logger.info("Episteme ingestion: processing %s", filepath.name)

    content = filepath.read_text(encoding="utf-8")
    if not content.strip():
        logger.warning("Skipping empty file: %s", filepath.name)
        return 0

    metadata, body = extract_frontmatter(content)

    base_meta = {
        "source_file": filepath.name,
        "author": str(metadata.get("author", "Unknown")),
        "paper_type": str(metadata.get("paper_type", "unknown")),
        "domain": str(metadata.get("domain", "general")),
        "epistemic_status": str(metadata.get("epistemic_status", "theoretical")),
        "date": str(metadata.get("date", "")),
        "title": str(metadata.get("title",
                     filepath.stem.replace("_", " ").replace("-", " ").title())),
    }

    raw_chunks = chunk_text(body)
    if not raw_chunks:
        logger.warning("No chunks produced from: %s", filepath.name)
        return 0

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
        "  -> %d chunks from %s (%s, %s)",
        added, filepath.name, base_meta["author"], base_meta["paper_type"],
    )
    return added


def ingest_text(
    text: str,
    filename: str,
    author: str = "Unknown",
    paper_type: str = "unknown",
    domain: str = "general",
    epistemic_status: str = "theoretical",
    date: str = "",
    title: str = "Unknown",
    store: EpistemeStore | None = None,
) -> int:
    """Ingest raw text directly into the episteme store."""
    if store is None:
        store = get_store()

    if not text.strip():
        return 0

    base_meta = {
        "source_file": filename,
        "author": author,
        "paper_type": paper_type,
        "domain": domain,
        "epistemic_status": epistemic_status,
        "date": date,
        "title": title,
    }

    raw_chunks = chunk_text(text)
    if not raw_chunks:
        return 0

    store.remove_by_source(filename)

    chunks = []
    metadatas = []
    for chunk_content, char_position in raw_chunks:
        section = _extract_section(text, char_position)
        chunk_meta = {**base_meta, "section": section}
        chunks.append(chunk_content)
        metadatas.append(chunk_meta)

    return store.add_documents(chunks=chunks, metadatas=metadatas)


def ingest_directory(
    directory: Path | None = None,
    store: EpistemeStore | None = None,
) -> int:
    """Ingest all .md files from a directory."""
    if directory is None:
        directory = Path(config.TEXTS_DIR)
    if store is None:
        store = get_store()

    directory.mkdir(parents=True, exist_ok=True)

    total = 0
    for filepath in sorted(directory.glob("*.md")):
        try:
            total += ingest_file(filepath, store)
        except Exception as e:
            logger.error("Failed to ingest %s: %s", filepath.name, e)

    logger.info("Episteme ingestion: %d total chunks from %s", total, directory)
    return total
