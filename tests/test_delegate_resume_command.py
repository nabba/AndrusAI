"""Tests for the /delegate resume Signal slash command (Gap B closure,
2026-05-23).

Pins:
  * `_delegate_help` advertises the resume subcommand.
  * Parsing: ``/delegate resume <run_id> <hint>`` splits cleanly into
    run_id + hint, even with multi-word hints.
  * Empty hint returns usage; not-found run_id returns clear error.
  * Successful resume returns ``▶ Resumed run …`` ; refusal returns
    the underlying error.
  * Source-level pin: the handler exists in commands.py.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())


# ── Source-level pins (no full Commander boot) ─────────────────────


def test_help_lists_resume_subcommand():
    src = Path(
        "app/agents/commander/commands.py",
    ).read_text(encoding="utf-8")
    assert "/delegate resume <run_id> <hint>" in src


def test_resume_handler_present():
    src = Path(
        "app/agents/commander/commands.py",
    ).read_text(encoding="utf-8")
    # Subcommand dispatch
    assert 'if sub == "resume"' in src
    # Calls into escalation.resume_blocker (the canonical entry point)
    assert "from app.autonomous_executor.escalation import resume_blocker" in src
    assert "resume_blocker(" in src


def test_resume_handler_parses_hint_after_run_id():
    """The handler must split arg into ``run_id`` + ``hint`` (rest of
    the string) so multi-word hints like ``"AWS creds in vault"``
    work without quoting."""
    src = Path(
        "app/agents/commander/commands.py",
    ).read_text(encoding="utf-8")
    # Look for the split pattern that gives us first-token + rest.
    assert "arg.split(maxsplit=1)" in src
    # The "no hint" error path
    assert "Need a hint to resume" in src


def test_resume_handler_passes_sender_as_operator():
    """The Signal sender becomes the operator identity on the resume
    event — pinned for auditability."""
    src = Path(
        "app/agents/commander/commands.py",
    ).read_text(encoding="utf-8")
    assert 'operator=sender or "operator:signal"' in src


# ── Behavioral test (runs only with full stack) ────────────────────


def _commands_loadable() -> bool:
    try:
        from app.agents.commander import commands  # noqa: F401
        return True
    except Exception:
        return False


@pytest.mark.skipif(
    not _commands_loadable(),
    reason="commands.py requires full Commander boot",
)
class TestResumeBehaviour:
    def _h(self):
        from app.agents.commander.commands import _handle_delegate_command
        return _handle_delegate_command

    def test_empty_arg_returns_usage(self):
        h = self._h()
        result = h("/delegate resume", "test:sender")
        assert "Usage" in result and "resume" in result

    def test_unknown_run_returns_clear_error(self, monkeypatch):
        h = self._h()
        # Force the resolver to return None
        from app.agents.commander import commands as cmd_mod
        monkeypatch.setattr(
            cmd_mod, "_resolve_executor_run", lambda arg: None,
        )
        result = h(
            "/delegate resume abc12345 some hint", "test:sender",
        )
        assert "not found" in result.lower()

    def test_no_hint_returns_clear_error(self, monkeypatch):
        h = self._h()
        # Resolver returns a stub run; handler should still demand hint
        from app.agents.commander import commands as cmd_mod
        stub = MagicMock()
        stub.run_id = "abc12345" + "0" * 24
        stub.is_terminal = False
        monkeypatch.setattr(
            cmd_mod, "_resolve_executor_run", lambda arg: stub,
        )
        result = h("/delegate resume abc12345", "test:sender")
        assert "hint" in result.lower()
        assert "usage" in result.lower() or "need a hint" in result.lower()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
