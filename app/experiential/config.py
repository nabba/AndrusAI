"""experiential/config.py — Configuration for the journal/experiential KB."""

from __future__ import annotations

import os

CHROMA_PERSIST_DIR: str = os.environ.get(
    "EXPERIENTIAL_CHROMA_DIR", "/app/workspace/experiential"
)
COLLECTION_NAME: str = "experiential_journal"
ENTRIES_DIR: str = os.environ.get(
    "EXPERIENTIAL_ENTRIES_DIR", "/app/workspace/experiential/entries"
)

# Shorter chunks — journal entries are compact reflections.
CHUNK_SIZE: int = int(os.environ.get("EXPERIENTIAL_CHUNK_SIZE", "800"))
CHUNK_OVERLAP: int = int(os.environ.get("EXPERIENTIAL_CHUNK_OVERLAP", "100"))
CHARS_PER_TOKEN: int = 4
DEFAULT_TOP_K: int = int(os.environ.get("EXPERIENTIAL_TOP_K", "5"))
MIN_RELEVANCE_SCORE: float = float(os.environ.get("EXPERIENTIAL_MIN_SCORE", "0.25"))

# Valid entry types.
ENTRY_TYPES = {
    "task_reflection",
    "creative_insight",
    "error_learning",
    "interaction_narrative",
    "evolution_reflection",
    "unknown",
}

# Valid emotional valences.
VALENCES = {"positive", "neutral", "negative", "mixed"}
