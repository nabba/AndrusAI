"""Knowledge base module for vector storage and retrieval."""

# Import config first to avoid circular dependency
from app.knowledge_base import config

# Only import KnowledgeStore after config is loaded
from app.knowledge_base.vectorstore import KnowledgeStore

__all__ = ["KnowledgeStore", "config"]
