"""Tests for the ``call_or_skip`` helper (Phase D.3 wider wiring,
2026-05-22).

Pins the contract of the convenience wrapper used at 5+ Anthropic
call sites:

  * Returns True when pre_check passes (cap disabled / under).
  * Returns False when AnthropicDailyCapExceeded is raised.
  * Other unexpected exceptions degrade to True (failure-OPEN).
  * The ``source`` parameter is surfaced in the skip log line.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from unittest.mock import MagicMock

import pytest


_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())


# Direct-load the module under test to avoid the runtime_settings →
# pydantic_settings chain that fails on host.
_spec = importlib.util.spec_from_file_location(
    "_ab_d3_cos", "app/llm_anthropic_budget.py",
)
assert _spec is not None and _spec.loader is not None
ab = importlib.util.module_from_spec(_spec)
sys.modules["_ab_d3_cos"] = ab
_spec.loader.exec_module(ab)


class TestCallOrSkipBasics:
    def test_returns_true_when_pre_check_passes(self, monkeypatch):
        monkeypatch.setattr(ab, "pre_check", lambda **kw: None)
        assert ab.call_or_skip(estimated_cost_usd=0.005) is True

    def test_returns_false_when_cap_exceeded(self, monkeypatch):
        def _raise(**kw):
            raise ab.AnthropicDailyCapExceeded(
                today_spent_usd=24.0,
                daily_cap_usd=25.0,
                estimated_cost_usd=5.0,
            )

        monkeypatch.setattr(ab, "pre_check", _raise)
        assert ab.call_or_skip(estimated_cost_usd=5.0) is False

    def test_other_exception_is_failure_open(self, monkeypatch):
        # A bug in the gate itself must NOT block legitimate calls.
        def _raise(**kw):
            raise RuntimeError("gate internals broke")

        monkeypatch.setattr(ab, "pre_check", _raise)
        # Returns True — failure-OPEN
        assert ab.call_or_skip(estimated_cost_usd=0.005) is True

    def test_default_estimate_zero(self, monkeypatch):
        # No estimate supplied → 0.0 → pre_check should still be called
        seen = {}

        def _spy(**kwargs):
            seen.update(kwargs)

        monkeypatch.setattr(ab, "pre_check", _spy)
        ab.call_or_skip()
        assert seen == {"estimated_cost_usd": 0.0}


class TestCallOrSkipLogging:
    def test_source_in_skip_log_line(self, monkeypatch, caplog):
        def _raise(**kw):
            raise ab.AnthropicDailyCapExceeded(
                today_spent_usd=24.0,
                daily_cap_usd=25.0,
                estimated_cost_usd=5.0,
            )

        monkeypatch.setattr(ab, "pre_check", _raise)
        with caplog.at_level(logging.INFO, logger=ab.logger.name):
            ab.call_or_skip(
                estimated_cost_usd=5.0,
                source="brainstorm:idea_mutator",
            )
        msgs = [r.getMessage() for r in caplog.records]
        assert any("brainstorm:idea_mutator" in m for m in msgs)

    def test_no_source_still_logs_skip(self, monkeypatch, caplog):
        def _raise(**kw):
            raise ab.AnthropicDailyCapExceeded(
                today_spent_usd=24.0, daily_cap_usd=25.0,
                estimated_cost_usd=5.0,
            )

        monkeypatch.setattr(ab, "pre_check", _raise)
        with caplog.at_level(logging.INFO, logger=ab.logger.name):
            ab.call_or_skip(estimated_cost_usd=5.0)
        msgs = [r.getMessage() for r in caplog.records]
        assert any("Anthropic call skipped" in m for m in msgs)


class TestExportSurface:
    def test_call_or_skip_in_dunder_all(self):
        assert "call_or_skip" in ab.__all__


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
