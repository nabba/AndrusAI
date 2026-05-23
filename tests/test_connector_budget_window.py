"""Tests for window_spend_by_connector helper (2026-05-22).

The aggregator powers the last-7-days view on the ConnectorBudgetCard.

Covers:
  * Empty ledger → empty dict
  * Today-only rows aggregate as expected
  * Mixed-day rows aggregate within window
  * Rows older than `days` excluded
  * `days` parameter honored (1, 7, 30)
  * `days < 1` clamped to 1
  * Multiple connectors aggregated independently
  * Estimated calls counted separately
  * Corrupted ledger row skipped silently
"""
from __future__ import annotations

import datetime as _dt
import json
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


from app.connector_budget import (  # noqa: E402
    record_spend,
    window_spend_by_connector,
)
from app.connector_budget import store as store_mod  # noqa: E402


@pytest.fixture
def isolated_ledger(tmp_path):
    store_mod.reset_for_tests(tmp_path)
    yield tmp_path
    store_mod.reset_for_tests(None)


def _write_row(workspace_path, *, connector, day_offset, usd, estimated=False):
    """Write a row directly into the ledger with a backdated ts.
    `day_offset=0` is today, -1 is yesterday, etc."""
    target_dt = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(
        days=day_offset,
    )
    ledger = workspace_path / "connector_budget" / "spend.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "connector": connector,
            "ts": target_dt.isoformat(timespec="seconds"),
            "usd": usd,
            "estimated": estimated,
        }) + "\n")


# ── Empty / boundary cases ───────────────────────────────────────────


class TestEmpty:
    def test_no_ledger_returns_empty(self, isolated_ledger):
        assert window_spend_by_connector(days=7) == {}

    def test_zero_days_clamped_to_one(self, isolated_ledger):
        record_spend("x", 0.10)
        result = window_spend_by_connector(days=0)
        # Should be clamped to days=1, returning only today's rows
        assert "x" in result

    def test_negative_days_clamped_to_one(self, isolated_ledger):
        record_spend("x", 0.10)
        result = window_spend_by_connector(days=-5)
        assert "x" in result


# ── Today-only ───────────────────────────────────────────────────────


class TestTodayOnly:
    def test_single_today_row(self, isolated_ledger):
        record_spend("x", 0.10)
        result = window_spend_by_connector(days=7)
        assert result == {
            "x": {"usd": 0.10, "calls": 1, "estimated_calls": 0},
        }

    def test_multiple_today_rows_sum(self, isolated_ledger):
        record_spend("x", 0.10)
        record_spend("x", 0.20)
        record_spend("x", 0.05, estimated=True)
        result = window_spend_by_connector(days=7)
        assert result["x"]["usd"] == pytest.approx(0.35)
        assert result["x"]["calls"] == 3
        assert result["x"]["estimated_calls"] == 1


# ── Multi-day window ────────────────────────────────────────────────


class TestMultiDay:
    def test_aggregates_across_days_in_window(self, isolated_ledger):
        _write_row(isolated_ledger, connector="x", day_offset=0, usd=0.10)
        _write_row(isolated_ledger, connector="x", day_offset=-1, usd=0.05)
        _write_row(isolated_ledger, connector="x", day_offset=-3, usd=0.07)
        result = window_spend_by_connector(days=7)
        assert result["x"]["usd"] == pytest.approx(0.22)
        assert result["x"]["calls"] == 3

    def test_rows_beyond_window_excluded(self, isolated_ledger):
        _write_row(isolated_ledger, connector="x", day_offset=0, usd=0.10)
        # 10 days ago is outside a 7-day window
        _write_row(isolated_ledger, connector="x", day_offset=-10, usd=99.0)
        result = window_spend_by_connector(days=7)
        assert result["x"]["usd"] == pytest.approx(0.10)
        assert result["x"]["calls"] == 1

    def test_window_boundary_exact_inclusion(self, isolated_ledger):
        # days=3 means today + 2 prior days
        _write_row(isolated_ledger, connector="x", day_offset=0, usd=0.10)
        _write_row(isolated_ledger, connector="x", day_offset=-2, usd=0.05)
        _write_row(isolated_ledger, connector="x", day_offset=-3, usd=99.0)
        result = window_spend_by_connector(days=3)
        # day=-3 is OUT, day=-2 is IN
        assert result["x"]["usd"] == pytest.approx(0.15)


# ── Multi-connector independence ─────────────────────────────────────


class TestMultiConnector:
    def test_independent_aggregates(self, isolated_ledger):
        _write_row(isolated_ledger, connector="a", day_offset=0, usd=0.50)
        _write_row(isolated_ledger, connector="b", day_offset=-1, usd=1.00)
        _write_row(isolated_ledger, connector="b", day_offset=0, usd=0.25)
        result = window_spend_by_connector(days=7)
        assert result == {
            "a": {"usd": 0.50, "calls": 1, "estimated_calls": 0},
            "b": {"usd": 1.25, "calls": 2, "estimated_calls": 0},
        }

    def test_estimated_calls_counted_separately(self, isolated_ledger):
        _write_row(
            isolated_ledger, connector="x", day_offset=-1,
            usd=0.05, estimated=True,
        )
        _write_row(
            isolated_ledger, connector="x", day_offset=0,
            usd=0.05, estimated=False,
        )
        result = window_spend_by_connector(days=7)
        assert result["x"]["calls"] == 2
        assert result["x"]["estimated_calls"] == 1


# ── Robustness ───────────────────────────────────────────────────────


class TestRobustness:
    def test_corrupted_row_skipped(self, isolated_ledger):
        ledger = isolated_ledger / "connector_budget" / "spend.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as fh:
            fh.write("not json\n")
            fh.write(
                json.dumps({
                    "connector": "x",
                    "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                    "usd": 0.05,
                }) + "\n",
            )
            fh.write("{also bad\n")
        result = window_spend_by_connector(days=7)
        assert result["x"]["calls"] == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
