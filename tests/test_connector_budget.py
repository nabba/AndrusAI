"""Tests for app/connector_budget (2026-05-22).

Covers:
  * record_spend + today_spend round trip
  * Cross-connector isolation (spend on A doesn't leak into B)
  * Old-day rows excluded from today_spend
  * Corrupted ledger row → skipped, not crash
  * Master switch OFF → decorator is a pass-through, no pre-check, no record
  * Master switch ON + under cap → wrapped function runs, spend recorded
  * Master switch ON + over cap → raises ConnectorBudgetExceeded
  * Cap-boundary refusal: exactly-at-cap blocks (cap is ceiling)
  * cost_extractor used when provided (estimated=False)
  * cost_extractor failure falls back to estimate (estimated=True)
  * Async function: same gating semantics via await path
  * Decorator factory rejects non-positive daily_cap_usd
  * Decorator factory rejects negative estimated_cost_usd
  * Runtime-settings switch round-trip (skipped on host lacking pydantic_settings)
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
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


from app.connector_budget import (  # noqa: E402
    ConnectorBudgetExceeded,
    today_spend_all_connectors,
    with_connector_budget,
)
from app.connector_budget import store as store_mod  # noqa: E402
from app.connector_budget import decorator as dec_mod  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def isolated_ledger(tmp_path):
    store_mod.reset_for_tests(tmp_path)
    yield tmp_path
    store_mod.reset_for_tests(None)


@pytest.fixture
def switch_on(monkeypatch):
    monkeypatch.setattr(dec_mod, "_master_switch_on", lambda: True)


@pytest.fixture
def switch_off(monkeypatch):
    monkeypatch.setattr(dec_mod, "_master_switch_on", lambda: False)


# ── Store: record + today_spend ───────────────────────────────────────


class TestStore:
    def test_empty_ledger_returns_zero(self, isolated_ledger):
        assert store_mod.today_spend("nothing") == 0.0

    def test_record_then_today_spend(self, isolated_ledger):
        store_mod.record_spend("clearbit", 0.05)
        store_mod.record_spend("clearbit", 0.10)
        assert store_mod.today_spend("clearbit") == pytest.approx(0.15)

    def test_cross_connector_isolation(self, isolated_ledger):
        store_mod.record_spend("a", 0.50)
        store_mod.record_spend("b", 0.25)
        assert store_mod.today_spend("a") == pytest.approx(0.50)
        assert store_mod.today_spend("b") == pytest.approx(0.25)

    def test_old_day_rows_excluded(self, isolated_ledger):
        # Manually write a row dated last week
        old_row = {
            "connector": "clearbit",
            "ts": "2026-05-15T12:00:00+00:00",
            "usd": 99.99,
            "estimated": False,
        }
        ledger = isolated_ledger / "connector_budget" / "spend.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(old_row) + "\n")
        # Today's spend
        store_mod.record_spend("clearbit", 0.10)
        assert store_mod.today_spend("clearbit") == pytest.approx(0.10)

    def test_corrupted_row_skipped(self, isolated_ledger):
        ledger = isolated_ledger / "connector_budget" / "spend.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as fh:
            fh.write("this is not json\n")
            fh.write(json.dumps({"connector": "x", "ts": "bad", "usd": 1.0}) + "\n")
        store_mod.record_spend("x", 0.05)
        # Only the valid today-row counts
        assert store_mod.today_spend("x") == pytest.approx(0.05)


# ── Aggregate helper ──────────────────────────────────────────────────


class TestAggregate:
    def test_empty_returns_empty_dict(self, isolated_ledger):
        assert today_spend_all_connectors() == {}

    def test_multi_connector(self, isolated_ledger):
        store_mod.record_spend("a", 0.10)
        store_mod.record_spend("a", 0.20)
        store_mod.record_spend("b", 0.05)
        agg = today_spend_all_connectors()
        assert set(agg.keys()) == {"a", "b"}
        assert agg["a"]["usd"] == pytest.approx(0.30)
        assert agg["a"]["calls"] == 2
        assert agg["b"]["usd"] == pytest.approx(0.05)
        assert agg["b"]["calls"] == 1

    def test_estimated_calls_counted_separately(self, isolated_ledger):
        store_mod.record_spend("x", 0.05, estimated=True)
        store_mod.record_spend("x", 0.05, estimated=False)
        store_mod.record_spend("x", 0.05, estimated=True)
        agg = today_spend_all_connectors()
        assert agg["x"]["calls"] == 3
        assert agg["x"]["estimated_calls"] == 2

    def test_old_day_rows_excluded_from_aggregate(self, isolated_ledger):
        old = {
            "connector": "a", "ts": "2026-05-15T12:00:00+00:00",
            "usd": 99.99, "estimated": False,
        }
        ledger = isolated_ledger / "connector_budget" / "spend.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(old) + "\n")
        store_mod.record_spend("a", 0.10)
        agg = today_spend_all_connectors()
        assert agg["a"]["usd"] == pytest.approx(0.10)
        assert agg["a"]["calls"] == 1


# ── Decorator: master switch OFF ──────────────────────────────────────


class TestSwitchOff:
    def test_pass_through_sync(self, isolated_ledger, switch_off):
        @with_connector_budget("x", daily_cap_usd=0.01, estimated_cost_usd=99.0)
        def fn():
            return "ok"

        # Even with estimate >> cap, no refusal — switch is off
        assert fn() == "ok"
        # No spend recorded
        assert store_mod.today_spend("x") == 0.0

    def test_pass_through_async(self, isolated_ledger, switch_off):
        @with_connector_budget("y", daily_cap_usd=0.01, estimated_cost_usd=99.0)
        async def afn():
            return "ok-async"

        assert asyncio.run(afn()) == "ok-async"
        assert store_mod.today_spend("y") == 0.0


# ── Decorator: master switch ON ───────────────────────────────────────


class TestSwitchOn:
    def test_under_cap_runs_and_records(self, isolated_ledger, switch_on):
        @with_connector_budget(
            "clearbit", daily_cap_usd=2.0, estimated_cost_usd=0.05,
        )
        def lookup(domain):
            return {"domain": domain}

        out = lookup("acme.com")
        assert out == {"domain": "acme.com"}
        # Estimate recorded (no extractor provided)
        assert store_mod.today_spend("clearbit") == pytest.approx(0.05)

    def test_over_cap_raises(self, isolated_ledger, switch_on):
        @with_connector_budget(
            "clearbit", daily_cap_usd=0.10, estimated_cost_usd=0.05,
        )
        def lookup():
            return "ran"

        # Cap is INCLUSIVE — 2 calls fit (0.05 + 0.05 = 0.10 == cap), then
        # the third (0.10 + 0.05 = 0.15 > cap) is refused.
        assert lookup() == "ran"
        assert lookup() == "ran"
        with pytest.raises(ConnectorBudgetExceeded) as exc_info:
            lookup()
        err = exc_info.value
        assert err.connector == "clearbit"
        assert err.daily_cap_usd == 0.10
        assert err.estimated_cost_usd == 0.05
        # Spend NOT incremented by a refused call
        assert store_mod.today_spend("clearbit") == pytest.approx(0.10)

    def test_under_cap_inclusive(self, isolated_ledger, switch_on):
        # Spent 0.04 already; cap 0.10; next estimate 0.05 → 0.09 ≤ 0.10 OK
        store_mod.record_spend("clearbit", 0.04)

        @with_connector_budget(
            "clearbit", daily_cap_usd=0.10, estimated_cost_usd=0.05,
        )
        def lookup():
            return "ok"

        assert lookup() == "ok"

    def test_cost_extractor_used(self, isolated_ledger, switch_on):
        @with_connector_budget(
            "anthropic_demo",
            daily_cap_usd=10.0,
            estimated_cost_usd=0.50,
            cost_extractor=lambda r: r["cost_usd"],
        )
        def call_llm():
            return {"text": "hello", "cost_usd": 0.0123}

        call_llm()
        # Actual cost recorded, not the conservative estimate
        assert store_mod.today_spend("anthropic_demo") == pytest.approx(
            0.0123,
        )

    def test_extractor_failure_falls_back_to_estimate(
        self, isolated_ledger, switch_on,
    ):
        def boom(r):
            raise RuntimeError("no cost field")

        @with_connector_budget(
            "weird",
            daily_cap_usd=1.0,
            estimated_cost_usd=0.05,
            cost_extractor=boom,
        )
        def call():
            return {"no_cost_here": True}

        call()
        # Estimate was used as fallback
        assert store_mod.today_spend("weird") == pytest.approx(0.05)

    def test_async_under_cap(self, isolated_ledger, switch_on):
        @with_connector_budget(
            "async_demo", daily_cap_usd=1.0, estimated_cost_usd=0.01,
        )
        async def afetch():
            return 42

        assert asyncio.run(afetch()) == 42
        assert store_mod.today_spend("async_demo") == pytest.approx(0.01)

    def test_async_over_cap_raises(self, isolated_ledger, switch_on):
        # 0.99 already spent; cap 1.0; estimate 0.05 → 1.04 > 1.0 → refused
        store_mod.record_spend("async_demo2", 0.99)

        @with_connector_budget(
            "async_demo2", daily_cap_usd=1.0, estimated_cost_usd=0.05,
        )
        async def afetch():
            return "should not reach"

        with pytest.raises(ConnectorBudgetExceeded):
            asyncio.run(afetch())


# ── Decorator factory validation ──────────────────────────────────────


class TestFactoryValidation:
    def test_zero_cap_rejected(self):
        with pytest.raises(ValueError, match="daily_cap_usd"):
            with_connector_budget("x", daily_cap_usd=0.0)

    def test_negative_cap_rejected(self):
        with pytest.raises(ValueError, match="daily_cap_usd"):
            with_connector_budget("x", daily_cap_usd=-1.0)

    def test_negative_estimate_rejected(self):
        with pytest.raises(ValueError, match="estimated_cost_usd"):
            with_connector_budget(
                "x", daily_cap_usd=1.0, estimated_cost_usd=-0.01,
            )


# ── Master-switch round-trip (skipped on host without pydantic_settings) ──


class TestMasterSwitch:
    def _import_rs(self):
        try:
            import app.runtime_settings as rs
            return rs
        except Exception as exc:
            pytest.skip(f"app.runtime_settings unavailable: {exc}")

    def test_default_is_off(self, monkeypatch, tmp_path):
        rs = self._import_rs()
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(rs, "_cache", None)
        monkeypatch.setattr(rs, "_STATE_PATH", tmp_path / "runtime_settings.json")
        assert rs.get_connector_budgets_enabled() is False

    def test_setter_flips(self, monkeypatch, tmp_path):
        rs = self._import_rs()
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(rs, "_cache", None)
        monkeypatch.setattr(rs, "_STATE_PATH", tmp_path / "runtime_settings.json")
        rs.set_connector_budgets_enabled(True)
        assert rs.get_connector_budgets_enabled() is True
        rs.set_connector_budgets_enabled(False)
        assert rs.get_connector_budgets_enabled() is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
