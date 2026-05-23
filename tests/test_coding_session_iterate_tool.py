"""Tests for the CodingSessionIterateTool — Phase A.1 closure
(2026-05-22).

Closes the orphan-code gap from the audit: the iterate_until_green
primitive now has a real agent-callable entry point. This tool is
the FIRST production caller of the iterate loop.

Covers:
  * Master switch OFF → status "disabled" returned as JSON
  * Master switch ON + session not found → ERROR
  * Session not ACTIVE → REFUSED
  * Target file not in worktree → REFUSED
  * Happy path: iterate runs, outcome serialized to JSON
  * Master switch ON + pyright sidecar OFF → type_check_enabled=False
  * Master switch ON + pyright sidecar ON → type_checker passed
    (per-iteration type errors flow into diagnosis)
  * Tool registered in the factory output
"""
from __future__ import annotations

import json
import sys
import types
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


def _import_tool_factory():
    try:
        from app.tools.coding_session_tools import create_coding_session_tools
        return create_coding_session_tools
    except Exception as exc:
        pytest.skip(f"coding_session_tools unavailable: {exc}")


def _make_session_stub(*, sid="sess-1", active=True, worktree_path=None):
    """Lightweight CodingSession stub the tool can interrogate.

    Real CodingSession needs the full dataclass; this stub mirrors the
    handful of attrs the iterate tool reads.
    """
    from types import SimpleNamespace
    status_active = SimpleNamespace(value="active")
    status_done = SimpleNamespace(value="submitted")
    cs = SimpleNamespace(
        id=sid,
        agent_id="coder",
        is_active=active,
        status=status_active if active else status_done,
        worktree_path=str(worktree_path or "/tmp/wt"),
        bytes_written=0,
        files_touched=set(),
        run_count=0,
        config=SimpleNamespace(max_run_timeout_s=120),
    )
    return cs


# ── Factory wiring ───────────────────────────────────────────────────


class TestFactoryWiring:
    def test_tool_in_factory_output(self):
        factory = _import_tool_factory()
        tools = factory()
        if not tools:
            pytest.skip("factory returned empty (crewai stub)")
        names = {t.name for t in tools if hasattr(t, "name")}
        assert "coding_session_iterate" in names

    def test_factory_returns_nine_tools(self):
        factory = _import_tool_factory()
        tools = factory()
        if not tools:
            pytest.skip("factory returned empty")
        # 8 originals + iterate = 9
        assert len(tools) == 9


def _get_iterate_tool():
    factory = _import_tool_factory()
    tools = factory()
    if not tools:
        pytest.skip("factory returned empty")
    for t in tools:
        if getattr(t, "name", "") == "coding_session_iterate":
            return t
    pytest.skip("iterate tool not in inventory")


def _invoke(tool, **kwargs):
    if hasattr(tool, "_run") and callable(tool._run):
        return tool._run(**kwargs)
    if hasattr(tool, "func") and callable(tool.func):
        return tool.func(**kwargs)
    return tool(**kwargs)


# ── Master switch OFF ────────────────────────────────────────────────


class TestMasterSwitchOff:
    def test_disabled_returned(self, monkeypatch):
        tool = _get_iterate_tool()
        try:
            from app import runtime_settings as rs
        except Exception as exc:
            pytest.skip(f"runtime_settings unavailable: {exc}")
        monkeypatch.setattr(rs, "get_iterate_loop_enabled", lambda: False)
        monkeypatch.setattr(rs, "get_pyright_sidecar_enabled", lambda: False)

        result = _invoke(
            tool,
            session_id="any",
            target_file="x.py",
            test_argv=["pytest"],
        )
        payload = json.loads(result)
        assert payload["status"] == "disabled"
        assert "iterate_loop_enabled" in payload["reason"]


# ── Master switch ON, session lookup paths ───────────────────────────


class TestSessionLookup:
    def _enable(self, monkeypatch):
        try:
            from app import runtime_settings as rs
        except Exception as exc:
            pytest.skip(f"runtime_settings unavailable: {exc}")
        monkeypatch.setattr(rs, "get_iterate_loop_enabled", lambda: True)
        monkeypatch.setattr(rs, "get_pyright_sidecar_enabled", lambda: False)

    def test_session_not_found(self, monkeypatch):
        tool = _get_iterate_tool()
        self._enable(monkeypatch)

        from app.coding_session import runtime
        fake_mgr = MagicMock()
        fake_mgr.get.return_value = None
        monkeypatch.setattr(runtime, "get_manager", lambda: fake_mgr)

        result = _invoke(
            tool, session_id="missing",
            target_file="x.py", test_argv=["pytest"],
        )
        assert result.startswith("ERROR:")
        assert "not found" in result

    def test_session_not_active(self, monkeypatch):
        tool = _get_iterate_tool()
        self._enable(monkeypatch)

        from app.coding_session import runtime
        cs = _make_session_stub(active=False)
        fake_mgr = MagicMock()
        fake_mgr.get.return_value = cs
        monkeypatch.setattr(runtime, "get_manager", lambda: fake_mgr)

        result = _invoke(
            tool, session_id="x",
            target_file="x.py", test_argv=["pytest"],
        )
        assert result.startswith("REFUSED:")
        assert "not ACTIVE" in result

    def test_target_file_not_in_worktree(self, monkeypatch, tmp_path):
        tool = _get_iterate_tool()
        self._enable(monkeypatch)

        from app.coding_session import runtime
        cs = _make_session_stub(worktree_path=tmp_path)
        fake_mgr = MagicMock()
        fake_mgr.get.return_value = cs
        monkeypatch.setattr(runtime, "get_manager", lambda: fake_mgr)

        # Don't create the file
        result = _invoke(
            tool, session_id="x",
            target_file="missing.py", test_argv=["pytest"],
        )
        assert result.startswith("REFUSED:")
        assert "not in worktree" in result


# ── Happy path ───────────────────────────────────────────────────────


class TestHappyPath:
    def test_iterate_invoked_with_correct_callbacks(
        self, monkeypatch, tmp_path,
    ):
        tool = _get_iterate_tool()
        try:
            from app import runtime_settings as rs
        except Exception as exc:
            pytest.skip(f"runtime_settings unavailable: {exc}")
        monkeypatch.setattr(rs, "get_iterate_loop_enabled", lambda: True)
        monkeypatch.setattr(rs, "get_pyright_sidecar_enabled", lambda: False)

        # Create the target file
        target = tmp_path / "x.py"
        target.write_text("def f(): pass\n", encoding="utf-8")

        from app.coding_session import runtime
        cs = _make_session_stub(worktree_path=tmp_path)
        fake_mgr = MagicMock()
        fake_mgr.get.return_value = cs
        fake_mgr.config = cs.config
        fake_mgr.backend = MagicMock()
        fake_mgr.backend.read_worktree_file.return_value = "src"
        monkeypatch.setattr(runtime, "get_manager", lambda: fake_mgr)

        # Mock iterate_until_green so we can inspect what callbacks
        # the tool built
        captured = {}

        def _fake_iterate(*, target_file, test_runner, file_reader,
                          file_writer, type_checker, config,
                          pattern_signature, error_class):
            captured["target_file"] = target_file
            captured["type_checker"] = type_checker
            captured["config"] = config
            captured["pattern_signature"] = pattern_signature
            from types import SimpleNamespace
            outcome = SimpleNamespace()
            outcome.iterations = 1
            outcome.as_jsonable = lambda: {
                "status": "passed",
                "iterations": 1,
                "cost_usd": 0.001,
                "fixes_applied": [],
                "last_test_result": {"ok": True},
                "last_decline_reason": "",
                "error_text": "",
                "type_errors": [],
            }
            return outcome

        import app.tools.coding_session_tools as cst_mod
        # The iterate import is inside the tool body via from-import.
        # Patch at the source module so the late-binding picks up the fake.
        from app.coding_session import iterate as iterate_mod
        monkeypatch.setattr(
            iterate_mod, "iterate_until_green", _fake_iterate,
        )

        result = _invoke(
            tool,
            session_id="sess-1",
            target_file="x.py",
            test_argv=["pytest", "tests/test_x.py"],
            max_iterations=5,
            budget_usd=0.5,
        )
        payload = json.loads(result)
        assert payload["status"] == "passed"
        assert payload["session_id"] == "sess-1"
        assert payload["target_file"] == "x.py"
        assert payload["type_check_enabled"] is False
        # Callbacks were properly wired
        assert captured["target_file"] == "x.py"
        # No type_checker when sidecar off
        assert captured["type_checker"] is None
        # Config carries the tool's params + run_type_check=False
        assert captured["config"].max_iterations == 5
        assert captured["config"].budget_usd == 0.5
        assert captured["config"].run_type_check is False
        assert "iterate_tool" in captured["pattern_signature"]


# ── Type-checker wiring when sidecar ON ──────────────────────────────


class TestTypeCheckerWiring:
    def test_type_checker_passed_when_sidecar_on(
        self, monkeypatch, tmp_path,
    ):
        tool = _get_iterate_tool()
        try:
            from app import runtime_settings as rs
        except Exception as exc:
            pytest.skip(f"runtime_settings unavailable: {exc}")
        monkeypatch.setattr(rs, "get_iterate_loop_enabled", lambda: True)
        monkeypatch.setattr(rs, "get_pyright_sidecar_enabled", lambda: True)

        target = tmp_path / "x.py"
        target.write_text("def f(): pass\n", encoding="utf-8")

        from app.coding_session import runtime
        cs = _make_session_stub(worktree_path=tmp_path)
        fake_mgr = MagicMock()
        fake_mgr.get.return_value = cs
        fake_mgr.config = cs.config
        fake_mgr.backend = MagicMock()
        monkeypatch.setattr(runtime, "get_manager", lambda: fake_mgr)

        captured = {}

        def _fake_iterate(*, target_file, test_runner, file_reader,
                          file_writer, type_checker, config,
                          pattern_signature, error_class):
            captured["type_checker"] = type_checker
            captured["run_type_check"] = config.run_type_check
            from types import SimpleNamespace
            outcome = SimpleNamespace()
            outcome.iterations = 0
            outcome.as_jsonable = lambda: {
                "status": "passed", "iterations": 0,
                "cost_usd": 0.0, "fixes_applied": [],
                "last_test_result": None, "last_decline_reason": "",
                "error_text": "", "type_errors": [],
            }
            return outcome

        from app.coding_session import iterate as iterate_mod
        monkeypatch.setattr(
            iterate_mod, "iterate_until_green", _fake_iterate,
        )

        result = _invoke(
            tool, session_id="sess-1",
            target_file="x.py", test_argv=["pytest"],
        )
        payload = json.loads(result)
        assert payload["type_check_enabled"] is True
        # Type checker is callable, not None
        assert captured["type_checker"] is not None
        assert callable(captured["type_checker"])
        assert captured["run_type_check"] is True


# ── runtime_settings failure-isolated ────────────────────────────────


class TestRuntimeSettingsFailureIsolated:
    def test_broken_runtime_settings_returns_disabled(self, monkeypatch):
        tool = _get_iterate_tool()
        try:
            from app import runtime_settings as rs
        except Exception as exc:
            pytest.skip(f"runtime_settings unavailable: {exc}")

        def _boom():
            raise RuntimeError("rs sick")
        monkeypatch.setattr(rs, "get_iterate_loop_enabled", _boom)

        result = _invoke(
            tool, session_id="x",
            target_file="x.py", test_argv=["pytest"],
        )
        payload = json.loads(result)
        assert payload["status"] == "disabled"
        assert "runtime_settings unavailable" in payload["reason"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
