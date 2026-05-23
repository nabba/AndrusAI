"""Tests for the code_intel refresh job + agent tools (2026-05-20).

Covers Phase 3 piece 1b:
  * refresh: master switch off → no-op
  * refresh: cadence guard prevents re-fire within window
  * refresh: force=True bypasses cadence
  * refresh: build+save integration happy path
  * refresh: build exception captured in state + reported
  * refresh: missing app_root handled gracefully
  * idle_scheduler registers "code-intel-refresh" tuple
  * agent tools: format output for empty index
  * agent tools: format output with results
  * agent tools: defensive on non-string + empty input
  * agent tools: exception in query returns formatted error
"""
from __future__ import annotations

import os
import sys
import textwrap
import time
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
from app.code_intel import store as cs_store  # noqa: E402
from app.code_intel import refresh as cs_refresh  # noqa: E402


def _patch_settings(**overrides):
    base = runtime_settings._defaults()
    base.update(overrides)
    return patch.object(runtime_settings, "_cache", base)


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


@pytest.fixture(autouse=True)
def _reset_runtime_cache():
    runtime_settings._cache = None  # type: ignore[attr-defined]
    yield
    runtime_settings._cache = None  # type: ignore[attr-defined]


# ============================================================================
# Refresh — master switch
# ============================================================================


class TestRefreshMasterSwitch:
    def test_master_switch_off_is_noop(self, tmp_path):
        cs_store.reset_for_tests(tmp_path / "index")
        cs_refresh.reset_state_for_tests()
        with _patch_settings(code_intel_enabled=False):
            out = cs_refresh.run_refresh()
        assert out["ran"] is False
        assert out["skipped_reason"] == "master_switch_off"

    def test_master_switch_on_runs_refresh(self, tmp_path, monkeypatch):
        cs_store.reset_for_tests(tmp_path / "index")
        cs_refresh.reset_state_for_tests()
        # Point CODE_INTEL_ROOT at a tiny tmp directory with one file
        src = tmp_path / "src"
        _write(src / "x.py", "def f(): pass\n")
        monkeypatch.setenv("CODE_INTEL_ROOT", str(src))
        with _patch_settings(code_intel_enabled=True):
            out = cs_refresh.run_refresh()
        assert out["ran"] is True
        assert out["stats"]["symbols"] >= 1


# ============================================================================
# Refresh — cadence
# ============================================================================


class TestRefreshCadence:
    def test_cadence_guard_blocks_second_call(
        self, tmp_path, monkeypatch,
    ):
        cs_store.reset_for_tests(tmp_path / "index")
        cs_refresh.reset_state_for_tests()
        _write(tmp_path / "src" / "x.py", "def f(): pass\n")
        monkeypatch.setenv("CODE_INTEL_ROOT", str(tmp_path / "src"))
        # Default cadence is 30 min — second call within 30 min skips.
        with _patch_settings(code_intel_enabled=True):
            first = cs_refresh.run_refresh()
            second = cs_refresh.run_refresh()
        assert first["ran"] is True
        assert second["ran"] is False
        assert "cadence_guard" in second["skipped_reason"]

    def test_force_bypasses_cadence(self, tmp_path, monkeypatch):
        cs_store.reset_for_tests(tmp_path / "index")
        cs_refresh.reset_state_for_tests()
        _write(tmp_path / "src" / "x.py", "def f(): pass\n")
        monkeypatch.setenv("CODE_INTEL_ROOT", str(tmp_path / "src"))
        with _patch_settings(code_intel_enabled=True):
            first = cs_refresh.run_refresh()
            second = cs_refresh.run_refresh(force=True)
        assert first["ran"] is True
        assert second["ran"] is True

    def test_env_override_cadence(self, tmp_path, monkeypatch):
        cs_store.reset_for_tests(tmp_path / "index")
        cs_refresh.reset_state_for_tests()
        _write(tmp_path / "src" / "x.py", "def f(): pass\n")
        monkeypatch.setenv("CODE_INTEL_ROOT", str(tmp_path / "src"))
        # Set cadence to 1s; second call after 1.2s should fire again.
        monkeypatch.setenv("CODE_INTEL_CADENCE_S", "1")
        with _patch_settings(code_intel_enabled=True):
            first = cs_refresh.run_refresh()
            time.sleep(1.2)
            second = cs_refresh.run_refresh()
        assert first["ran"] is True
        assert second["ran"] is True

    def test_force_works_when_master_switch_off(
        self, tmp_path, monkeypatch,
    ):
        # Operator-initiated rebuild path: force=True bypasses the
        # master switch too, so manual recovery isn't blocked by a
        # config flip.
        cs_store.reset_for_tests(tmp_path / "index")
        cs_refresh.reset_state_for_tests()
        _write(tmp_path / "src" / "x.py", "def f(): pass\n")
        monkeypatch.setenv("CODE_INTEL_ROOT", str(tmp_path / "src"))
        with _patch_settings(code_intel_enabled=False):
            out = cs_refresh.run_refresh(force=True)
        assert out["ran"] is True


# ============================================================================
# Refresh — failure handling
# ============================================================================


class TestRefreshFailures:
    def test_missing_root_recorded_as_error(self, tmp_path, monkeypatch):
        cs_store.reset_for_tests(tmp_path / "index")
        cs_refresh.reset_state_for_tests()
        monkeypatch.setenv(
            "CODE_INTEL_ROOT",
            str(tmp_path / "nonexistent"),
        )
        with _patch_settings(code_intel_enabled=True):
            out = cs_refresh.run_refresh()
        assert out["ran"] is False
        assert out["skipped_reason"] == "root_missing"
        assert "not found" in out["error"]

    def test_build_exception_recorded(self, tmp_path, monkeypatch):
        cs_store.reset_for_tests(tmp_path / "index")
        cs_refresh.reset_state_for_tests()
        _write(tmp_path / "src" / "x.py", "def f(): pass\n")
        monkeypatch.setenv("CODE_INTEL_ROOT", str(tmp_path / "src"))

        def _boom(**kw):
            raise RuntimeError("indexer exploded")

        # ``build_index`` is imported INSIDE run_refresh; patch at
        # the indexer module's binding (the actual source of truth).
        from app.code_intel import indexer as cs_indexer
        monkeypatch.setattr(cs_indexer, "build_index", _boom)

        with _patch_settings(code_intel_enabled=True):
            out = cs_refresh.run_refresh()
        assert out["ran"] is False
        assert out["skipped_reason"] == "exception"
        assert "indexer exploded" in out["error"]


# ============================================================================
# idle_scheduler registration
# ============================================================================


class TestSchedulerRegistration:
    def test_code_intel_refresh_tuple_present(self):
        from app.idle_scheduler import JobWeight, _default_jobs
        jobs = _default_jobs()
        matching = [j for j in jobs if j[0] == "code-intel-refresh"]
        assert len(matching) == 1
        name, fn, weight = matching[0]
        assert weight == JobWeight.HEAVY
        assert callable(fn)

    def test_tuple_function_is_master_switch_gated(self):
        from app.idle_scheduler import _default_jobs
        jobs = _default_jobs()
        matching = [j for j in jobs if j[0] == "code-intel-refresh"]
        _, fn, _ = matching[0]
        # Default settings have code_intel_enabled=False — call is no-op.
        with _patch_settings(code_intel_enabled=False):
            fn()  # should not raise


# ============================================================================
# Agent tools — output formatting
# ============================================================================


class TestAgentToolsEmptyIndex(unittest.TestCase):
    def setUp(self) -> None:
        # Reset to a fresh tmp index dir so is_built() returns False.
        from pathlib import Path
        import tempfile
        self._tmp = tempfile.mkdtemp()
        cs_store.reset_for_tests(Path(self._tmp))

    def test_find_symbol_when_index_not_built(self):
        from app.code_intel.agent_tools import code_intel_find_symbol
        # CrewAI's @tool wraps the function; calling via .func bypasses
        # the BaseTool wrapping. The stub @tool in the test environment
        # returns the function as-is, so calling directly works either way.
        result = _call_tool(code_intel_find_symbol, "anything")
        self.assertIn("has not been built yet", result)

    def test_find_references_when_index_not_built(self):
        from app.code_intel.agent_tools import code_intel_find_references
        result = _call_tool(code_intel_find_references, "anything")
        self.assertIn("has not been built yet", result)

    def test_find_callers_when_index_not_built(self):
        from app.code_intel.agent_tools import code_intel_find_callers
        result = _call_tool(code_intel_find_callers, "anything")
        self.assertIn("has not been built yet", result)


def _call_tool(tool_fn, *args, **kwargs):
    """Invoke a CrewAI-decorated tool function regardless of whether
    we're in a stubbed env (function returned as-is) or a real env
    (BaseTool instance). The stub env path is direct call; the real
    env path uses .func or ._run depending on CrewAI version."""
    if callable(tool_fn):
        try:
            return tool_fn(*args, **kwargs)
        except TypeError:
            pass
    # Real CrewAI BaseTool surface
    if hasattr(tool_fn, "func"):
        return tool_fn.func(*args, **kwargs)
    if hasattr(tool_fn, "_run"):
        return tool_fn._run(*args, **kwargs)
    raise RuntimeError(f"cannot invoke tool {tool_fn!r}")


class TestAgentToolsWithIndex:
    def test_find_symbol_returns_formatted_results(
        self, tmp_path, monkeypatch,
    ):
        cs_store.reset_for_tests(tmp_path / "index")
        # Build a tiny index.
        from app.code_intel import build_index, save_index
        _write(tmp_path / "src" / "x.py", """
            def my_function():
                '''Brief docstring.'''
                return 1

            class MyClass:
                def my_method(self):
                    return 2
        """)
        snap = build_index(root=tmp_path / "src")
        save_index(snap)

        from app.code_intel.agent_tools import code_intel_find_symbol
        result = _call_tool(code_intel_find_symbol, "my_function")
        assert "definition" in result
        assert "my_function" in result
        assert "Brief docstring" in result
        assert "x.py:" in result

    def test_find_symbol_no_match_reports_zero(self, tmp_path):
        cs_store.reset_for_tests(tmp_path / "index")
        from app.code_intel import build_index, save_index
        _write(tmp_path / "src" / "x.py", "def f(): pass\n")
        snap = build_index(root=tmp_path / "src")
        save_index(snap)
        from app.code_intel.agent_tools import code_intel_find_symbol
        result = _call_tool(code_intel_find_symbol, "nonexistent_name")
        assert "0 definition" in result
        assert "no matches" in result

    def test_find_references_returns_formatted_results(
        self, tmp_path,
    ):
        cs_store.reset_for_tests(tmp_path / "index")
        from app.code_intel import build_index, save_index
        _write(tmp_path / "src" / "x.py", """
            def my_func():
                return 1

            def caller():
                return my_func() + my_func()
        """)
        snap = build_index(root=tmp_path / "src")
        save_index(snap)
        from app.code_intel.agent_tools import code_intel_find_references
        result = _call_tool(code_intel_find_references, "my_func")
        assert "reference(s)" in result

    def test_find_callers_returns_formatted_results(self, tmp_path):
        cs_store.reset_for_tests(tmp_path / "index")
        from app.code_intel import build_index, save_index
        _write(tmp_path / "src" / "x.py", """
            def my_func():
                return 1

            def caller_one():
                return my_func()

            def caller_two():
                return my_func() + 1
        """)
        snap = build_index(root=tmp_path / "src")
        save_index(snap)
        from app.code_intel.agent_tools import code_intel_find_callers
        result = _call_tool(code_intel_find_callers, "my_func")
        assert "caller(s) of 'my_func'" in result
        assert "caller_one" in result
        assert "caller_two" in result


# ============================================================================
# Agent tools — defensive on bad inputs
# ============================================================================


class TestAgentToolsDefensive(unittest.TestCase):
    def setUp(self) -> None:
        from pathlib import Path
        import tempfile
        self._tmp = tempfile.mkdtemp()
        cs_store.reset_for_tests(Path(self._tmp))

    def test_find_symbol_rejects_non_string(self):
        from app.code_intel.agent_tools import code_intel_find_symbol
        result = _call_tool(code_intel_find_symbol, 123)
        self.assertIn("must be a string", result)

    def test_find_symbol_rejects_empty_string(self):
        from app.code_intel.agent_tools import code_intel_find_symbol
        result = _call_tool(code_intel_find_symbol, "   ")
        self.assertIn("cannot be empty", result)

    def test_find_references_rejects_non_string(self):
        from app.code_intel.agent_tools import code_intel_find_references
        result = _call_tool(code_intel_find_references, None)
        self.assertIn("must be a string", result)

    def test_find_callers_rejects_empty(self):
        from app.code_intel.agent_tools import code_intel_find_callers
        result = _call_tool(code_intel_find_callers, "")
        self.assertIn("cannot be empty", result)


# ============================================================================
# Truncation
# ============================================================================


class TestTruncation:
    def test_excess_results_truncated_with_footer(self, tmp_path):
        cs_store.reset_for_tests(tmp_path / "index")
        from app.code_intel import build_index, save_index
        # Build a file with 30 functions all called "f" via class
        # bodies (each method is named differently to be valid Python
        # but we'll search for a common reference name).
        funcs = "\n".join(
            f"def fn_{i}():\n    return common_target()\n"
            for i in range(30)
        )
        _write(tmp_path / "src" / "x.py", funcs + "\ndef common_target(): pass\n")
        snap = build_index(root=tmp_path / "src")
        save_index(snap)

        from app.code_intel.agent_tools import code_intel_find_callers
        result = _call_tool(code_intel_find_callers, "common_target")
        # All 30 callers reported in count, but only 25 shown.
        assert "30 caller(s)" in result
        assert "more (use file_prefix" in result


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
