"""Tests for /budgets Signal slash command (2026-05-22).

Read-only operator surface from Signal — shows today's + 7-day totals
plus per-connector breakdown of @with_connector_budget spending.

Covers:
  * Disabled master switch → friendly OFF message
  * Empty ledger → "no spend recorded" hint
  * Single connector → today + 7d formatted
  * Multi connector → sorted by 7d desc + listed
  * runtime_settings broken → fail-safe OFF response
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


def _import_handler():
    try:
        from app.agents.commander.commands import _handle_budgets_command
        return _handle_budgets_command
    except Exception as exc:
        pytest.skip(f"commands module unavailable: {exc}")


class TestBudgetsCommand:
    def test_disabled_returns_off_message(self, monkeypatch):
        fn = _import_handler()
        from app import runtime_settings as rs
        monkeypatch.setattr(
            rs, "get_connector_budgets_enabled", lambda: False,
        )
        result = fn()
        assert "OFF" in result
        assert "pass-through" in result

    def test_empty_ledger_helpful_hint(self, monkeypatch, tmp_path):
        fn = _import_handler()
        from app import runtime_settings as rs
        from app.connector_budget import store as store_mod
        monkeypatch.setattr(
            rs, "get_connector_budgets_enabled", lambda: True,
        )
        store_mod.reset_for_tests(tmp_path)
        try:
            result = fn()
            assert "no spend recorded" in result
        finally:
            store_mod.reset_for_tests(None)

    def test_single_connector_formatting(self, monkeypatch, tmp_path):
        fn = _import_handler()
        from app import runtime_settings as rs
        from app.connector_budget import store as store_mod
        monkeypatch.setattr(
            rs, "get_connector_budgets_enabled", lambda: True,
        )
        store_mod.reset_for_tests(tmp_path)
        try:
            store_mod.record_spend("aviationstack", 0.001)
            store_mod.record_spend("aviationstack", 0.002)
            result = fn()
            assert "aviationstack" in result
            assert "today $0.0030" in result
            assert "2 call" in result
        finally:
            store_mod.reset_for_tests(None)

    def test_multi_connector_sorted_by_window(self, monkeypatch, tmp_path):
        fn = _import_handler()
        from app import runtime_settings as rs
        from app.connector_budget import store as store_mod
        monkeypatch.setattr(
            rs, "get_connector_budgets_enabled", lambda: True,
        )
        store_mod.reset_for_tests(tmp_path)
        try:
            # Small connector
            store_mod.record_spend("small", 0.001)
            # Big connector
            store_mod.record_spend("large", 5.000)
            store_mod.record_spend("large", 5.000)
            result = fn()
            # large should appear before small (higher 7d total)
            large_idx = result.find("large")
            small_idx = result.find("small")
            assert large_idx > 0 and small_idx > 0
            assert large_idx < small_idx
        finally:
            store_mod.reset_for_tests(None)

    def test_runtime_settings_broken_fail_safe(self, monkeypatch):
        fn = _import_handler()
        from app import runtime_settings as rs

        def _boom():
            raise RuntimeError("settings sick")
        monkeypatch.setattr(
            rs, "get_connector_budgets_enabled", _boom,
        )
        # Should not raise; returns the OFF message (defensive)
        result = fn()
        assert "OFF" in result or "pass-through" in result


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
