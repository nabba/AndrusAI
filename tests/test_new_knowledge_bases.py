"""Tests for the four new knowledge bases (Phase 2)."""

import os
import pytest

_LOW_MEM = os.environ.get("LOW_MEM_TESTS", "1") == "1"


# ── Episteme KB ─────────────────────────────────────────────────────────────

def test_episteme_config():
    from app.episteme import config
    assert config.COLLECTION_NAME == "episteme_research"
    assert config.CHUNK_SIZE == 1200
    assert "research_paper" in config.PAPER_TYPES


def test_episteme_imports():
    from app.episteme import EpistemeStore, EpistemeSearchTool, get_episteme_tools
    assert EpistemeStore is not None
    tools = get_episteme_tools()
    assert len(tools) >= 1
    assert tools[0].name == "search_research_knowledge"


def test_episteme_ingestion_module():
    from app.episteme.ingestion import extract_frontmatter, chunk_text
    meta, body = extract_frontmatter("---\ntitle: Test\n---\n\nBody text here.")
    assert meta.get("title") == "Test"
    assert "Body text here" in body


# ── Experiential KB ─────────────────────────────────────────────────────────

def test_experiential_config():
    from app.experiential import config
    assert config.COLLECTION_NAME == "experiential_journal"
    assert config.CHUNK_SIZE == 800
    assert "task_reflection" in config.ENTRY_TYPES


def test_experiential_imports():
    from app.experiential import ExperientialStore, JournalSearchTool, get_experiential_tools
    assert ExperientialStore is not None
    reader_tools = get_experiential_tools("reader")
    assert len(reader_tools) == 1  # search only
    writer_tools = get_experiential_tools("writer")
    assert len(writer_tools) == 2  # search + write


def test_journal_writer_module():
    from app.experiential.journal_writer import JournalWriter
    writer = JournalWriter()
    assert hasattr(writer, "write_post_task_reflection")
    assert hasattr(writer, "write_custom_entry")


# ── Aesthetics KB ───────────────────────────────────────────────────────────

def test_aesthetics_config():
    from app.aesthetics import config
    assert config.COLLECTION_NAME == "aesthetic_patterns"
    assert "elegant_code" in config.PATTERN_TYPES


def test_aesthetics_imports():
    from app.aesthetics import AestheticStore, AestheticSearchTool, FlagAestheticTool, get_aesthetic_tools
    assert AestheticStore is not None
    tools = get_aesthetic_tools("coder")
    assert len(tools) == 2  # search + flag
    assert tools[0].name == "search_aesthetic_patterns"
    assert tools[1].name == "flag_aesthetic_pattern"


# ── Tensions KB ─────────────────────────────────────────────────────────────

def test_tensions_config():
    from app.tensions import config
    assert config.COLLECTION_NAME == "unresolved_tensions"
    assert "principle_conflict" in config.TENSION_TYPES
    assert "unresolved" in config.RESOLUTION_STATUSES


def test_tensions_imports():
    from app.tensions import TensionStore, TensionSearchTool, RecordTensionTool, get_tension_tools
    assert TensionStore is not None
    tools = get_tension_tools("critic")
    assert len(tools) == 2  # search + record
    assert tools[0].name == "search_tensions"
    assert tools[1].name == "record_tension"


def test_tension_detector_module():
    from app.tensions.detector import detect_tension
    # With empty inputs, should return None.
    assert detect_tension("", "", "") is None


# ── Cross-KB integration ───────────────────────────────────────────────────

def test_retrieval_config_dataclass():
    from app.retrieval.config import RetrievalConfig
    cfg = RetrievalConfig(
        temporal_enabled=True,
        temporal_field="created_at",
    )
    assert cfg.temporal_enabled is True
    assert cfg.temporal_field == "created_at"


def test_philosophy_dialectics_module():
    from app.philosophy.dialectics import DialecticalGraph, get_graph
    graph = get_graph()
    assert isinstance(graph, DialecticalGraph)
    # Without Neo4j, find_counter_arguments returns empty list.
    assert graph.find_counter_arguments("virtue is sufficient") == []


def test_philosophy_dialectics_tool():
    from app.philosophy.dialectics_tool import FindCounterArgumentTool
    tool = FindCounterArgumentTool()
    assert tool.name == "find_counter_argument"


# ── Paths registration ─────────────────────────────────────────────────────

def test_new_paths_registered():
    from app.paths import (
        EPISTEME_DIR, EPISTEME_TEXTS_DIR,
        EXPERIENTIAL_DIR, EXPERIENTIAL_ENTRIES_DIR,
        AESTHETICS_DIR, AESTHETICS_PATTERNS_DIR,
        TENSIONS_DIR, TENSIONS_ENTRIES_DIR,
        LITERATURE_LIBRARY_DIR,
    )
    # All should be Path objects.
    assert str(EPISTEME_DIR).endswith("episteme")
    assert str(EXPERIENTIAL_ENTRIES_DIR).endswith("entries")
    assert str(AESTHETICS_PATTERNS_DIR).endswith("patterns")
    assert str(TENSIONS_ENTRIES_DIR).endswith("entries")
    assert str(LITERATURE_LIBRARY_DIR).endswith("literature_library")
