"""
Philosophy RAG configuration.

All values can be overridden via environment variables.
"""

import os

# ── Storage ──────────────────────────────────────────────────────────────────
CHROMA_PERSIST_DIR = os.environ.get("PHIL_CHROMA_DIR", "/app/workspace/philosophy")
COLLECTION_NAME = os.environ.get("PHIL_COLLECTION", "philosophy_humanist")
TEXTS_DIR = os.environ.get("PHIL_TEXTS_DIR", "/app/workspace/philosophy/texts")

# ── Chunking (optimized for philosophical/argumentative text) ────────────────
# Larger chunks than enterprise KB — philosophical arguments span paragraphs
# and lose coherence when split too small.
CHUNK_SIZE = int(os.environ.get("PHIL_CHUNK_SIZE", "1200"))     # tokens
CHUNK_OVERLAP = int(os.environ.get("PHIL_CHUNK_OVERLAP", "200"))  # tokens
CHARS_PER_TOKEN = 4  # rough approximation for English prose

# ── Retrieval ────────────────────────────────────────────────────────────────
DEFAULT_TOP_K = int(os.environ.get("PHIL_TOP_K", "5"))
MIN_RELEVANCE_SCORE = float(os.environ.get("PHIL_MIN_SCORE", "0.3"))

# ── Upload limits ────────────────────────────────────────────────────────────
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB
