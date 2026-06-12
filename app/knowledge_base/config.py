"""
Knowledge Base configuration.

All values can be overridden via environment variables.
"""

import os

from app.paths import chroma_kb_dir

# ── Storage ──────────────────────────────────────────────────────────────────
# Chroma data dir (derived index — may live on the CHROMA_DATA_ROOT named
# volume). The per-KB env override wins.
CHROMA_PERSIST_DIR = os.environ.get("KB_CHROMA_DIR", "") or str(
    chroma_kb_dir("knowledge")
)
CHROMA_COLLECTION_NAME = os.environ.get("KB_COLLECTION", "enterprise_knowledge")

# ── Embeddings ───────────────────────────────────────────────────────────────
# NOTE: This config value is NOT used for actual embedding computation.
# All embedding goes through app.memory.chromadb_manager.embed() which uses
# Ollama nomic-embed-text (768-dim). This string is kept only for reference.
EMBEDDING_MODEL = os.environ.get("KB_EMBEDDING_MODEL", "nomic-embed-text")

# ── Chunking ─────────────────────────────────────────────────────────────────
CHUNK_SIZE = int(os.environ.get("KB_CHUNK_SIZE", "1000"))
CHUNK_OVERLAP = int(os.environ.get("KB_CHUNK_OVERLAP", "200"))

# ── Retrieval ────────────────────────────────────────────────────────────────
DEFAULT_TOP_K = int(os.environ.get("KB_TOP_K", "6"))
MIN_RELEVANCE_SCORE = float(os.environ.get("KB_MIN_SCORE", "0.3"))

# ── Supported file extensions ────────────────────────────────────────────────
SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".csv",
    ".txt", ".md", ".html", ".htm", ".json",
}
