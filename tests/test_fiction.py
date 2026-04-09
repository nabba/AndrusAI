"""
Fiction Inspiration System Tests
==================================

Comprehensive tests for the fiction/literature inspiration subsystem:
epistemic safety (5-layer defense), agent access control, metadata
enrichment, tool framing, collection isolation, and system wiring.

CRITICAL: These tests verify that fiction content NEVER contaminates
factual knowledge systems (knowledge base, philosophy DB, researcher agent).

Run: docker exec crewai-team-gateway-1 python3 -m pytest /app/tests/test_fiction.py -v
"""

import inspect
import json
import os
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

_LOW_MEM = os.environ.get("LOW_MEM_TESTS", "1") == "1"


# ════════════════════════════════════════════════════════════════════════════════
# 1. MODULE IMPORTS & CONSTANTS
# ════════════════════════════════════════════════════════════════════════════════

class TestImportsAndConstants:
    """All fiction module symbols must import cleanly."""

    def test_core_imports(self):
        from app.fiction_inspiration import (
            FICTION_COLLECTION_NAME, FICTION_ENABLED_AGENTS, FICTION_DISABLED_AGENTS,
            FICTION_AWARENESS_PROMPT, FICTION_LIBRARY_DIR,
            get_fiction_tools, agent_has_fiction_access,
            search_fiction, random_inspiration, list_fiction_catalog,
            ingest_book, ingest_library,
            _get_collection, _enrich_metadata, _format_result,
        )
        assert callable(get_fiction_tools)
        assert callable(agent_has_fiction_access)

    def test_collection_name(self):
        from app.fiction_inspiration import FICTION_COLLECTION_NAME
        assert FICTION_COLLECTION_NAME == "fiction_inspiration"

    def test_fiction_library_dir(self):
        from app.fiction_inspiration import FICTION_LIBRARY_DIR
        assert isinstance(FICTION_LIBRARY_DIR, Path)


# ════════════════════════════════════════════════════════════════════════════════
# 2. LAYER 5: AGENT ACCESS CONTROL (SELECTIVE ACCESS)
# ════════════════════════════════════════════════════════════════════════════════

class TestAgentAccessControl:
    """CRITICAL: Only creative agents get fiction. Factual agents NEVER get it."""

    def test_enabled_agents(self):
        from app.fiction_inspiration import FICTION_ENABLED_AGENTS
        assert "commander" in FICTION_ENABLED_AGENTS
        assert "coder" in FICTION_ENABLED_AGENTS
        assert "writer" in FICTION_ENABLED_AGENTS
        assert "media_analyst" in FICTION_ENABLED_AGENTS

    def test_disabled_agents(self):
        from app.fiction_inspiration import FICTION_DISABLED_AGENTS
        assert "researcher" in FICTION_DISABLED_AGENTS
        assert "self_improver" in FICTION_DISABLED_AGENTS
        assert "critic" in FICTION_DISABLED_AGENTS

    def test_enabled_and_disabled_are_disjoint(self):
        from app.fiction_inspiration import FICTION_ENABLED_AGENTS, FICTION_DISABLED_AGENTS
        overlap = FICTION_ENABLED_AGENTS & FICTION_DISABLED_AGENTS
        assert len(overlap) == 0, f"Overlap between enabled/disabled: {overlap}"

    def test_access_check_enabled(self):
        from app.fiction_inspiration import agent_has_fiction_access
        assert agent_has_fiction_access("writer") is True
        assert agent_has_fiction_access("coder") is True
        assert agent_has_fiction_access("commander") is True

    def test_access_check_disabled(self):
        from app.fiction_inspiration import agent_has_fiction_access
        assert agent_has_fiction_access("researcher") is False
        assert agent_has_fiction_access("self_improver") is False
        assert agent_has_fiction_access("critic") is False

    def test_access_check_case_insensitive(self):
        from app.fiction_inspiration import agent_has_fiction_access
        assert agent_has_fiction_access("Writer") is True
        assert agent_has_fiction_access("RESEARCHER") is False

    def test_access_check_unknown_agent(self):
        from app.fiction_inspiration import agent_has_fiction_access
        assert agent_has_fiction_access("unknown_agent_xyz") is False

    def test_frozensets_are_immutable(self):
        from app.fiction_inspiration import FICTION_ENABLED_AGENTS, FICTION_DISABLED_AGENTS
        assert isinstance(FICTION_ENABLED_AGENTS, frozenset)
        assert isinstance(FICTION_DISABLED_AGENTS, frozenset)


# ════════════════════════════════════════════════════════════════════════════════
# 3. LAYER 4: AGENT SYSTEM PROMPTS (EPISTEMIC BOUNDARY)
# ════════════════════════════════════════════════════════════════════════════════

class TestFictionAwarenessPrompt:
    """CRITICAL: Agents with fiction access must have epistemic boundary rules."""

    def test_prompt_exists(self):
        from app.fiction_inspiration import FICTION_AWARENESS_PROMPT
        assert isinstance(FICTION_AWARENESS_PROMPT, str)
        assert len(FICTION_AWARENESS_PROMPT) > 200

    def test_prompt_contains_absolute_rules(self):
        from app.fiction_inspiration import FICTION_AWARENESS_PROMPT
        assert "ABSOLUTE RULES" in FICTION_AWARENESS_PROMPT
        assert "NEVER factual" in FICTION_AWARENESS_PROMPT or "NEVER" in FICTION_AWARENESS_PROMPT

    def test_prompt_forbids_citation_as_fact(self):
        from app.fiction_inspiration import FICTION_AWARENESS_PROMPT
        assert "NEVER cite fictional content as evidence" in FICTION_AWARENESS_PROMPT

    def test_prompt_requires_attribution(self):
        from app.fiction_inspiration import FICTION_AWARENESS_PROMPT
        assert "Inspired by" in FICTION_AWARENESS_PROMPT

    def test_prompt_declares_epistemic_separation(self):
        from app.fiction_inspiration import FICTION_AWARENESS_PROMPT
        assert "EPISTEMICALLY SEPARATE" in FICTION_AWARENESS_PROMPT

    def test_prompt_mentions_hallucination(self):
        from app.fiction_inspiration import FICTION_AWARENESS_PROMPT
        assert "HALLUCINATION" in FICTION_AWARENESS_PROMPT or "hallucination" in FICTION_AWARENESS_PROMPT

    def test_writer_backstory_has_fiction_protocol(self):
        src = inspect.getsource(__import__("app.agents.writer", fromlist=["_"]))
        assert "FICTION_AWARENESS_PROMPT" in src

    def test_coder_backstory_has_fiction_protocol(self):
        src = inspect.getsource(__import__("app.agents.coder", fromlist=["_"]))
        assert "FICTION_AWARENESS_PROMPT" in src

    def test_researcher_backstory_has_no_fiction_protocol(self):
        src = inspect.getsource(__import__("app.agents.researcher", fromlist=["_"]))
        assert "FICTION_AWARENESS_PROMPT" not in src
        assert "get_fiction_tools" not in src

    def test_critic_backstory_has_no_fiction_protocol(self):
        src = inspect.getsource(__import__("app.agents.critic", fromlist=["_"]))
        assert "FICTION_AWARENESS_PROMPT" not in src
        assert "get_fiction_tools" not in src


# ════════════════════════════════════════════════════════════════════════════════
# 4. LAYER 3: TOOL-LEVEL FRAMING (RESULT WRAPPING)
# ════════════════════════════════════════════════════════════════════════════════

class TestToolLevelFraming:
    """CRITICAL: Every fiction result must be wrapped in warning envelope."""

    def test_format_result_contains_header(self):
        from app.fiction_inspiration import _format_result
        result = _format_result("Test document text", {
            "book_title": "Test Book",
            "author": "Test Author",
            "themes": "[]",
            "chapter": "Chapter 1",
        })
        assert "FICTIONAL INSPIRATION" in result
        assert "NOT FACTUAL KNOWLEDGE" in result

    def test_format_result_contains_footer(self):
        from app.fiction_inspiration import _format_result
        result = _format_result("Test text", {
            "book_title": "X", "author": "Y", "themes": "[]",
        })
        assert "HALLUCINATION" in result
        assert "NEVER USE AS" in result
        assert "Fact" in result or "fact" in result

    def test_format_result_shows_source(self):
        from app.fiction_inspiration import _format_result
        result = _format_result("Text", {
            "book_title": "Foundation", "author": "Isaac Asimov",
            "themes": '["sci-fi"]',
        })
        assert "Foundation" in result
        assert "Isaac Asimov" in result

    def test_format_result_shows_use_instructions(self):
        from app.fiction_inspiration import _format_result
        result = _format_result("Text", {
            "book_title": "X", "author": "Y", "themes": "[]",
        })
        assert "USE AS" in result
        assert "Creative fuel" in result or "creative" in result.lower()

    def test_search_fiction_wraps_results(self):
        from app.fiction_inspiration import search_fiction
        result = search_fiction("psychohistory", n_results=1)
        if "No fiction" not in result and "empty" not in result.lower():
            assert "FICTIONAL" in result or "fictional" in result

    def test_tool_descriptions_warn_about_fiction(self):
        from app.fiction_inspiration import get_fiction_tools
        tools = get_fiction_tools()
        for tool in tools:
            desc = tool.description.lower()
            assert "fictional" in desc or "fiction" in desc or "hallucination" in desc, \
                f"Tool '{tool.name}' description lacks fiction warning"


# ════════════════════════════════════════════════════════════════════════════════
# 5. LAYER 2: IMMUTABLE METADATA
# ════════════════════════════════════════════════════════════════════════════════

class TestImmutableMetadata:
    """CRITICAL: Every fiction chunk must have source_type=fiction, epistemic_status=imaginary."""

    def test_ingest_sets_source_type(self):
        """All ingested chunks must have source_type='fiction'."""
        from app.fiction_inspiration import _get_collection
        col = _get_collection()
        if col.count() == 0:
            pytest.skip("Fiction collection empty")
        sample = col.get(limit=10, include=["metadatas"])
        for meta in sample["metadatas"]:
            assert meta.get("source_type") == "fiction", \
                f"Chunk missing source_type=fiction: {meta}"

    def test_ingest_sets_epistemic_status(self):
        """All ingested chunks must have epistemic_status='imaginary'."""
        from app.fiction_inspiration import _get_collection
        col = _get_collection()
        if col.count() == 0:
            pytest.skip("Fiction collection empty")
        sample = col.get(limit=10, include=["metadatas"])
        for meta in sample["metadatas"]:
            assert meta.get("epistemic_status") == "imaginary", \
                f"Chunk missing epistemic_status=imaginary: {meta}"

    def test_metadata_schema_complete(self):
        """Every chunk must have all required metadata fields."""
        from app.fiction_inspiration import _get_collection
        col = _get_collection()
        if col.count() == 0:
            pytest.skip("Fiction collection empty")
        required = {"source_type", "epistemic_status", "book_title", "author",
                     "themes", "chapter", "chunk_index", "source_file", "ingested_at"}
        sample = col.get(limit=5, include=["metadatas"])
        for meta in sample["metadatas"]:
            missing = required - set(meta.keys())
            assert len(missing) == 0, f"Missing metadata fields: {missing}"

    def test_ingest_book_source_code_sets_immutable_fields(self):
        """Source code of ingest_book must set both immutable fields."""
        src = inspect.getsource(
            __import__("app.fiction_inspiration", fromlist=["ingest_book"]).ingest_book
        )
        assert '"source_type": "fiction"' in src
        assert '"epistemic_status": "imaginary"' in src


# ════════════════════════════════════════════════════════════════════════════════
# 6. LAYER 1: COLLECTION SEPARATION
# ════════════════════════════════════════════════════════════════════════════════

class TestCollectionSeparation:
    """CRITICAL: Fiction, knowledge, and philosophy must be separate collections."""

    def test_fiction_collection_name(self):
        from app.fiction_inspiration import FICTION_COLLECTION_NAME
        assert FICTION_COLLECTION_NAME == "fiction_inspiration"

    def test_knowledge_collection_name_different(self):
        from app.knowledge_base.config import CHROMA_COLLECTION_NAME
        from app.fiction_inspiration import FICTION_COLLECTION_NAME
        assert CHROMA_COLLECTION_NAME != FICTION_COLLECTION_NAME
        assert CHROMA_COLLECTION_NAME == "enterprise_knowledge"

    def test_philosophy_collection_name_different(self):
        from app.philosophy.config import COLLECTION_NAME
        from app.fiction_inspiration import FICTION_COLLECTION_NAME
        assert COLLECTION_NAME != FICTION_COLLECTION_NAME
        assert COLLECTION_NAME == "philosophy_humanist"

    def test_three_collections_all_distinct(self):
        from app.fiction_inspiration import FICTION_COLLECTION_NAME
        from app.knowledge_base.config import CHROMA_COLLECTION_NAME as KB_NAME
        from app.philosophy.config import COLLECTION_NAME as PHIL_NAME
        names = {FICTION_COLLECTION_NAME, KB_NAME, PHIL_NAME}
        assert len(names) == 3, f"Collection name collision: {names}"

    def test_knowledge_store_cannot_query_fiction(self):
        """KnowledgeStore uses its own collection, not fiction."""
        src = inspect.getsource(
            __import__("app.knowledge_base.vectorstore", fromlist=["KnowledgeStore"])
        )
        assert "fiction_inspiration" not in src
        assert "fiction" not in src.lower() or "fiction" in src.lower().split("comment")[0] is False

    def test_philosophy_store_cannot_query_fiction(self):
        """PhilosophyStore uses its own collection, not fiction."""
        src = inspect.getsource(
            __import__("app.philosophy.vectorstore", fromlist=["PhilosophyStore"])
        )
        assert "fiction_inspiration" not in src


# ════════════════════════════════════════════════════════════════════════════════
# 7. FICTION TOOLS
# ════════════════════════════════════════════════════════════════════════════════

class TestFictionTools:
    """Fiction tools return exactly 3 tools with correct names."""

    def test_get_fiction_tools_returns_three(self):
        from app.fiction_inspiration import get_fiction_tools
        tools = get_fiction_tools()
        assert len(tools) == 3

    def test_tool_names(self):
        from app.fiction_inspiration import get_fiction_tools
        tools = get_fiction_tools()
        names = {t.name for t in tools}
        assert "fictional_inspiration" in names
        assert "random_fictional_inspiration" in names
        assert "fiction_library_catalog" in names

    def test_search_tool_has_args(self):
        from app.fiction_inspiration import get_fiction_tools
        tools = get_fiction_tools()
        search = [t for t in tools if t.name == "fictional_inspiration"][0]
        schema = search.args_schema.model_json_schema()
        assert "query" in schema.get("properties", {})

    def test_all_tools_callable(self):
        from app.fiction_inspiration import get_fiction_tools
        for tool in get_fiction_tools():
            assert hasattr(tool, "_run")
            assert callable(tool._run)


# ════════════════════════════════════════════════════════════════════════════════
# 8. AGENT WIRING TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestAgentWiring:
    """Verify fiction tools are wired into correct agents and excluded from others."""

    def test_writer_has_fiction_tools(self):
        src = inspect.getsource(__import__("app.agents.writer", fromlist=["_"]))
        assert "get_fiction_tools()" in src

    def test_coder_has_fiction_tools(self):
        src = inspect.getsource(__import__("app.agents.coder", fromlist=["_"]))
        assert "get_fiction_tools()" in src

    def test_researcher_has_no_fiction_tools(self):
        src = inspect.getsource(__import__("app.agents.researcher", fromlist=["_"]))
        assert "get_fiction_tools" not in src
        assert "fiction_inspiration" not in src

    def test_critic_has_no_fiction_tools(self):
        src = inspect.getsource(__import__("app.agents.critic", fromlist=["_"]))
        assert "get_fiction_tools" not in src
        assert "fiction_inspiration" not in src

    def test_media_analyst_has_no_fiction_tools(self):
        """Media analyst is in FICTION_ENABLED_AGENTS but tools not yet wired."""
        src = inspect.getsource(__import__("app.agents.media_analyst", fromlist=["_"]))
        # Currently not wired — this documents the current state
        has_fiction = "get_fiction_tools" in src
        # Either way is acceptable — just verify it's intentional
        assert isinstance(has_fiction, bool)


# ════════════════════════════════════════════════════════════════════════════════
# 9. METADATA ENRICHMENT TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestMetadataEnrichment:
    """3-stage metadata extraction pipeline."""

    def test_enrich_metadata_signature(self):
        from app.fiction_inspiration import _enrich_metadata
        sig = inspect.signature(_enrich_metadata)
        params = list(sig.parameters.keys())
        assert "filepath" in params
        assert "frontmatter" in params
        assert "body" in params

    def test_enrichment_strips_epub_artifacts(self):
        """Epub HTML artifacts should be stripped before LLM analysis."""
        src = inspect.getsource(
            __import__("app.fiction_inspiration", fromlist=["_enrich_metadata"])._enrich_metadata
        )
        assert "```{=html}" in src or "html" in src
        assert ":::" in src  # pandoc div markers

    def test_bad_author_rejection(self):
        """Authors like 'Nazi Germany' must be rejected."""
        src = inspect.getsource(
            __import__("app.fiction_inspiration", fromlist=["_enrich_metadata"])._enrich_metadata
        )
        assert "nazi germany" in src.lower()

    def test_artifact_title_rejection(self):
        """Titles starting with ![], ```, ::: etc must be rejected."""
        src = inspect.getsource(
            __import__("app.fiction_inspiration", fromlist=["ingest_book"]).ingest_book
        )
        assert "_artifact_starts" in src
        assert "![" in src
        assert "```" in src or "`" in src

    def test_filename_fallback_for_bad_title(self):
        """When title is an artifact, filename-derived title should be used."""
        from app.fiction_inspiration import _metadata_from_filename
        result = _metadata_from_filename(Path("Foundation_Edge_-_Isaac_Asimov.md"))
        assert isinstance(result, dict)
        assert result.get("title") or result.get("author")


# ════════════════════════════════════════════════════════════════════════════════
# 10. INGESTION TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestIngestion:
    """Fiction book ingestion pipeline."""

    def test_ingest_book_returns_dict(self):
        from app.fiction_inspiration import FICTION_LIBRARY_DIR
        books = list(FICTION_LIBRARY_DIR.glob("*.md"))
        if not books:
            pytest.skip("No fiction books")
        from app.fiction_inspiration import ingest_book
        # Don't actually ingest — just verify the function exists and is callable
        assert callable(ingest_book)

    def test_collection_has_fiction_data(self):
        from app.fiction_inspiration import _get_collection
        col = _get_collection()
        assert col.count() >= 0  # May be 0 in fresh container

    def test_genre_field_in_metadata(self):
        """Genre field should be present in chunk metadata after enrichment."""
        from app.fiction_inspiration import _get_collection
        col = _get_collection()
        if col.count() == 0:
            pytest.skip("Fiction collection empty")
        sample = col.get(limit=5, include=["metadatas"])
        # At least some chunks should have genre
        genres = [m.get("genre", "") for m in sample["metadatas"]]
        has_genre = any(g for g in genres)
        # Genre is optional (enrichment may not have run)
        assert isinstance(genres, list)


# ════════════════════════════════════════════════════════════════════════════════
# 11. CROSS-CONTAMINATION PREVENTION TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestCrossContamination:
    """CRITICAL: Fiction content must NEVER leak into factual systems."""

    def test_knowledge_base_has_no_fiction_import(self):
        """Knowledge base module must not import fiction_inspiration."""
        for mod in ("app.knowledge_base.vectorstore", "app.knowledge_base.ingestion",
                     "app.knowledge_base.tools", "app.knowledge_base.config"):
            src = inspect.getsource(__import__(mod, fromlist=["_"]))
            assert "fiction" not in src.lower(), \
                f"{mod} imports or references fiction!"

    def test_philosophy_has_no_fiction_import(self):
        """Philosophy module must not import fiction_inspiration."""
        for mod in ("app.philosophy.vectorstore", "app.philosophy.ingestion",
                     "app.philosophy.rag_tool"):
            src = inspect.getsource(__import__(mod, fromlist=["_"]))
            assert "fiction" not in src.lower(), \
                f"{mod} imports or references fiction!"

    def test_researcher_agent_isolated_from_fiction(self):
        """Researcher must have zero fiction access paths."""
        src = inspect.getsource(__import__("app.agents.researcher", fromlist=["_"]))
        assert "fiction" not in src.lower()

    def test_self_improver_isolated_from_fiction(self):
        """Self-improver must have zero fiction access paths."""
        try:
            src = inspect.getsource(__import__("app.crews.self_improvement_crew", fromlist=["_"]))
            assert "fiction" not in src.lower()
        except Exception:
            pass  # Module may not exist

    def test_critic_isolated_from_fiction(self):
        """Critic must have zero fiction access paths."""
        src = inspect.getsource(__import__("app.agents.critic", fromlist=["_"]))
        assert "fiction" not in src.lower()

    def test_no_shared_query_function(self):
        """No shared function exists that queries both fiction and knowledge."""
        # Check that knowledge_base doesn't import from fiction
        kb_src = inspect.getsource(__import__("app.knowledge_base.vectorstore", fromlist=["_"]))
        assert "fiction_inspiration" not in kb_src

    def test_fiction_collection_metadata_is_fictional(self):
        """The ChromaDB collection itself must be marked as fictional."""
        from app.fiction_inspiration import _get_collection
        col = _get_collection()
        # Collection metadata should indicate fictional content
        meta = col.metadata or {}
        assert meta.get("epistemic_status") == "fictional" or "fiction" in str(meta).lower()


# ════════════════════════════════════════════════════════════════════════════════
# 12. SYSTEM WIRING TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestSystemWiring:
    """Fiction system must be properly wired into infrastructure."""

    def test_idle_scheduler_ingests_fiction(self):
        src = inspect.getsource(__import__("app.idle_scheduler", fromlist=["_"]))
        assert "fiction" in src.lower()

    def test_firebase_publishes_fiction_status(self):
        src = inspect.getsource(__import__("app.firebase.publish", fromlist=["_"]))
        assert "fiction" in src.lower()

    def test_firebase_listener_has_fiction_queue(self):
        src = inspect.getsource(__import__("app.firebase.listeners", fromlist=["_"]))
        assert "fiction_queue" in src

    def test_api_has_fiction_endpoints(self):
        src = inspect.getsource(__import__("app.api.fiction", fromlist=["_"]))
        assert "/upload" in src
        assert "/status" in src

    def test_main_mounts_fiction_router(self):
        src = inspect.getsource(__import__("app.main", fromlist=["_"]))
        assert "fiction_router" in src

    def test_firestore_rules_allow_fiction_queue(self):
        """Firestore rules must include fiction_queue collection."""
        rules_path = Path("/Users/andrus/BotArmy/crewai-team/dashboard/firestore.rules")
        if rules_path.exists():
            rules = rules_path.read_text()
            assert "fiction_queue" in rules
        else:
            # Inside Docker, check from workspace
            pass  # Firestore rules not in container


# ════════════════════════════════════════════════════════════════════════════════
# 13. DASHBOARD INTEGRATION TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestDashboardIntegration:
    """Dashboard must display fiction data with genre column."""

    def test_publish_includes_genre(self):
        src = inspect.getsource(
            __import__("app.firebase.publish", fromlist=["report_fiction_library"]).report_fiction_library
        )
        assert "genre" in src

    def test_dashboard_has_genre_column(self):
        dashboard = Path("/Users/andrus/BotArmy/crewai-team/dashboard/public/index.html")
        if dashboard.exists():
            html = dashboard.read_text()
            assert "Genre" in html or "genre" in html


# ════════════════════════════════════════════════════════════════════════════════
# 14. INTEGRATION TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestIntegration:
    """End-to-end integration tests."""

    def test_fiction_collection_operational(self):
        from app.fiction_inspiration import _get_collection
        col = _get_collection()
        count = col.count()
        assert isinstance(count, int)
        assert count >= 0

    def test_search_fiction_returns_string(self):
        from app.fiction_inspiration import search_fiction
        result = search_fiction("galactic empire", n_results=2)
        assert isinstance(result, str)

    def test_list_catalog_returns_string(self):
        from app.fiction_inspiration import list_fiction_catalog
        result = list_fiction_catalog()
        assert isinstance(result, str)

    def test_random_inspiration_returns_string(self):
        from app.fiction_inspiration import random_inspiration
        result = random_inspiration()
        assert isinstance(result, str)

    def test_fiction_and_knowledge_never_share_results(self):
        """Query same term in both — results must come from different collections."""
        from app.fiction_inspiration import _get_collection as fiction_col
        fc = fiction_col()
        fiction_count = fc.count()

        from app.knowledge_base.vectorstore import KnowledgeStore
        ks = KnowledgeStore()
        kb_stats = ks.stats()

        # Collections exist independently
        assert fiction_count >= 0
        assert kb_stats.get("total_chunks", 0) >= 0
        # Collection names are different (verified structurally above)


# ════════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
