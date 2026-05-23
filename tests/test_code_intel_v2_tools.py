"""Tests for the v2 code-intel agent tools — Phase C.2 (2026-05-22).

Covers:
  * ``code_intel_coverage`` — references-to-symbol restricted to a
    test_root prefix
  * ``code_intel_deps`` — sorted+deduped import list from a file's
    AST

Each tool's contract is:
  - input validation (type, empty, traversal, suffix)
  - index-not-built diagnostic for coverage
  - happy paths
  - exception isolation
  - output truncation
"""
from __future__ import annotations

import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

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


from app.code_intel import store as cs_store  # noqa: E402


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


def _call_tool(tool_fn, *args, **kwargs):
    """Invoke a CrewAI-decorated tool regardless of whether the test
    env has the real crewai package (BaseTool wrapper) or the stub
    (function-as-is)."""
    if callable(tool_fn):
        try:
            return tool_fn(*args, **kwargs)
        except TypeError:
            pass
    if hasattr(tool_fn, "func"):
        return tool_fn.func(*args, **kwargs)
    if hasattr(tool_fn, "_run"):
        return tool_fn._run(*args, **kwargs)
    raise RuntimeError(f"cannot invoke tool {tool_fn!r}")


# ============================================================================
# code_intel_coverage — input validation
# ============================================================================


class TestCoverageDefensive(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        cs_store.reset_for_tests(Path(self._tmp))

    def test_rejects_non_string_name(self):
        from app.code_intel.agent_tools import code_intel_coverage
        result = _call_tool(code_intel_coverage, 123)
        self.assertIn("must be a string", result)

    def test_rejects_empty_name(self):
        from app.code_intel.agent_tools import code_intel_coverage
        result = _call_tool(code_intel_coverage, "   ")
        self.assertIn("cannot be empty", result)

    def test_rejects_non_string_test_root(self):
        from app.code_intel.agent_tools import code_intel_coverage
        result = _call_tool(code_intel_coverage, "foo", test_root=123)
        self.assertIn("test_root must be a string", result)

    def test_rejects_empty_test_root(self):
        from app.code_intel.agent_tools import code_intel_coverage
        result = _call_tool(code_intel_coverage, "foo", test_root="   ")
        self.assertIn("test_root cannot be empty", result)

    def test_index_not_built_diagnostic(self):
        from app.code_intel.agent_tools import code_intel_coverage
        result = _call_tool(code_intel_coverage, "foo")
        self.assertIn("has not been built yet", result)


# ============================================================================
# code_intel_coverage — happy paths
# ============================================================================


class TestCoverageHappy:
    def test_returns_only_tests_dir_references(self, tmp_path):
        cs_store.reset_for_tests(tmp_path / "index")
        from app.code_intel import build_index, save_index
        # app/x.py defines my_func; tests/test_x.py + app/y.py both
        # reference it. Coverage should report only the tests/ row.
        _write(tmp_path / "src" / "app" / "x.py", """
            def my_func():
                return 1
        """)
        _write(tmp_path / "src" / "app" / "y.py", """
            from app.x import my_func
            def production_caller():
                return my_func()
        """)
        _write(tmp_path / "src" / "tests" / "test_x.py", """
            from app.x import my_func
            def test_returns_one():
                assert my_func() == 1
        """)
        snap = build_index(root=tmp_path / "src")
        save_index(snap)

        from app.code_intel.agent_tools import code_intel_coverage
        result = _call_tool(code_intel_coverage, "my_func")

        assert "test reference(s)" in result
        assert "tests/" in result
        assert "test_x.py" in result
        # Production caller must NOT appear — coverage is tests-only
        assert "y.py" not in result

    def test_empty_when_no_test_uses_it(self, tmp_path):
        cs_store.reset_for_tests(tmp_path / "index")
        from app.code_intel import build_index, save_index
        _write(tmp_path / "src" / "app" / "x.py", """
            def untested():
                return 1
        """)
        snap = build_index(root=tmp_path / "src")
        save_index(snap)

        from app.code_intel.agent_tools import code_intel_coverage
        result = _call_tool(code_intel_coverage, "untested")
        assert "0 test reference(s)" in result
        assert "no matches" in result

    def test_custom_test_root(self, tmp_path):
        cs_store.reset_for_tests(tmp_path / "index")
        from app.code_intel import build_index, save_index
        # Use a non-standard test root prefix
        _write(tmp_path / "src" / "app" / "x.py", """
            def some_func():
                return 1
        """)
        _write(tmp_path / "src" / "spec" / "x_spec.py", """
            from app.x import some_func
            def test_one():
                assert some_func() == 1
        """)
        snap = build_index(root=tmp_path / "src")
        save_index(snap)

        from app.code_intel.agent_tools import code_intel_coverage
        result = _call_tool(
            code_intel_coverage,
            "some_func",
            test_root="spec/",
        )
        assert "test reference(s)" in result
        assert "spec/" in result
        assert "x_spec.py" in result


# ============================================================================
# code_intel_deps — input validation
# ============================================================================


class TestDepsDefensive(unittest.TestCase):
    def test_rejects_non_string(self):
        from app.code_intel.agent_tools import code_intel_deps
        result = _call_tool(code_intel_deps, 123)
        self.assertIn("must be a string", result)

    def test_rejects_empty(self):
        from app.code_intel.agent_tools import code_intel_deps
        result = _call_tool(code_intel_deps, "   ")
        self.assertIn("cannot be empty", result)

    def test_rejects_absolute_path(self):
        from app.code_intel.agent_tools import code_intel_deps
        result = _call_tool(code_intel_deps, "/etc/passwd")
        self.assertIn("absolute paths refused", result)

    def test_rejects_parent_traversal(self):
        from app.code_intel.agent_tools import code_intel_deps
        result = _call_tool(code_intel_deps, "app/../secrets.py")
        self.assertIn("parent-traversal", result)

    def test_rejects_non_python_suffix(self):
        from app.code_intel.agent_tools import code_intel_deps
        result = _call_tool(code_intel_deps, "README.md")
        self.assertIn("must end in '.py'", result)


# ============================================================================
# code_intel_deps — happy paths
# ============================================================================


class TestDepsHappy:
    def test_extracts_simple_imports(self, tmp_path, monkeypatch):
        # Use cwd so the relative path resolves against tmp_path
        monkeypatch.chdir(tmp_path)
        _write(tmp_path / "subject.py", """
            import os
            import sys
            from pathlib import Path
            from collections import defaultdict, Counter

            def f():
                pass
        """)
        from app.code_intel.agent_tools import code_intel_deps
        result = _call_tool(code_intel_deps, "subject.py")
        assert "module(s) imported" in result
        assert "subject.py" in result
        # Each import name should appear once on its own line
        assert "  os\n" in result + "\n" or " os" in result
        assert "pathlib" in result
        assert "collections" in result
        # Dedup: ``from collections import a, b`` records "collections" once
        assert result.count("collections") == 1

    def test_extracts_relative_imports(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write(tmp_path / "subject.py", """
            from . import sibling
            from .sibling import thing
            from ..parent.child import other
        """)
        from app.code_intel.agent_tools import code_intel_deps
        result = _call_tool(code_intel_deps, "subject.py")
        assert "module(s) imported" in result
        # Relative imports are surfaced in their dotted form
        assert ".sibling" in result
        assert "..parent.child" in result

    def test_missing_file_returns_no_imports(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from app.code_intel.agent_tools import code_intel_deps
        result = _call_tool(code_intel_deps, "ghost.py")
        assert "no imports found" in result

    def test_unparseable_file_returns_no_imports(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.chdir(tmp_path)
        _write(tmp_path / "broken.py", "def f( # syntax error\n")
        from app.code_intel.agent_tools import code_intel_deps
        result = _call_tool(code_intel_deps, "broken.py")
        # find_module_deps is failure-isolated — returns empty list
        assert "no imports found" in result

    def test_sorted_output(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write(tmp_path / "subject.py", """
            import zebra
            import alpha
            import middle
        """)
        from app.code_intel.agent_tools import code_intel_deps
        result = _call_tool(code_intel_deps, "subject.py")
        # The body's module names appear in sorted order (alphabetical)
        a_pos = result.find("alpha")
        m_pos = result.find("middle")
        z_pos = result.find("zebra")
        assert 0 < a_pos < m_pos < z_pos


# ============================================================================
# ALL_CODE_INTEL_TOOLS registry includes both new tools
# ============================================================================


def test_registry_includes_v2_tools():
    from app.code_intel.agent_tools import (
        ALL_CODE_INTEL_TOOLS,
        code_intel_coverage,
        code_intel_deps,
    )
    assert code_intel_coverage in ALL_CODE_INTEL_TOOLS
    assert code_intel_deps in ALL_CODE_INTEL_TOOLS
    # And the count went from 4 to 6
    assert len(ALL_CODE_INTEL_TOOLS) == 6


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
