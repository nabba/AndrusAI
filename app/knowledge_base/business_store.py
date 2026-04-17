"""
knowledge_base/business_store.py — Per-business Knowledge Base registry.

Each business/project gets its own ChromaDB collection (`biz_kb_{name}`)
with full isolation from other businesses and from the global enterprise KB.
The same KnowledgeStore class is used — just different collection names.

When a task mentions a business (detected via project_isolation), the
context injection pipeline queries both the global KB and the business KB.

Usage:
    from app.knowledge_base.business_store import get_registry

    registry = get_registry()
    store = registry.get_or_create("plg")
    store.add_document("/path/to/plg_doc.pdf", category="product")
    results = store.query("refund policy")

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import logging
import re
import threading

logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────────────

BUSINESS_KB_PREFIX = "biz_kb_"
# All business KBs share the same persist directory as enterprise KB
# (they're different collections in the same ChromaDB instance).
_DEFAULT_PERSIST_DIR = "/app/workspace/knowledge"


def _sanitize_name(name: str) -> str:
    """Sanitize a business name for use as a ChromaDB collection suffix."""
    clean = re.sub(r"[^a-z0-9_]", "_", name.lower().strip())
    clean = re.sub(r"_+", "_", clean).strip("_")
    return clean[:50] or "unnamed"


def _collection_name(business_name: str) -> str:
    """Build the ChromaDB collection name for a business."""
    return f"{BUSINESS_KB_PREFIX}{_sanitize_name(business_name)}"


# ── Registry ────────────────────────────────────────────────────────────────

class BusinessKBRegistry:
    """Thread-safe registry of per-business KnowledgeStore instances.

    Each business gets its own ChromaDB collection with full isolation.
    Collections are created lazily on first access or eagerly when a
    project is created.
    """

    def __init__(self, persist_dir: str = _DEFAULT_PERSIST_DIR):
        self._persist_dir = persist_dir
        self._stores: dict[str, object] = {}  # business_name -> KnowledgeStore
        self._lock = threading.Lock()

    def get_or_create(self, business_name: str) -> object:
        """Get an existing business KB store, or create one."""
        key = _sanitize_name(business_name)
        if key in self._stores:
            return self._stores[key]

        with self._lock:
            if key in self._stores:
                return self._stores[key]

            store = self._create_store(key)
            self._stores[key] = store
            return store

    def create_store(self, business_name: str) -> object:
        """Eagerly create a business KB store (called on project creation)."""
        return self.get_or_create(business_name)

    def get_store(self, business_name: str) -> object | None:
        """Get an existing store without creating. Returns None if not found."""
        key = _sanitize_name(business_name)
        return self._stores.get(key)

    def _create_store(self, sanitized_name: str) -> object:
        """Create a new KnowledgeStore for a business."""
        from app.knowledge_base.vectorstore import KnowledgeStore

        col_name = f"{BUSINESS_KB_PREFIX}{sanitized_name}"
        store = KnowledgeStore(
            persist_dir=self._persist_dir,
            collection_name=col_name,
        )
        logger.info(
            "BusinessKBRegistry: created store '%s' (%d existing chunks)",
            col_name, store._collection.count(),
        )
        return store

    def list_businesses(self) -> list[dict]:
        """List all known business KBs with stats."""
        results = []
        for name, store in sorted(self._stores.items()):
            try:
                count = store._collection.count()
                stats = store.stats() if hasattr(store, 'stats') else {}
                results.append({
                    "business_name": name,
                    "collection_name": _collection_name(name),
                    "total_chunks": count,
                    "total_documents": stats.get("total_documents", 0),
                    "total_characters": stats.get("total_characters", 0),
                    "categories": stats.get("categories", {}),
                })
            except Exception:
                results.append({
                    "business_name": name,
                    "collection_name": _collection_name(name),
                    "total_chunks": 0,
                })
        return results

    def discover_existing(self) -> list[str]:
        """Discover business KB collections that already exist in ChromaDB.

        Called at startup to populate the registry with pre-existing
        business KBs (e.g., from a previous container run).
        """
        try:
            import chromadb
            client = chromadb.PersistentClient(path=self._persist_dir)
            collections = client.list_collections()
            found = []
            for col in collections:
                if col.name.startswith(BUSINESS_KB_PREFIX):
                    biz_name = col.name[len(BUSINESS_KB_PREFIX):]
                    if biz_name and biz_name not in self._stores:
                        self.get_or_create(biz_name)
                        found.append(biz_name)
            if found:
                logger.info("BusinessKBRegistry: discovered existing KBs: %s", found)
            return found
        except Exception:
            return []

    def query_business(
        self,
        business_name: str,
        question: str,
        top_k: int = 4,
        min_score: float = 0.30,
    ) -> list[dict]:
        """Query a specific business KB. Returns empty list if not found."""
        store = self.get_store(business_name)
        if store is None:
            return []
        try:
            return store.query_reranked(
                question=question, top_k=top_k, min_score=min_score,
            )
        except Exception:
            try:
                return store.query(
                    question=question, top_k=top_k, min_score=min_score,
                )
            except Exception:
                return []


# ── Singleton ───────────────────────────────────────────────────────────────

_registry: BusinessKBRegistry | None = None
_registry_lock = threading.Lock()


def get_registry() -> BusinessKBRegistry:
    """Get or create the singleton BusinessKBRegistry."""
    global _registry
    if _registry is not None:
        return _registry

    with _registry_lock:
        if _registry is not None:
            return _registry
        _registry = BusinessKBRegistry()

        # Discover existing business KBs from ChromaDB.
        try:
            _registry.discover_existing()
        except Exception:
            pass

        # Also discover from project_isolation projects.
        try:
            from app.project_isolation import get_manager
            pm = get_manager()
            for proj in pm.list_projects():
                name = proj.get("name", "")
                if name and name != "default":
                    _registry.get_or_create(name)
        except Exception:
            pass

        return _registry
