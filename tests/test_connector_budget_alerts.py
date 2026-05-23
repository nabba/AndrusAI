"""Tests for connector_budget Signal-alert path (2026-05-22).

When ConnectorBudgetExceeded fires inside the @with_connector_budget
wrapper, the decorator emits a Signal alert via app.notify.notify.
The alert is deduped per-(connector, day) so repeated refusals in
the same loop iteration don't spam.

Covers:
  * First hit fires alert (notify called once)
  * Second hit SAME day does NOT fire (dedup honored)
  * notify() failure is silent (alert path failure-isolated)
  * should_alert_budget_exceeded round-trip
  * Dedup state file path resolution (workspace-aware)
  * Alert state writes the connector + today date
  * Per-connector independence: budget hit on A doesn't dedup B
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
    should_alert_budget_exceeded,
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


@pytest.fixture
def fake_notify(monkeypatch):
    """Capture notify() invocations. We patch the lazy-imported notify
    name by monkeypatching sys.modules['app.notify'].notify."""
    calls = []

    def _capture(title, body, **kwargs):
        calls.append({"title": title, "body": body, **kwargs})

    fake_mod = MagicMock()
    fake_mod.notify = _capture
    monkeypatch.setitem(sys.modules, "app.notify", fake_mod)
    return calls


# ── should_alert_budget_exceeded ─────────────────────────────────────


class TestDedupHelper:
    def test_first_call_returns_true(self, isolated_ledger):
        assert should_alert_budget_exceeded("aviationstack") is True

    def test_second_call_same_day_returns_false(self, isolated_ledger):
        assert should_alert_budget_exceeded("aviationstack") is True
        assert should_alert_budget_exceeded("aviationstack") is False
        assert should_alert_budget_exceeded("aviationstack") is False

    def test_per_connector_independent(self, isolated_ledger):
        assert should_alert_budget_exceeded("a") is True
        assert should_alert_budget_exceeded("b") is True
        # Both deduped now
        assert should_alert_budget_exceeded("a") is False
        assert should_alert_budget_exceeded("b") is False

    def test_dedup_state_file_created(self, isolated_ledger):
        should_alert_budget_exceeded("x")
        path = isolated_ledger / "connector_budget" / "alerts.json"
        assert path.exists()
        import json
        state = json.loads(path.read_text(encoding="utf-8"))
        assert "x" in state


# ── Decorator: alert fires on budget exceeded ────────────────────────


class TestAlertOnBudgetExceeded:
    def test_first_hit_fires_alert(
        self, isolated_ledger, switch_on, fake_notify, monkeypatch,
    ):
        # No overrides → defaults
        monkeypatch.setattr(dec_mod, "_resolve_overrides", lambda c: {})

        @with_connector_budget(
            "x", daily_cap_usd=0.01, estimated_cost_usd=0.05,
        )
        def fn():
            return "ran"

        # First call refused (0 + 0.05 > 0.01)
        with pytest.raises(ConnectorBudgetExceeded):
            fn()

        # Alert fired
        assert len(fake_notify) == 1
        call = fake_notify[0]
        assert "x" in call["title"]
        assert "$0.0100" in call["body"]  # the cap formatted
        assert call.get("url") == "/cp/settings"
        assert call.get("topic") == "connector_budget_exceeded"
        assert call.get("tag") == "connector-budget-x"

    def test_second_hit_same_day_no_alert(
        self, isolated_ledger, switch_on, fake_notify, monkeypatch,
    ):
        monkeypatch.setattr(dec_mod, "_resolve_overrides", lambda c: {})

        @with_connector_budget(
            "x", daily_cap_usd=0.01, estimated_cost_usd=0.05,
        )
        def fn():
            return "ran"

        # First hit
        with pytest.raises(ConnectorBudgetExceeded):
            fn()
        # Second hit same day
        with pytest.raises(ConnectorBudgetExceeded):
            fn()
        # Third hit same day
        with pytest.raises(ConnectorBudgetExceeded):
            fn()

        # Only ONE alert fired (dedup honored)
        assert len(fake_notify) == 1

    def test_per_connector_alerts_independent(
        self, isolated_ledger, switch_on, fake_notify, monkeypatch,
    ):
        monkeypatch.setattr(dec_mod, "_resolve_overrides", lambda c: {})

        @with_connector_budget(
            "a", daily_cap_usd=0.01, estimated_cost_usd=0.05,
        )
        def fn_a():
            return "ran"

        @with_connector_budget(
            "b", daily_cap_usd=0.01, estimated_cost_usd=0.05,
        )
        def fn_b():
            return "ran"

        with pytest.raises(ConnectorBudgetExceeded):
            fn_a()
        with pytest.raises(ConnectorBudgetExceeded):
            fn_b()

        # Each fires once
        assert len(fake_notify) == 2
        names = {c["title"] for c in fake_notify}
        assert "Connector budget hit: a" in names
        assert "Connector budget hit: b" in names


# ── Alert path failure-isolated ──────────────────────────────────────


class TestAlertFailureIsolated:
    def test_notify_failure_does_not_swallow_exception(
        self, isolated_ledger, switch_on, monkeypatch,
    ):
        """Even when notify() fails, the ConnectorBudgetExceeded still
        propagates to the caller — alert is best-effort."""
        monkeypatch.setattr(dec_mod, "_resolve_overrides", lambda c: {})

        def _broken_notify(title, body, **kwargs):
            raise RuntimeError("signal-cli is offline")

        fake_mod = MagicMock()
        fake_mod.notify = _broken_notify
        monkeypatch.setitem(sys.modules, "app.notify", fake_mod)

        @with_connector_budget(
            "x", daily_cap_usd=0.01, estimated_cost_usd=0.05,
        )
        def fn():
            return "ran"

        # The exception is still raised even when alert path is broken
        with pytest.raises(ConnectorBudgetExceeded):
            fn()

    def test_notify_module_unavailable_silent(
        self, isolated_ledger, switch_on, monkeypatch,
    ):
        """If app.notify can't be imported, the alert silently no-ops
        and the budget exception still propagates."""
        monkeypatch.setattr(dec_mod, "_resolve_overrides", lambda c: {})
        # Force-remove app.notify so the lazy import inside
        # _maybe_alert_budget_exceeded raises ImportError
        if "app.notify" in sys.modules:
            del sys.modules["app.notify"]
        monkeypatch.setattr(
            "builtins.__import__",
            _selective_import_blocker("app.notify"),
        )

        @with_connector_budget(
            "x", daily_cap_usd=0.01, estimated_cost_usd=0.05,
        )
        def fn():
            return "ran"

        with pytest.raises(ConnectorBudgetExceeded):
            fn()


def _selective_import_blocker(blocked_name: str):
    """Build a __import__ replacement that raises only for the named
    module — everything else falls through to the real importer."""
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) \
        else __builtins__.__import__

    def _import(name, *args, **kwargs):
        if name == blocked_name:
            raise ImportError(f"blocked: {name}")
        return real_import(name, *args, **kwargs)

    return _import


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
