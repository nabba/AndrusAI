"""episteme/config.py — Configuration for the research/metacognitive KB."""

from __future__ import annotations

import os

CHROMA_PERSIST_DIR: str = os.environ.get(
    "EPISTEME_CHROMA_DIR", "/app/workspace/episteme"
)
COLLECTION_NAME: str = "episteme_research"
TEXTS_DIR: str = os.environ.get(
    "EPISTEME_TEXTS_DIR", "/app/workspace/episteme/texts"
)

CHUNK_SIZE: int = int(os.environ.get("EPISTEME_CHUNK_SIZE", "1200"))
CHUNK_OVERLAP: int = int(os.environ.get("EPISTEME_CHUNK_OVERLAP", "200"))
CHARS_PER_TOKEN: int = 4
DEFAULT_TOP_K: int = int(os.environ.get("EPISTEME_TOP_K", "5"))
MIN_RELEVANCE_SCORE: float = float(os.environ.get("EPISTEME_MIN_SCORE", "0.30"))
MAX_UPLOAD_SIZE: int = 10 * 1024 * 1024  # 10 MB

# Valid paper types for metadata filtering.
PAPER_TYPES = {
    "research_paper",
    "architecture_decision",
    "design_pattern",
    "failed_experiment",
    "methodology",
    "survey",
    "unknown",
}
