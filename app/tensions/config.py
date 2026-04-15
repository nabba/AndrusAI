"""tensions/config.py — Configuration for the contradictions/tensions KB."""

from __future__ import annotations

import os

CHROMA_PERSIST_DIR: str = os.environ.get(
    "TENSIONS_CHROMA_DIR", "/app/workspace/tensions"
)
COLLECTION_NAME: str = "unresolved_tensions"
ENTRIES_DIR: str = os.environ.get(
    "TENSIONS_ENTRIES_DIR", "/app/workspace/tensions/entries"
)

CHUNK_SIZE: int = int(os.environ.get("TENSIONS_CHUNK_SIZE", "1000"))
CHUNK_OVERLAP: int = int(os.environ.get("TENSIONS_CHUNK_OVERLAP", "150"))
CHARS_PER_TOKEN: int = 4
DEFAULT_TOP_K: int = int(os.environ.get("TENSIONS_TOP_K", "5"))
MIN_RELEVANCE_SCORE: float = float(os.environ.get("TENSIONS_MIN_SCORE", "0.25"))

TENSION_TYPES = {
    "principle_conflict",
    "philosophy_vs_experience",
    "competing_values",
    "unresolved_question",
    "unknown",
}

RESOLUTION_STATUSES = {"unresolved", "partially_resolved", "dissolved"}
