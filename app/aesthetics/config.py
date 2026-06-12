"""aesthetics/config.py — Configuration for the aesthetic pattern library."""

from __future__ import annotations

import os

from app.paths import chroma_kb_dir

# Chroma data dir (derived index — may live on the CHROMA_DATA_ROOT named
# volume). The per-KB env override wins; patterns stay on the workspace mount.
CHROMA_PERSIST_DIR: str = os.environ.get(
    "AESTHETICS_CHROMA_DIR", ""
) or str(chroma_kb_dir("aesthetics"))
COLLECTION_NAME: str = "aesthetic_patterns"
PATTERNS_DIR: str = os.environ.get(
    "AESTHETICS_PATTERNS_DIR", "/app/workspace/aesthetics/patterns"
)

CHUNK_SIZE: int = int(os.environ.get("AESTHETICS_CHUNK_SIZE", "1000"))
CHUNK_OVERLAP: int = int(os.environ.get("AESTHETICS_CHUNK_OVERLAP", "150"))
CHARS_PER_TOKEN: int = 4
DEFAULT_TOP_K: int = int(os.environ.get("AESTHETICS_TOP_K", "5"))
MIN_RELEVANCE_SCORE: float = float(os.environ.get("AESTHETICS_MIN_SCORE", "0.25"))

PATTERN_TYPES = {
    "elegant_code",
    "beautiful_prose",
    "well_structured_argument",
    "creative_solution",
    "unknown",
}
