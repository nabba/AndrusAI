"""Tests for auto-enable pyright on coding_session_submit (2026-05-22).

When both ``auto_type_check_on_submit_enabled`` and
``pyright_sidecar_enabled`` are ON, the ``coding_session_submit``
agent tool defaults ``with_type_check=True`` so the agent gets
type-error metadata attached to every CR fanout without remembering
to opt in.

Covers:
  * Both OFF (default) → submit_session called with with_type_check=False
  * Only auto ON → with_type_check=False (sidecar still gated)
  * Only sidecar ON → with_type_check=False (auto still gated)
  * Both ON → with_type_check=True (the load-bearing case)
  * runtime_settings unavailable → with_type_check=False (failure-isolated)
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


def _make_submit_tool(monkeypatch, *, auto, sidecar, capture):
    """Build a CodingSessionSubmitTool wired with stubbed settings
    + a fake submit_session that records the with_type_check value."""
    # Mock the runtime_settings module's getters
    try:
        from app import runtime_settings as rs
    except Exception as exc:
        pytest.skip(f"runtime_settings unavailable: {exc}")
    monkeypatch.setattr(
        rs, "get_auto_type_check_on_submit_enabled", lambda: auto,
    )
    monkeypatch.setattr(
        rs, "get_pyright_sidecar_enabled", lambda: sidecar,
    )

    # Now build the tool factory
    try:
        from app.tools.coding_session_tools import (
            create_coding_session_tools,
        )
    except Exception as exc:
        pytest.skip(f"coding_session_tools import failed: {exc}")

    tools = create_coding_session_tools()
    if not tools:
        pytest.skip("coding_session_tools returned empty (crewai stub)")

    submit_tool = next(
        (t for t in tools if getattr(t, "name", "") == "coding_session_submit"),
        None,
    )
    if submit_tool is None:
        pytest.skip("submit tool not in inventory")

    # Stub submit_session so we capture with_type_check without running
    # the real lifecycle
    def _fake_submit_session(
        session_id, *, submit_reason, manager, with_type_check=False,
        **_kw,
    ):
        capture["session_id"] = session_id
        capture["with_type_check"] = with_type_check
        return (MagicMock(), [])

    from app.coding_session import submit as submit_mod
    monkeypatch.setattr(
        submit_mod, "submit_session", _fake_submit_session,
    )

    # Also stub manager so we don't hit the real backend
    from app.coding_session import runtime
    monkeypatch.setattr(runtime, "get_manager", lambda: MagicMock())

    return submit_tool


def _invoke_tool(tool, **kwargs):
    """Call the @tool-decorated function, peeling through CrewAI BaseTool
    wrappers when the live crewai package is installed."""
    if hasattr(tool, "_run") and callable(tool._run):
        return tool._run(**kwargs)
    if hasattr(tool, "func") and callable(tool.func):
        return tool.func(**kwargs)
    return tool(**kwargs)


class TestAutoTypeCheckSubmit:
    def test_both_off_default(self, monkeypatch):
        captured: dict = {}
        tool = _make_submit_tool(
            monkeypatch, auto=False, sidecar=False, capture=captured,
        )
        _invoke_tool(tool, session_id="s1", reason="test")
        assert captured["with_type_check"] is False

    def test_auto_on_sidecar_off(self, monkeypatch):
        captured: dict = {}
        tool = _make_submit_tool(
            monkeypatch, auto=True, sidecar=False, capture=captured,
        )
        _invoke_tool(tool, session_id="s1", reason="test")
        # Auto wants type-check but sidecar is off → stays False
        assert captured["with_type_check"] is False

    def test_auto_off_sidecar_on(self, monkeypatch):
        captured: dict = {}
        tool = _make_submit_tool(
            monkeypatch, auto=False, sidecar=True, capture=captured,
        )
        _invoke_tool(tool, session_id="s1", reason="test")
        # Sidecar available but operator hasn't opted into auto → False
        assert captured["with_type_check"] is False

    def test_both_on_auto_enables(self, monkeypatch):
        captured: dict = {}
        tool = _make_submit_tool(
            monkeypatch, auto=True, sidecar=True, capture=captured,
        )
        _invoke_tool(tool, session_id="s1", reason="test")
        # The load-bearing case: both ON → auto-flips to True
        assert captured["with_type_check"] is True

    def test_runtime_settings_unavailable_fails_safe(self, monkeypatch):
        """If runtime_settings is broken, the auto-check defaults OFF
        rather than crashing the submit."""
        captured: dict = {}
        # First build the tool with both ON
        tool = _make_submit_tool(
            monkeypatch, auto=True, sidecar=True, capture=captured,
        )
        # Then replace runtime_settings with one that raises
        from app import runtime_settings as rs

        def _boom():
            raise RuntimeError("settings sick")
        monkeypatch.setattr(
            rs, "get_auto_type_check_on_submit_enabled", _boom,
        )

        _invoke_tool(tool, session_id="s1", reason="test")
        # Failed lookup → stays False, submit still proceeds
        assert captured["with_type_check"] is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
