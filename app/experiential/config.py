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
# task_reflection..evolution_reflection: pre-existing per-task reflections.
# episode/chapter/arc/epoch: Narrative-Self pipeline (Apr 2026).
#   episode  — synthesized from a cluster of salience events
#   chapter  — daily consolidation of episodes
#   arc      — weekly rollup (reserved; not yet emitted)
#   epoch    — monthly rollup (reserved; not yet emitted)
ENTRY_TYPES = {
    "task_reflection",
    "creative_insight",
    "error_learning",
    "interaction_narrative",
    "evolution_reflection",
    "episode",
    "chapter",
    "arc",
    "epoch",
    "unknown",
}

# Valid emotional valences.
VALENCES = {"positive", "neutral", "negative", "mixed"}
