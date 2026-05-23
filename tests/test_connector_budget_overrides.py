"""Tests for connector_budget operator-configurable overrides (2026-05-22).

The decorator now consults ``runtime_settings.get_connector_budget_overrides()``
at call time so operators can tune caps live without redeploying.

Covers:
  * No override → decorator defaults applied
  * Override with daily_cap_usd only → cap overridden, estimate falls
    back to decorator default
  * Override with estimated_cost_usd only → estimate overridden, cap
    falls back to default
  * Full override → both values used
  * Bogus override (non-numeric, zero cap, negative estimate) → falls
    back to defaults
  * No runtime_settings module → falls back to defaults
  * Override raises higher cap than default → previously-blocked
    calls now succeed
  * Override lowers cap → previously-allowed calls now refused
  * Per-call lookup: override change between calls applies live
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


from app.connector_budget import (  # noqa: E402
    ConnectorBudgetExceeded,
    today_spend,
    with_connector_budget,
)
from app.connector_budget import decorator as dec_mod  # noqa: E402
from app.connector_budget import store as store_mod  # noqa: E402


@pytest.fixture
def isolated_ledger(tmp_path):
    store_mod.reset_for_tests(tmp_path)
    yield tmp_path
    store_mod.reset_for_tests(None)


@pytest.fixture
def switch_on(monkeypatch):
    monkeypatch.setattr(dec_mod, "_master_switch_on", lambda: True)


def _stub_overrides(monkeypatch, overrides):
    """Replace _resolve_overrides with a deterministic stub for testing."""
    monkeypatch.setattr(
        dec_mod,
        "_resolve_overrides",
        lambda connector: overrides.get(connector, {}),
    )


# ── No override: decorator defaults ──────────────────────────────────


class TestNoOverride:
    def test_defaults_used_when_no_override(
        self, isolated_ledger, switch_on, monkeypatch,
    ):
        _stub_overrides(monkeypatch, {})

        @with_connector_budget(
            "x", daily_cap_usd=0.10, estimated_cost_usd=0.05,
        )
        def fn():
            return "ok"

        assert fn() == "ok"
        assert fn() == "ok"
        # Third call would push to 0.15 > 0.10 cap
        with pytest.raises(ConnectorBudgetExceeded) as exc:
            fn()
        assert exc.value.daily_cap_usd == 0.10
        assert exc.value.estimated_cost_usd == 0.05


# ── Override applied ────────────────────────────────────────────────


class TestOverrideApplied:
    def test_full_override(
        self, isolated_ledger, switch_on, monkeypatch,
    ):
        _stub_overrides(monkeypatch, {
            "x": {"daily_cap_usd": 1.0, "estimated_cost_usd": 0.50},
        })

        @with_connector_budget(
            "x", daily_cap_usd=0.10, estimated_cost_usd=0.05,
        )
        def fn():
            return "ok"

        # Default cap (0.10) would refuse after 2 calls; override (1.0)
        # with bigger estimate (0.50) refuses after 2 also but for
        # different reasons. Confirm OVERRIDE values were used.
        assert fn() == "ok"
        assert fn() == "ok"
        with pytest.raises(ConnectorBudgetExceeded) as exc:
            fn()
        # Exception carries the overridden cap + estimate
        assert exc.value.daily_cap_usd == 1.0
        assert exc.value.estimated_cost_usd == 0.50

    def test_partial_override_cap_only(
        self, isolated_ledger, switch_on, monkeypatch,
    ):
        # Operator raises just the cap, leaves estimate at default
        _stub_overrides(monkeypatch, {
            "x": {"daily_cap_usd": 1.0},
        })

        @with_connector_budget(
            "x", daily_cap_usd=0.05, estimated_cost_usd=0.01,
        )
        def fn():
            return "ok"

        # With default cap 0.05, only 5 calls fit. With override cap
        # 1.0, ~100 calls fit. Run 10.
        for _ in range(10):
            assert fn() == "ok"
        assert today_spend("x") == pytest.approx(0.10)

    def test_partial_override_estimate_only(
        self, isolated_ledger, switch_on, monkeypatch,
    ):
        _stub_overrides(monkeypatch, {
            "x": {"estimated_cost_usd": 0.005},
        })

        @with_connector_budget(
            "x", daily_cap_usd=0.10, estimated_cost_usd=0.05,
        )
        def fn():
            return "ok"

        # Phase B.3 (2026-05-22) — Decimal arithmetic makes the
        # cap boundary EXACT. With est=0.005 and cap=0.10, exactly
        # 20 calls fit (cumulative = 0.10 == cap, the cap-as-ceiling
        # inclusive rule allows the boundary). Previously this test
        # had to use 15 calls because float arithmetic made
        # 19 * 0.005 != 0.095 — now we can pin the exact boundary.
        for _ in range(20):
            assert fn() == "ok"
        # Cumulative spend is exactly the cap
        assert today_spend("x") == pytest.approx(0.10, abs=1e-9)
        # 21st call would push to 0.105 > 0.10 → refused
        with pytest.raises(ConnectorBudgetExceeded):
            fn()


# ── Override raises higher cap → unblocks blocked calls ──────────────


class TestRuntimeReconfiguration:
    def test_higher_cap_unblocks(
        self, isolated_ledger, switch_on, monkeypatch,
    ):
        # Pre-spend up to the default cap
        store_mod.record_spend("x", 0.10)
        # Operator notices the cap is hit, raises it via override
        _stub_overrides(monkeypatch, {
            "x": {"daily_cap_usd": 1.0},
        })

        @with_connector_budget(
            "x", daily_cap_usd=0.10, estimated_cost_usd=0.05,
        )
        def fn():
            return "ok"

        # Now the call succeeds because effective cap is 1.0
        assert fn() == "ok"

    def test_lower_cap_blocks(
        self, isolated_ledger, switch_on, monkeypatch,
    ):
        # Override LOWERS the cap below default
        _stub_overrides(monkeypatch, {
            "x": {"daily_cap_usd": 0.04},
        })

        @with_connector_budget(
            "x", daily_cap_usd=1.0, estimated_cost_usd=0.05,
        )
        def fn():
            return "ok"

        # Single call would push to 0.05 > 0.04 effective cap → refused
        with pytest.raises(ConnectorBudgetExceeded) as exc:
            fn()
        assert exc.value.daily_cap_usd == 0.04


# ── Bogus override falls back to defaults ────────────────────────────


class TestBogusOverride:
    def test_zero_cap_falls_back(
        self, isolated_ledger, switch_on, monkeypatch,
    ):
        _stub_overrides(monkeypatch, {
            "x": {"daily_cap_usd": 0.0},  # invalid
        })

        @with_connector_budget(
            "x", daily_cap_usd=0.10, estimated_cost_usd=0.05,
        )
        def fn():
            return "ok"

        # Override rejected at decorator level → defaults applied →
        # call succeeds (cap = 0.10).
        assert fn() == "ok"

    def test_negative_estimate_falls_back(
        self, isolated_ledger, switch_on, monkeypatch,
    ):
        _stub_overrides(monkeypatch, {
            "x": {"estimated_cost_usd": -1.0},
        })

        @with_connector_budget(
            "x", daily_cap_usd=0.10, estimated_cost_usd=0.05,
        )
        def fn():
            return "ok"

        # Falls back to default 0.05 estimate → first call OK
        assert fn() == "ok"
        # Spend = 0.05 (default, not the bogus -1.0)
        assert today_spend("x") == pytest.approx(0.05)


# ── Resolver failure-isolated ────────────────────────────────────────


class TestResolverIsolation:
    def test_runtime_settings_unavailable_falls_back(
        self, isolated_ledger, switch_on, monkeypatch,
    ):
        # Make _resolve_overrides raise — decorator should still work
        def _boom(connector):
            raise RuntimeError("rs sick")
        monkeypatch.setattr(dec_mod, "_resolve_overrides", _boom)

        @with_connector_budget(
            "x", daily_cap_usd=0.10, estimated_cost_usd=0.05,
        )
        def fn():
            return "ok"

        # The wrap shouldn't crash; falls back to defaults
        try:
            result = fn()
            assert result == "ok"
        except RuntimeError:
            pytest.fail(
                "resolver exception should be isolated, not propagated"
            )


# ── Live tuning: change override between calls ───────────────────────


class TestLiveTuning:
    def test_override_change_applies_per_call(
        self, isolated_ledger, switch_on, monkeypatch,
    ):
        # Start with default (cap 0.05, est 0.05): one call allowed.
        # Then operator widens to 1.0 → subsequent calls allowed too.
        overrides_state = {}
        monkeypatch.setattr(
            dec_mod, "_resolve_overrides",
            lambda c: overrides_state.get(c, {}),
        )

        @with_connector_budget(
            "x", daily_cap_usd=0.05, estimated_cost_usd=0.05,
        )
        def fn():
            return "ok"

        # First call lands at exactly cap (0.05 == 0.05 OK; next would
        # push to 0.10 > 0.05 → refused).
        assert fn() == "ok"
        with pytest.raises(ConnectorBudgetExceeded):
            fn()

        # Operator widens the cap mid-flight
        overrides_state["x"] = {"daily_cap_usd": 1.0}

        # Next call: spent 0.05, est 0.05, eff cap 1.0 → 0.10 < 1.0 OK
        assert fn() == "ok"


# ── runtime_settings setter/getter round-trip ────────────────────────


class TestRuntimeSettingsAPI:
    def _import_rs(self):
        try:
            import app.runtime_settings as rs
            return rs
        except Exception as exc:
            pytest.skip(f"app.runtime_settings unavailable: {exc}")

    def test_default_empty(self, monkeypatch, tmp_path):
        rs = self._import_rs()
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(rs, "_cache", None)
        monkeypatch.setattr(rs, "_STATE_PATH", tmp_path / "runtime_settings.json")
        assert rs.get_connector_budget_overrides() == {}

    def test_setter_merges_fields(self, monkeypatch, tmp_path):
        rs = self._import_rs()
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(rs, "_cache", None)
        monkeypatch.setattr(rs, "_STATE_PATH", tmp_path / "runtime_settings.json")
        rs.set_connector_budget_override(
            "aviationstack", daily_cap_usd=0.005,
        )
        out = rs.get_connector_budget_overrides()
        assert out == {"aviationstack": {"daily_cap_usd": 0.005}}
        # Merge an estimate without touching the cap
        rs.set_connector_budget_override(
            "aviationstack", estimated_cost_usd=0.001,
        )
        out = rs.get_connector_budget_overrides()
        assert out == {
            "aviationstack": {
                "daily_cap_usd": 0.005,
                "estimated_cost_usd": 0.001,
            },
        }

    def test_setter_rejects_invalid_values(self, monkeypatch, tmp_path):
        rs = self._import_rs()
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(rs, "_cache", None)
        monkeypatch.setattr(rs, "_STATE_PATH", tmp_path / "runtime_settings.json")
        with pytest.raises(ValueError, match="connector must be"):
            rs.set_connector_budget_override("", daily_cap_usd=1.0)
        with pytest.raises(ValueError, match="daily_cap_usd"):
            rs.set_connector_budget_override("x", daily_cap_usd=0.0)
        with pytest.raises(ValueError, match="estimated_cost_usd"):
            rs.set_connector_budget_override(
                "x", estimated_cost_usd=-0.01,
            )

    def test_remover(self, monkeypatch, tmp_path):
        rs = self._import_rs()
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(rs, "_cache", None)
        monkeypatch.setattr(rs, "_STATE_PATH", tmp_path / "runtime_settings.json")
        rs.set_connector_budget_override("a", daily_cap_usd=1.0)
        rs.set_connector_budget_override("b", daily_cap_usd=2.0)
        assert rs.remove_connector_budget_override("a") is True
        assert rs.remove_connector_budget_override("a") is False  # idempotent
        assert "a" not in rs.get_connector_budget_overrides()
        assert "b" in rs.get_connector_budget_overrides()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
