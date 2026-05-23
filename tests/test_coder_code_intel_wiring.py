"""Tests for the coder × code_intel agent-tools wire-in (2026-05-22).

Pins that:
  * Both create paths (legacy + LoadableAgent) extend their tools
    list with ``ALL_CODE_INTEL_TOOLS`` under the optional_tool_group
    seam.
  * Backstory mentions code_intel_type_check so the LLM has prompt-
    level guidance to actually call it.
  * Failure-isolated: if code_intel imports fail, the rest of the
    coder still builds.

We don't try to construct the real Agent (heavy LLM bootstrap on
host); we patch out the Agent class and assert what tools were
collected before construction.
"""
from __future__ import annotations

import sys
import types
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


# ── Static checks against the source ─────────────────────────────────


class TestCoderSourceContainsWiring:
    """Lightweight checks that the wire-in is present in the file.
    Avoids the heavy import path the runtime would trigger."""

    def _read_coder(self):
        from pathlib import Path
        src_path = (
            Path(__file__).resolve().parent.parent
            / "app" / "agents" / "coder.py"
        )
        return src_path.read_text(encoding="utf-8")

    def test_legacy_path_has_code_intel_group(self):
        src = self._read_coder()
        assert 'optional_tool_group("coder", "code_intel")' in src

    def test_legacy_path_extends_all_tools(self):
        src = self._read_coder()
        # The wire-in extends with ALL_CODE_INTEL_TOOLS — pin both
        # references (legacy + loadable)
        count = src.count("ALL_CODE_INTEL_TOOLS")
        assert count >= 2, (
            f"expected ALL_CODE_INTEL_TOOLS referenced in BOTH legacy "
            f"and loadable paths, got {count}"
        )

    def test_backstory_mentions_type_check(self):
        src = self._read_coder()
        assert "code_intel_type_check" in src
        assert "AFTER editing" in src or "after editing" in src.lower()

    def test_backstory_mentions_find_symbol(self):
        src = self._read_coder()
        assert "code_intel_find_symbol" in src


# ── ALL_CODE_INTEL_TOOLS shape ───────────────────────────────────────


class TestAllToolsExport:
    def test_contains_core_four_tools(self):
        from app.code_intel.agent_tools import (
            ALL_CODE_INTEL_TOOLS,
            code_intel_find_symbol,
            code_intel_find_references,
            code_intel_find_callers,
            code_intel_type_check,
        )
        # The core four are load-bearing for the coder agent; later
        # phases (C.2 added coverage + deps) extend additively, so
        # we assert minimum cardinality + membership rather than ==.
        for tool in (
            code_intel_find_symbol,
            code_intel_find_references,
            code_intel_find_callers,
            code_intel_type_check,
        ):
            assert tool in ALL_CODE_INTEL_TOOLS
        assert len(ALL_CODE_INTEL_TOOLS) >= 4

    def test_type_check_in_export(self):
        from app.code_intel.agent_tools import (
            ALL_CODE_INTEL_TOOLS,
            code_intel_type_check,
        )
        assert code_intel_type_check in ALL_CODE_INTEL_TOOLS


# ── Failure-isolation around the optional_tool_group ─────────────────


class TestFailureIsolation:
    def test_optional_tool_group_swallows_import_failure(self):
        """If a tool group's import raises, the wrap logs but the
        outer coder build continues. Pins by exercising the helper
        directly with a failing block."""
        from app.agents._common import optional_tool_group
        # No exception should escape — the wrap catches.
        with optional_tool_group("coder", "code_intel"):
            raise ModuleNotFoundError("simulated missing")
        # Reached this line → wrap absorbed the exception
        assert True

    def test_optional_tool_group_logs_generic_exception(self):
        from app.agents._common import optional_tool_group
        with optional_tool_group("coder", "code_intel"):
            raise RuntimeError("simulated transient")
        assert True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
