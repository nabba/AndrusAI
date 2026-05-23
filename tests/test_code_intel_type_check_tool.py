"""Tests for the code_intel_type_check agent tool (2026-05-22).

Wraps app.code_intel.pyright_sidecar for the coder agent. Tests pin:

  * Input validation (non-str, empty, absolute, parent-traversal)
  * Disabled-master-switch user message
  * Unavailable-binary user message
  * Timed-out user message
  * Sidecar error field surfaced
  * Empty-diagnostics → "(no diagnostics)" clean output
  * Mixed-severity diagnostics → grouped error/warning/info in header
  * Truncation at MAX cap with footer
  * Per-line formatting (severity icon, file:line:column, rule, message)
  * ALL_CODE_INTEL_TOOLS export updated
"""
from __future__ import annotations

import sys
import types
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


from app.code_intel.agent_tools import (  # noqa: E402
    ALL_CODE_INTEL_TOOLS,
    code_intel_type_check,
)
from app.code_intel.pyright_sidecar import PyrightDiagnostic, PyrightReport  # noqa: E402


def _invoke(tool, *args, **kwargs):
    """Call the @tool-decorated function, peeling through CrewAI BaseTool
    wrappers when the live crewai package is installed."""
    if hasattr(tool, "func") and callable(tool.func):
        return tool.func(*args, **kwargs)
    if hasattr(tool, "_run") and callable(tool._run):
        return tool._run(*args, **kwargs)
    return tool(*args, **kwargs)


# ── Input validation ─────────────────────────────────────────────────


class TestInputValidation:
    def test_non_string_returns_error(self):
        out = _invoke(code_intel_type_check, 42)
        assert "must be a string" in out

    def test_empty_path_returns_error(self):
        out = _invoke(code_intel_type_check, "   ")
        assert "cannot be empty" in out

    def test_absolute_path_refused(self):
        out = _invoke(code_intel_type_check, "/etc/passwd")
        assert "absolute paths refused" in out

    def test_parent_traversal_refused(self):
        out = _invoke(code_intel_type_check, "app/../etc")
        assert "parent-traversal" in out


# ── Sidecar disabled / unavailable / timed_out / error ───────────────


class TestSidecarStates:
    def test_disabled(self):
        r = PyrightReport(disabled=True, available=True)
        with patch(
            "app.code_intel.check_file", return_value=r,
        ):
            out = _invoke(code_intel_type_check, "app/x.py")
        assert "disabled" in out
        assert "pyright_sidecar_enabled" in out

    def test_unavailable(self):
        r = PyrightReport(disabled=False, available=False)
        with patch(
            "app.code_intel.check_file", return_value=r,
        ):
            out = _invoke(code_intel_type_check, "app/x.py")
        assert "not on PATH" in out
        assert "install pyright" in out

    def test_timed_out(self):
        r = PyrightReport(
            disabled=False, available=True,
            timed_out=True, duration_s=30.0,
        )
        with patch(
            "app.code_intel.check_file", return_value=r,
        ):
            out = _invoke(code_intel_type_check, "app/x.py")
        assert "timed out" in out
        assert "30.0s" in out

    def test_error_field_surfaced(self):
        r = PyrightReport(
            disabled=False, available=True,
            error="parse: invalid JSON",
        )
        with patch(
            "app.code_intel.check_file", return_value=r,
        ):
            out = _invoke(code_intel_type_check, "app/x.py")
        assert "parse: invalid JSON" in out


# ── Clean + diagnostic output ────────────────────────────────────────


class TestDiagnosticOutput:
    def test_no_diagnostics(self):
        r = PyrightReport(diagnostics=[], available=True)
        with patch(
            "app.code_intel.check_file", return_value=r,
        ):
            out = _invoke(code_intel_type_check, "app/clean.py")
        assert "no diagnostics" in out

    def test_mixed_severity_grouped(self):
        diags = [
            PyrightDiagnostic("app/x.py", 5, 1, "warning", "rW", "msg-w"),
            PyrightDiagnostic("app/x.py", 10, 1, "error", "rE", "msg-e"),
            PyrightDiagnostic("app/x.py", 3, 1, "info", "rI", "msg-i"),
        ]
        r = PyrightReport(diagnostics=diags, available=True)
        with patch(
            "app.code_intel.check_file", return_value=r,
        ):
            out = _invoke(code_intel_type_check, "app/x.py")
        # Header has counters
        assert "3 diagnostic" in out
        assert "1 error" in out
        assert "1 warning" in out
        # Severity ordering: error first, warning second, info last
        e_idx = out.find("msg-e")
        w_idx = out.find("msg-w")
        i_idx = out.find("msg-i")
        assert 0 < e_idx < w_idx < i_idx

    def test_truncation_with_footer(self):
        # 25 diagnostics → 20 shown + footer
        diags = [
            PyrightDiagnostic(
                "app/x.py", i + 1, 1, "error", "r", f"err{i}",
            )
            for i in range(25)
        ]
        r = PyrightReport(diagnostics=diags, available=True)
        with patch(
            "app.code_intel.check_file", return_value=r,
        ):
            out = _invoke(code_intel_type_check, "app/x.py")
        assert "and 5 more" in out

    def test_line_format(self):
        d = PyrightDiagnostic(
            "app/x.py", 42, 7, "error", "reportFoo", "bad type",
        )
        r = PyrightReport(diagnostics=[d], available=True)
        with patch(
            "app.code_intel.check_file", return_value=r,
        ):
            out = _invoke(code_intel_type_check, "app/x.py")
        assert "❌" in out
        assert "app/x.py:42:7" in out
        assert "[reportFoo]" in out
        assert "bad type" in out


# ── Wiring ───────────────────────────────────────────────────────────


class TestWiring:
    def test_type_check_in_all_tools_export(self):
        assert code_intel_type_check in ALL_CODE_INTEL_TOOLS
        # Family grows by additive composition — assert minimum
        # cardinality so adding new tools (Phase C.2 added two; future
        # phases may add more) doesn't break this contract test.
        # Type-check is the load-bearing member here.
        assert len(ALL_CODE_INTEL_TOOLS) >= 4


# ── Exception isolation ──────────────────────────────────────────────


class TestExceptionIsolation:
    def test_check_file_raises_returns_user_friendly_error(self):
        with patch(
            "app.code_intel.check_file", side_effect=RuntimeError("boom"),
        ):
            out = _invoke(code_intel_type_check, "app/x.py")
        # User-facing error, no traceback
        assert "code_intel_type_check failed" in out
        assert "RuntimeError" in out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
