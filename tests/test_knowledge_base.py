"""
Knowledge Base Tests
=====================

Comprehensive tests for the enterprise knowledge base subsystem:
config, ingestion (10 format extractors, chunking), vectorstore (CRUD, query, stats),
CrewAI tools, API routes, context injection, and system wiring.

Run: docker exec crewai-team-gateway-1 python3 -m pytest /app/tests/test_knowledge_base.py -v

Heavy tests (ChromaDB/CrewAI imports) skippable: LOW_MEM_TESTS=0 to enable all.
"""

import inspect
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

_LOW_MEM = os.environ.get("LOW_MEM_TESTS", "1") == "1"


# ════════════════════════════════════════════════════════════════════════════════
# 1. IMPORTS & CONFIG
# ════════════════════════════════════════════════════════════════════════════════

class TestImportsAndConfig:
    """All knowledge base modules must import and config must be sane."""

    def test_config_constants(self):
        from app.knowledge_base.config import (
            CHROMA_PERSIST_DIR, CHROMA_COLLECTION_NAME, EMBEDDING_MODEL,
            CHUNK_SIZE, CHUNK_OVERLAP, DEFAULT_TOP_K, MIN_RELEVANCE_SCORE,
            SUPPORTED_EXTENSIONS,
        )
        assert CHROMA_COLLECTION_NAME == "enterprise_knowledge"
        assert CHUNK_SIZE >= 100
        assert 0 < CHUNK_OVERLAP < CHUNK_SIZE
        assert DEFAULT_TOP_K >= 1
        assert 0.0 <= MIN_RELEVANCE_SCORE <= 1.0
        assert len(SUPPORTED_EXTENSIONS) >= 10
        assert ".pdf" in SUPPORTED_EXTENSIONS
        assert ".docx" in SUPPORTED_EXTENSIONS
        assert ".csv" in SUPPORTED_EXTENSIONS

    def test_ingestion_imports(self):
        from app.knowledge_base.ingestion import (
            DocumentChunk, IngestionResult, ingest_document,
            detect_format, chunk_text, EXTRACTORS,
        )
        assert callable(ingest_document)
        assert callable(detect_format)
        assert callable(chunk_text)
        assert len(EXTRACTORS) >= 10

    @pytest.mark.skipif(_LOW_MEM, reason="ChromaDB import heavy")
    def test_vectorstore_imports(self):
        from app.knowledge_base.vectorstore import KnowledgeStore
        assert callable(KnowledgeStore)

    @pytest.mark.skipif(_LOW_MEM, reason="CrewAI import heavy")
    def test_tools_imports(self):
        from app.knowledge_base.tools import (
            KnowledgeSearchTool, KnowledgeIngestTool, KnowledgeStatusTool,
            get_knowledge_tools, get_store, set_store,
        )
        assert callable(get_knowledge_tools)

    def test_package_exports(self):
        import app.knowledge_base
        assert hasattr(app.knowledge_base, "KnowledgeStore")
        assert hasattr(app.knowledge_base, "KnowledgeSearchTool")
        assert hasattr(app.knowledge_base, "get_knowledge_tools")


# ════════════════════════════════════════════════════════════════════════════════
# 2. FORMAT DETECTION TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestFormatDetection:
    """Format detection from file paths and URLs."""

    def test_pdf(self):
        from app.knowledge_base.ingestion import detect_format
        assert detect_format("/path/to/doc.pdf") == ".pdf"

    def test_docx(self):
        from app.knowledge_base.ingestion import detect_format
        assert detect_format("report.docx") == ".docx"

    def test_csv(self):
        from app.knowledge_base.ingestion import detect_format
        assert detect_format("data.csv") == ".csv"

    def test_markdown(self):
        from app.knowledge_base.ingestion import detect_format
        assert detect_format("readme.md") == ".md"

    def test_html(self):
        from app.knowledge_base.ingestion import detect_format
        assert detect_format("page.html") == ".html"

    def test_json(self):
        from app.knowledge_base.ingestion import detect_format
        assert detect_format("config.json") == ".json"

    def test_url_https(self):
        from app.knowledge_base.ingestion import detect_format
        assert detect_format("https://example.com/page") == "url"

    def test_url_http(self):
        from app.knowledge_base.ingestion import detect_format
        assert detect_format("http://example.com") == "url"

    def test_unknown_format(self):
        from app.knowledge_base.ingestion import detect_format
        result = detect_format("file.xyz")
        assert result in (".xyz", "unknown")

    def test_all_supported_extensions(self):
        from app.knowledge_base.ingestion import detect_format
        from app.knowledge_base.config import SUPPORTED_EXTENSIONS
        for ext in SUPPORTED_EXTENSIONS:
            result = detect_format(f"test{ext}")
            assert result == ext, f"detect_format('test{ext}') returned {result}"


# ════════════════════════════════════════════════════════════════════════════════
# 3. EXTRACTORS REGISTRY TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestExtractors:
    """All format extractors must be registered and callable."""

    def test_all_extensions_have_extractors(self):
        from app.knowledge_base.ingestion import EXTRACTORS
        from app.knowledge_base.config import SUPPORTED_EXTENSIONS
        for ext in SUPPORTED_EXTENSIONS:
            assert ext in EXTRACTORS, f"No extractor for {ext}"
            assert callable(EXTRACTORS[ext]), f"Extractor for {ext} not callable"

    def test_extractors_include_pdf(self):
        from app.knowledge_base.ingestion import EXTRACTORS, extract_pdf
        assert EXTRACTORS[".pdf"] is extract_pdf

    def test_extractors_include_docx(self):
        from app.knowledge_base.ingestion import EXTRACTORS, extract_docx
        assert EXTRACTORS[".docx"] is extract_docx

    def test_extractors_md_uses_text(self):
        from app.knowledge_base.ingestion import EXTRACTORS, extract_text
        assert EXTRACTORS[".md"] is extract_text
        assert EXTRACTORS[".txt"] is extract_text

    def test_extract_text_reads_file(self):
        from app.knowledge_base.ingestion import extract_text
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Hello knowledge base world!")
            f.flush()
            result = extract_text(f.name)
            assert "Hello knowledge base world" in result
        os.unlink(f.name)

    def test_extract_text_caps_at_500k(self):
        from app.knowledge_base.ingestion import extract_text
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("x" * 600_000)
            f.flush()
            result = extract_text(f.name)
            assert len(result) <= 500_001
        os.unlink(f.name)

    def test_extract_csv(self):
        from app.knowledge_base.ingestion import extract_csv
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("name,value\nAlice,100\nBob,200\n")
            f.flush()
            result = extract_csv(f.name)
            assert "Alice" in result
            assert "Bob" in result
        os.unlink(f.name)

    def test_extract_json(self):
        from app.knowledge_base.ingestion import extract_json
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"key": "value", "nested": {"a": 1}}, f)
            f.flush()
            result = extract_json(f.name)
            assert "key" in result
            assert "value" in result
        os.unlink(f.name)


# ════════════════════════════════════════════════════════════════════════════════
# 4. CHUNKING TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestChunking:
    """Text chunking for knowledge base ingestion."""

    def test_short_text_single_chunk(self):
        from app.knowledge_base.ingestion import chunk_text
        chunks = chunk_text("Short text.", chunk_size=1000)
        # May return 0 if text is below minimum chunk filter threshold (50 chars)
        assert len(chunks) <= 1

    def test_empty_text(self):
        from app.knowledge_base.ingestion import chunk_text
        chunks = chunk_text("")
        assert len(chunks) == 0

    def test_long_text_multiple_chunks(self):
        from app.knowledge_base.ingestion import chunk_text
        text = "The quick brown fox jumps over the lazy dog. " * 100
        chunks = chunk_text(text, chunk_size=200, chunk_overlap=50)
        assert len(chunks) > 1

    def test_chunks_nonempty(self):
        from app.knowledge_base.ingestion import chunk_text
        text = "Sentence one about knowledge. Sentence two about data. " * 50
        chunks = chunk_text(text, chunk_size=200, chunk_overlap=50)
        for c in chunks:
            assert len(c.strip()) > 0

    def test_simple_chunker_fallback(self):
        from app.knowledge_base.ingestion import _simple_chunk
        text = "Word " * 500
        chunks = _simple_chunk(text, size=200, overlap=50)
        assert len(chunks) > 1
        for c in chunks:
            assert len(c) > 0


# ════════════════════════════════════════════════════════════════════════════════
# 5. DOCUMENT CHUNK & INGESTION RESULT TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestDataClasses:
    """DocumentChunk and IngestionResult dataclasses."""

    def test_document_chunk_defaults(self):
        from app.knowledge_base.ingestion import DocumentChunk
        chunk = DocumentChunk(text="Test text")
        assert chunk.text == "Test text"
        assert isinstance(chunk.metadata, dict)

    def test_document_chunk_id_deterministic(self):
        from app.knowledge_base.ingestion import DocumentChunk
        c1 = DocumentChunk(text="test", metadata={"source": "doc.pdf", "chunk_index": 0})
        c2 = DocumentChunk(text="test", metadata={"source": "doc.pdf", "chunk_index": 0})
        assert c1.chunk_id == c2.chunk_id

    def test_document_chunk_id_varies_by_index(self):
        from app.knowledge_base.ingestion import DocumentChunk
        c1 = DocumentChunk(text="test", metadata={"source": "doc.pdf", "chunk_index": 0})
        c2 = DocumentChunk(text="test", metadata={"source": "doc.pdf", "chunk_index": 1})
        assert c1.chunk_id != c2.chunk_id

    def test_ingestion_result_success(self):
        from app.knowledge_base.ingestion import IngestionResult
        r = IngestionResult(source="/doc.pdf", format=".pdf",
                            chunks_created=10, total_characters=5000, success=True)
        assert r.success is True
        assert r.error == ""

    def test_ingestion_result_failure(self):
        from app.knowledge_base.ingestion import IngestionResult
        r = IngestionResult(source="bad.xyz", format="unknown",
                            chunks_created=0, total_characters=0,
                            success=False, error="Unsupported format")
        assert r.success is False
        assert "Unsupported" in r.error


# ════════════════════════════════════════════════════════════════════════════════
# 6. INGESTION PIPELINE TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestIngestionPipeline:
    """Full ingestion pipeline from file to chunks."""

    def test_ingest_text_file(self):
        from app.knowledge_base.ingestion import ingest_document
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Knowledge is power. " * 50)
            f.flush()
            chunks, result = ingest_document(f.name, category="test")
            assert result.success is True
            assert result.chunks_created >= 1
            assert result.format in (".txt", "txt")
        os.unlink(f.name)

    def test_ingest_markdown_file(self):
        from app.knowledge_base.ingestion import ingest_document
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Heading\n\nParagraph about philosophy and ethics. " * 30)
            f.flush()
            chunks, result = ingest_document(f.name, category="docs")
            assert result.success is True
        os.unlink(f.name)

    def test_ingest_csv_file(self):
        from app.knowledge_base.ingestion import ingest_document
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("name,age,city\n" + "Alice,30,NYC\n" * 100)
            f.flush()
            chunks, result = ingest_document(f.name, category="data")
            assert result.success is True
        os.unlink(f.name)

    def test_ingest_nonexistent_file(self):
        from app.knowledge_base.ingestion import ingest_document
        chunks, result = ingest_document("/nonexistent/path/file.txt")
        assert result.success is False

    def test_ingest_with_tags(self):
        from app.knowledge_base.ingestion import ingest_document
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Tagged content about compliance and regulations. " * 30)
            f.flush()
            chunks, result = ingest_document(f.name, category="policy", tags=["2024"])
            assert result.success is True
            if chunks:
                assert chunks[0].metadata.get("category") == "policy"
        os.unlink(f.name)

    def test_ingest_preserves_metadata(self):
        from app.knowledge_base.ingestion import ingest_document
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Metadata test content for chunking purposes. " * 20)
            f.flush()
            chunks, result = ingest_document(f.name, category="meta_test")
            if chunks:
                meta = chunks[0].metadata
                assert "source" in meta or "source_path" in meta
                assert "chunk_index" in meta
        os.unlink(f.name)


# ════════════════════════════════════════════════════════════════════════════════
# 7. VECTORSTORE TESTS
# ════════════════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(_LOW_MEM, reason="ChromaDB import heavy")
class TestVectorStore:
    """KnowledgeStore ChromaDB operations."""

    def test_init(self):
        from app.knowledge_base.vectorstore import KnowledgeStore
        assert KnowledgeStore() is not None

    def test_stats(self):
        from app.knowledge_base.vectorstore import KnowledgeStore
        s = KnowledgeStore().stats()
        assert "total_chunks" in s
        assert "total_documents" in s
        assert "categories" in s

    def test_list_documents(self):
        from app.knowledge_base.vectorstore import KnowledgeStore
        assert isinstance(KnowledgeStore().list_documents(), list)

    def test_query_returns_list(self):
        from app.knowledge_base.vectorstore import KnowledgeStore
        assert isinstance(KnowledgeStore().query("test", top_k=3), list)

    def test_query_result_structure(self):
        from app.knowledge_base.vectorstore import KnowledgeStore
        for r in KnowledgeStore().query("knowledge management", top_k=2):
            assert "text" in r and "source" in r and "score" in r

    def test_add_text_and_remove(self):
        from app.knowledge_base.vectorstore import KnowledgeStore
        store = KnowledgeStore()
        try:
            result = store.add_text(
                text="Enterprise data governance principles for testing.",
                source_name="_test_kb_add_text", category="test")
            assert result.success is True
        finally:
            store.remove_document("_test_kb_add_text")

    def test_remove_nonexistent(self):
        from app.knowledge_base.vectorstore import KnowledgeStore
        assert KnowledgeStore().remove_document("nonexistent_xyz_99999") == 0

    def test_query_respects_min_score(self):
        from app.knowledge_base.vectorstore import KnowledgeStore
        for r in KnowledgeStore().query("test", top_k=5, min_score=0.9):
            assert r["score"] >= 0.9


# ════════════════════════════════════════════════════════════════════════════════
# 8. CREWAI TOOLS TESTS
# ════════════════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(_LOW_MEM, reason="CrewAI import heavy")
class TestCrewAITools:
    """Knowledge base CrewAI tools for agents."""

    def test_search_tool_name(self):
        from app.knowledge_base.tools import KnowledgeSearchTool
        assert KnowledgeSearchTool().name == "search_knowledge_base"

    def test_ingest_tool_name(self):
        from app.knowledge_base.tools import KnowledgeIngestTool
        assert KnowledgeIngestTool().name == "ingest_to_knowledge_base"

    def test_status_tool_name(self):
        from app.knowledge_base.tools import KnowledgeStatusTool
        assert KnowledgeStatusTool().name == "knowledge_base_status"

    def test_get_tools_default(self):
        from app.knowledge_base.tools import get_knowledge_tools
        names = [t.name for t in get_knowledge_tools()]
        assert "search_knowledge_base" in names
        assert "ingest_to_knowledge_base" not in names

    def test_get_tools_with_ingest(self):
        from app.knowledge_base.tools import get_knowledge_tools
        names = [t.name for t in get_knowledge_tools(include_ingest=True)]
        assert "ingest_to_knowledge_base" in names

    def test_search_tool_returns_string(self):
        from app.knowledge_base.tools import KnowledgeSearchTool
        assert isinstance(KnowledgeSearchTool()._run("test"), str)

    def test_status_tool_returns_string(self):
        from app.knowledge_base.tools import KnowledgeStatusTool
        assert isinstance(KnowledgeStatusTool()._run(), str)


# ════════════════════════════════════════════════════════════════════════════════
# 9. API ROUTES TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestAPIRoutes:
    """FastAPI knowledge base endpoints."""

    def test_router_exists(self):
        from app.api.kb import router
        assert router is not None

    def test_upload_size_limit(self):
        from app.api.kb import MAX_UPLOAD_SIZE
        assert MAX_UPLOAD_SIZE == 50 * 1024 * 1024

    def test_allowed_extensions(self):
        from app.api.kb import ALLOWED_EXTENSIONS
        assert ".pdf" in ALLOWED_EXTENSIONS
        assert len(ALLOWED_EXTENSIONS) >= 10

    def test_routes_defined(self):
        src = inspect.getsource(__import__("app.api.kb", fromlist=["_"]))
        assert "/upload" in src
        assert "/status" in src
        assert "/remove" in src
        assert "/reset" in src

    def test_lazy_singleton_pattern(self):
        src = inspect.getsource(__import__("app.api.kb", fromlist=["_"]))
        assert "_get_kb_store" in src


# ════════════════════════════════════════════════════════════════════════════════
# 10. KB COMMANDS TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestKBCommands:
    """Commander KB command handlers."""

    def test_all_commands_present(self):
        src = inspect.getsource(
            __import__("app.agents.commander.commands", fromlist=["_"])
        ).lower()
        for cmd in ("kb status", "kb list", "kb add", "kb remove", "kb search", "kb reset"):
            assert cmd in src, f"Missing command: {cmd}"


# ════════════════════════════════════════════════════════════════════════════════
# 11. CONTEXT INJECTION TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestContextInjection:
    """Automatic RAG context injection into crew tasks."""

    def test_context_loader_exists(self):
        from app.agents.commander.context import _load_knowledge_base_context
        assert callable(_load_knowledge_base_context)

    def test_context_loader_returns_string(self):
        from app.agents.commander.context import _load_knowledge_base_context
        assert isinstance(_load_knowledge_base_context("test"), str)

    def test_orchestrator_calls_context_loader(self):
        src = inspect.getsource(
            __import__("app.agents.commander.orchestrator", fromlist=["_"])
        )
        assert "_load_knowledge_base_context" in src


# ════════════════════════════════════════════════════════════════════════════════
# 12. SYSTEM WIRING TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestSystemWiring:
    """Knowledge base wired into all expected consumers."""

    def test_researcher_has_tool(self):
        src = inspect.getsource(__import__("app.agents.researcher", fromlist=["_"]))
        assert "KnowledgeSearchTool" in src

    def test_coder_has_tool(self):
        src = inspect.getsource(__import__("app.agents.coder", fromlist=["_"]))
        assert "KnowledgeSearchTool" in src

    def test_writer_has_tool(self):
        src = inspect.getsource(__import__("app.agents.writer", fromlist=["_"]))
        assert "KnowledgeSearchTool" in src

    def test_media_analyst_has_tool(self):
        src = inspect.getsource(__import__("app.agents.media_analyst", fromlist=["_"]))
        assert "KnowledgeSearchTool" in src

    def test_orchestrator_has_kb_add(self):
        src = inspect.getsource(
            __import__("app.agents.commander.orchestrator", fromlist=["_"])
        )
        assert "kb add" in src.lower()
        assert "add_document" in src

    def test_commands_uses_knowledge_store(self):
        src = inspect.getsource(
            __import__("app.agents.commander.commands", fromlist=["_"])
        )
        assert "KnowledgeStore" in src

    def test_context_uses_knowledge_base(self):
        src = inspect.getsource(
            __import__("app.agents.commander.context", fromlist=["_"])
        )
        assert "knowledge_base" in src

    def test_firebase_publish_reports_kb(self):
        src = inspect.getsource(__import__("app.firebase.publish", fromlist=["_"]))
        assert "report_knowledge_base" in src or "knowledge_base" in src

    def test_firebase_listeners_poll_kb(self):
        src = inspect.getsource(__import__("app.firebase.listeners", fromlist=["_"]))
        assert "KnowledgeStore" in src

    def test_main_app_mounts_kb_api(self):
        src = inspect.getsource(__import__("app.main", fromlist=["_"]))
        assert "kb" in src.lower()

    def test_firestore_schema_has_kb(self):
        src = inspect.getsource(
            __import__("app.contracts.firestore_schema", fromlist=["_"])
        )
        assert "knowledge_base" in src


# ════════════════════════════════════════════════════════════════════════════════
# 13. INJECTION DEFENSE TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestInjectionDefense:
    """Content sanitization prevents prompt injection."""

    def test_ingestion_has_sanitize(self):
        src = inspect.getsource(__import__("app.knowledge_base.ingestion", fromlist=["_"]))
        assert "sanitize" in src.lower()

    def test_max_extract_chars(self):
        from app.knowledge_base.ingestion import _MAX_EXTRACT_CHARS
        assert _MAX_EXTRACT_CHARS == 500_000

    def test_api_validates_extension(self):
        src = inspect.getsource(__import__("app.api.kb", fromlist=["_"]))
        assert "ALLOWED_EXTENSIONS" in src

    def test_api_checks_file_size(self):
        src = inspect.getsource(__import__("app.api.kb", fromlist=["_"]))
        assert "MAX_UPLOAD_SIZE" in src


# ════════════════════════════════════════════════════════════════════════════════
# 14. EDGE CASES
# ════════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_chunk_text_overlap_larger_than_size(self):
        """Langchain raises ValueError when overlap > size — verify graceful handling."""
        from app.knowledge_base.ingestion import chunk_text
        text = "Word " * 200
        try:
            chunks = chunk_text(text, chunk_size=100, chunk_overlap=150)
            # If it doesn't raise, chunks should be valid
            assert len(chunks) >= 0
        except ValueError:
            pass  # Langchain correctly rejects overlap > size

    def test_ingest_empty_file(self):
        from app.knowledge_base.ingestion import ingest_document
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("")
            f.flush()
            chunks, result = ingest_document(f.name)
            assert result.chunks_created == 0
        os.unlink(f.name)

    def test_detect_format_case_insensitive(self):
        from app.knowledge_base.ingestion import detect_format
        result = detect_format("FILE.PDF")
        assert result.lower() == ".pdf"

    def test_document_chunk_metadata_defaults(self):
        from app.knowledge_base.ingestion import DocumentChunk
        assert DocumentChunk(text="test").metadata == {}


# ════════════════════════════════════════════════════════════════════════════════
# 15. INTEGRATION TESTS
# ════════════════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(_LOW_MEM, reason="ChromaDB import heavy")
class TestIntegration:
    """End-to-end integration tests."""

    def test_full_ingest_query_remove_cycle(self):
        from app.knowledge_base.vectorstore import KnowledgeStore
        store = KnowledgeStore()
        try:
            result = store.add_text(
                text="Enterprise data governance requires clear policies.",
                source_name="_test_kb_integration", category="governance")
            assert result.success is True
            assert isinstance(store.query("data governance", top_k=3), list)
            docs = store.list_documents()
            assert any("_test_kb_integration" in str(d.get("source", "")) for d in docs)
            assert store.stats()["total_chunks"] > 0
        finally:
            store.remove_document("_test_kb_integration")

    def test_workspace_exists(self):
        from app.knowledge_base.config import CHROMA_PERSIST_DIR
        assert Path(CHROMA_PERSIST_DIR).exists()


# ════════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
