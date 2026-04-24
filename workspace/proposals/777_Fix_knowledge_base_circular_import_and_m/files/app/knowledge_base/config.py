"""Configuration for knowledge base module."""

# Database configuration
DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/knowledge_base"

# Embedding model settings
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384

# Vector store settings
COLLECTION_NAME = "knowledge_base"
DISTANCE_METRIC = "cosine"

# Chunking settings
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 200
