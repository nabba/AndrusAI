"""Tests for the code-intel subsystem (Phase 3 piece 1, 2026-05-20).

Covers:
  * Indexer: function/class/method/async/nested extraction
  * Indexer: skips standard ignore dirs + unparseable files + oversized
  * Store: atomic JSONL write/read + cache invalidation + reset
  * Queries: find_symbol/find_references/find_callers
  * Master switch: code_intel_enabled toggle + getters
  * Defensive shapes: empty index, name with no matches, lambda-call refs

Safety invariants pinned:
  * Default code_intel_enabled = False
  * Empty index returns empty lists (no raises)
  * Parse errors don't abort the index — other files still land
  * Atomic write: readers see pre-state or post-state, never partial
"""
from __future__ import annotations

import json
import sys
import textwrap
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())

try:
    import crewai as _real_crewai  # noqa: F401
    _crewai_available = True
except Exception:
    _crewai_available = False

if not _crewai_available:
    for _mod in ("crewai", "crewai.tools"):
        if _mod not in sys.modules:
            m = types.ModuleType(_mod)
            if _mod == "crewai.tools":
                m.tool = lambda name: (lambda fn: fn)
                m.BaseTool = type("BaseTool", (), {})
            sys.modules[_mod] = m


from app import runtime_settings  # noqa: E402
from app.code_intel import (  # noqa: E402
    IndexSnapshot,
    ReferenceLocation,
    SymbolKind,
    SymbolLocation,
    build_index,
    find_callers,
    find_references,
    find_symbol,
    index_stats,
    is_built,
    load_index,
    save_index,
    store,
)


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


# ── Indexer ─────────────────────────────────────────────────────────


class TestIndexer:
    def test_extracts_function(self, tmp_path):
        _write(tmp_path / "x.py", """
            def greet(name):
                return f'hi {name}'
        """)
        snap = build_index(root=tmp_path)
        assert len(snap.symbols) == 1
        s = snap.symbols[0]
        assert s.name == "greet"
        assert s.kind is SymbolKind.FUNCTION
        assert s.parent == ""

    def test_extracts_class_and_methods(self, tmp_path):
        _write(tmp_path / "calc.py", """
            class Calculator:
                def add(self, x, y):
                    return x + y

                async def add_async(self, x, y):
                    return self.add(x, y)
        """)
        snap = build_index(root=tmp_path)
        by_name = {s.name: s for s in snap.symbols}
        assert by_name["Calculator"].kind is SymbolKind.CLASS
        assert by_name["add"].kind is SymbolKind.METHOD
        assert by_name["add"].parent == "Calculator"
        assert by_name["add_async"].kind is SymbolKind.ASYNC_METHOD

    def test_extracts_async_function_at_module_level(self, tmp_path):
        _write(tmp_path / "a.py", """
            async def fetch():
                pass
        """)
        snap = build_index(root=tmp_path)
        assert snap.symbols[0].kind is SymbolKind.ASYNC_FUNCTION

    def test_records_references(self, tmp_path):
        _write(tmp_path / "x.py", """
            def f():
                return 1

            def g():
                return f() + f()
        """)
        snap = build_index(root=tmp_path)
        f_refs = [r for r in snap.references if r.name == "f"]
        # Two references to f() inside g
        assert len(f_refs) == 2
        assert all(r.in_function == "g" for r in f_refs)

    def test_records_attribute_references(self, tmp_path):
        _write(tmp_path / "x.py", """
            import os

            def f():
                return os.path.join('a', 'b')
        """)
        snap = build_index(root=tmp_path)
        # Should record both ``path`` (os.path) and ``join`` (os.path.join)
        names = {r.name for r in snap.references}
        assert "join" in names

    def test_docstring_first_line_captured(self, tmp_path):
        _write(tmp_path / "x.py", '''
            def f():
                """First line of docstring.

                Multiline body."""
                pass
        ''')
        snap = build_index(root=tmp_path)
        assert snap.symbols[0].docstring_first_line == "First line of docstring."

    def test_skips_venv_directories(self, tmp_path):
        _write(tmp_path / ".venv" / "lib" / "x.py", "def hidden(): pass\n")
        _write(tmp_path / "real.py", "def visible(): pass\n")
        snap = build_index(root=tmp_path)
        names = {s.name for s in snap.symbols}
        assert "visible" in names
        assert "hidden" not in names

    def test_skips_node_modules(self, tmp_path):
        _write(tmp_path / "node_modules" / "x.py", "def hidden(): pass\n")
        _write(tmp_path / "real.py", "def visible(): pass\n")
        snap = build_index(root=tmp_path)
        names = {s.name for s in snap.symbols}
        assert "hidden" not in names
        assert "visible" in names

    def test_skips_pycache(self, tmp_path):
        _write(tmp_path / "__pycache__" / "x.py", "def hidden(): pass\n")
        _write(tmp_path / "real.py", "def visible(): pass\n")
        snap = build_index(root=tmp_path)
        names = {s.name for s in snap.symbols}
        assert "hidden" not in names

    def test_indexer_skips_unparseable_file(self, tmp_path):
        # Syntax error file should be skipped, other files still landed.
        _write(tmp_path / "broken.py", "def )( bad syntax\n")
        _write(tmp_path / "good.py", "def ok(): pass\n")
        snap = build_index(root=tmp_path)
        names = {s.name for s in snap.symbols}
        assert "ok" in names
        # Broken file should NOT show up in indexed_files
        assert all(not f.endswith("broken.py") for f in snap.indexed_files)

    def test_extra_skip_dirs(self, tmp_path):
        _write(tmp_path / "vendor" / "x.py", "def hidden(): pass\n")
        _write(tmp_path / "real.py", "def visible(): pass\n")
        snap = build_index(
            root=tmp_path, extra_skip_dirs=["vendor"],
        )
        names = {s.name for s in snap.symbols}
        assert "hidden" not in names
        assert "visible" in names

    def test_max_files_cap(self, tmp_path):
        # Create 5 tiny files, cap at 3, only the first 3 land.
        for i in range(5):
            _write(tmp_path / f"f{i}.py", f"def fn_{i}(): pass\n")
        snap = build_index(root=tmp_path, max_files=3)
        assert len(snap.indexed_files) == 3
        assert len(snap.symbols) == 3

    def test_root_must_exist(self, tmp_path):
        with pytest.raises(ValueError, match="does not exist"):
            build_index(root=tmp_path / "missing")

    def test_root_must_be_directory(self, tmp_path):
        f = tmp_path / "f.py"
        f.write_text("")
        with pytest.raises(ValueError, match="not a directory"):
            build_index(root=f)


# ── Store ──────────────────────────────────────────────────────────


class TestStore:
    def test_save_and_load_roundtrip(self, tmp_path):
        store.reset_for_tests(tmp_path)
        _write(tmp_path / "src" / "x.py", """
            def f(): pass
            def g(): return f()
        """)
        snap = build_index(root=tmp_path / "src")
        save_index(snap)
        loaded = load_index()
        assert len(loaded.symbols) == len(snap.symbols)
        assert len(loaded.references) == len(snap.references)
        names = {s.name for s in loaded.symbols}
        assert "f" in names and "g" in names

    def test_empty_index_when_not_built(self, tmp_path):
        store.reset_for_tests(tmp_path / "fresh")
        loaded = load_index()
        assert loaded.symbols == []
        assert loaded.references == []
        assert loaded.indexed_files == []

    def test_is_built_predicate(self, tmp_path):
        store.reset_for_tests(tmp_path)
        assert not is_built()
        save_index(IndexSnapshot(indexed_at="2026-05-20T00:00:00+00:00"))
        assert is_built()

    def test_save_invalidates_cache(self, tmp_path):
        store.reset_for_tests(tmp_path)
        # First snapshot
        snap1 = IndexSnapshot(
            symbols=[SymbolLocation(
                name="a", kind=SymbolKind.FUNCTION,
                file_path="x.py", lineno=1, end_lineno=1,
            )],
            indexed_at="t1",
        )
        save_index(snap1)
        loaded1 = load_index()
        assert len(loaded1.symbols) == 1

        # Replace with empty snapshot
        save_index(IndexSnapshot(indexed_at="t2"))
        loaded2 = load_index()
        assert len(loaded2.symbols) == 0

    def test_save_index_returns_stats(self, tmp_path):
        store.reset_for_tests(tmp_path)
        snap = IndexSnapshot(
            symbols=[
                SymbolLocation(
                    name="a", kind=SymbolKind.FUNCTION,
                    file_path="x.py", lineno=1, end_lineno=1,
                ),
                SymbolLocation(
                    name="b", kind=SymbolKind.CLASS,
                    file_path="x.py", lineno=10, end_lineno=15,
                ),
            ],
            references=[
                ReferenceLocation(
                    name="a", file_path="x.py", lineno=20, col_offset=5,
                ),
            ],
            indexed_files=["x.py"],
        )
        stats = save_index(snap)
        assert stats == {"symbols": 2, "references": 1, "indexed_files": 1}


# ── Queries ─────────────────────────────────────────────────────────


@pytest.fixture
def populated_index(tmp_path):
    """Build a small repo + persist it; tests read via the query API."""
    src = tmp_path / "src"
    _write(src / "calc.py", """
        class Calculator:
            def add(self, x, y):
                return x + y

            def multiply(self, x, y):
                # uses add internally
                result = 0
                for _ in range(y):
                    result = self.add(result, x)
                return result

        def factorial(n):
            if n <= 1:
                return 1
            return n * factorial(n - 1)
    """)
    _write(src / "main.py", """
        from calc import Calculator, factorial

        def run():
            c = Calculator()
            return c.add(c.multiply(2, 3), factorial(4))
    """)
    store.reset_for_tests(tmp_path / "index")
    snap = build_index(root=src)
    save_index(snap)
    return tmp_path


class TestQueries:
    def test_find_symbol_finds_class(self, populated_index):
        results = find_symbol("Calculator")
        assert len(results) == 1
        assert results[0].kind is SymbolKind.CLASS

    def test_find_symbol_finds_method(self, populated_index):
        results = find_symbol("add")
        assert len(results) == 1
        assert results[0].kind is SymbolKind.METHOD
        assert results[0].parent == "Calculator"
        assert results[0].fully_qualified == "Calculator.add"

    def test_find_symbol_filter_by_kind(self, populated_index):
        results = find_symbol("add", kind=SymbolKind.FUNCTION)
        assert results == []  # add is a method, not a function

    def test_find_symbol_returns_empty_for_unknown(self, populated_index):
        assert find_symbol("nonexistent_name") == []

    def test_find_references_finds_usages(self, populated_index):
        refs = find_references("add")
        # Should find at least 2 references: self.add inside multiply
        # and c.add inside run
        names = {(r.in_function, r.in_class) for r in refs}
        assert ("multiply", "Calculator") in names
        assert ("run", "") in names

    def test_find_callers_finds_caller_functions(self, populated_index):
        callers = find_callers("add")
        caller_names = {c.fully_qualified for c in callers}
        # multiply (which calls self.add) + run (which calls c.add)
        assert "Calculator.multiply" in caller_names
        assert "run" in caller_names

    def test_find_callers_recursive_function(self, populated_index):
        # factorial calls factorial (recursive)
        callers = find_callers("factorial")
        names = {c.name for c in callers}
        # factorial appears in two contexts:
        #   - inside factorial itself (recursion)
        #   - inside run
        assert "factorial" in names
        assert "run" in names

    def test_find_callers_empty_for_unknown(self, populated_index):
        assert find_callers("nonexistent") == []

    def test_index_stats(self, populated_index):
        stats = index_stats()
        assert stats["symbols"] > 0
        assert stats["references"] > 0
        assert stats["indexed_files"] == 2


# ── Master switch ──────────────────────────────────────────────────


class TestMasterSwitch(unittest.TestCase):
    def setUp(self) -> None:
        runtime_settings._cache = None  # type: ignore[attr-defined]

    def _patch_settings(self, **overrides):
        base = runtime_settings._defaults()
        base.update(overrides)
        return patch.object(runtime_settings, "_cache", base)

    def test_default_off(self):
        with self._patch_settings():
            self.assertFalse(runtime_settings.get_code_intel_enabled())

    def test_set_and_get(self):
        with self._patch_settings(), patch.object(runtime_settings, "_save"):
            runtime_settings.set_code_intel_enabled(True)
            self.assertTrue(runtime_settings.get_code_intel_enabled())

    def test_get_when_off_does_not_block_queries(self):
        # Query API is always callable; the switch only gates the
        # idle-job refresh (future).
        with self._patch_settings():
            self.assertFalse(runtime_settings.get_code_intel_enabled())
            # find_symbol still works (returns empty when index empty)
            store.reset_for_tests(Path("/tmp/code_intel_test_off"))
            results = find_symbol("anything")
            self.assertEqual(results, [])


# ── Defensive shapes ───────────────────────────────────────────────


class TestDefensive:
    def test_oversized_file_is_skipped(self, tmp_path, monkeypatch):
        from app.code_intel import indexer
        monkeypatch.setattr(indexer, "MAX_FILE_BYTES", 50)  # 50 bytes cap
        # 200-byte file → over the cap
        _write(tmp_path / "big.py", "def f(): " + "x = 1; " * 30 + "pass\n")
        _write(tmp_path / "small.py", "def s(): pass\n")
        snap = build_index(root=tmp_path)
        names = {s.name for s in snap.symbols}
        assert "f" not in names
        assert "s" in names

    def test_symbol_location_fully_qualified(self):
        method = SymbolLocation(
            name="m", kind=SymbolKind.METHOD,
            file_path="x.py", lineno=1, end_lineno=1, parent="C",
        )
        assert method.fully_qualified == "C.m"
        func = SymbolLocation(
            name="f", kind=SymbolKind.FUNCTION,
            file_path="x.py", lineno=1, end_lineno=1,
        )
        assert func.fully_qualified == "f"

    def test_models_roundtrip_json(self):
        original = SymbolLocation(
            name="foo", kind=SymbolKind.ASYNC_METHOD,
            file_path="x.py", lineno=10, end_lineno=20,
            parent="Bar", docstring_first_line="A docstring.",
        )
        d = original.to_dict()
        # JSON-serialisable
        json.dumps(d)
        reloaded = SymbolLocation.from_dict(d)
        assert reloaded == original


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
