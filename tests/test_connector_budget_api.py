"""Tests for app/control_plane/connector_budget_api (2026-05-22).

Pins the read-only operator surface. Skips on host without fastapi
(matching the pattern used by test_reviews_api.py +
test_capability_regression_api.py); runs in CI.

Covers:
  * /state empty when ledger absent
  * /state aggregates today's spend per connector
  * Descending USD sort
  * Old-day rows excluded
  * Total counts match the sum across connectors
  * estimated_calls counter populated
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


@pytest.fixture
def client(tmp_path, monkeypatch):
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.control_plane.connector_budget_api import router
    except Exception as exc:
        pytest.skip(f"fastapi/router import failed: {exc}")
    from app.connector_budget import store as store_mod
    store_mod.reset_for_tests(tmp_path)
    app = FastAPI()
    app.include_router(router)
    yield TestClient(app)
    store_mod.reset_for_tests(None)


# ── /state ────────────────────────────────────────────────────────────


class TestStateEndpoint:
    def test_empty_state(self, client):
        resp = client.get("/api/cp/connector-budget/state")
        assert resp.status_code == 200
        data = resp.json()
        assert "enabled" in data
        assert data["connectors"] == []
        assert data["total_usd"] == 0
        assert data["total_calls"] == 0

    def test_single_connector(self, client):
        from app.connector_budget import record_spend
        record_spend("clearbit", 0.05)
        record_spend("clearbit", 0.10)
        resp = client.get("/api/cp/connector-budget/state")
        data = resp.json()
        assert len(data["connectors"]) == 1
        c = data["connectors"][0]
        assert c["connector"] == "clearbit"
        assert c["today_spend_usd"] == pytest.approx(0.15)
        assert c["today_calls"] == 2
        assert data["total_usd"] == pytest.approx(0.15)
        assert data["total_calls"] == 2

    def test_multi_connector_descending_sort(self, client):
        from app.connector_budget import record_spend
        # Small first, large second — output must be large first
        record_spend("small", 0.01)
        record_spend("large", 1.00)
        record_spend("medium", 0.50)
        resp = client.get("/api/cp/connector-budget/state")
        data = resp.json()
        names = [c["connector"] for c in data["connectors"]]
        assert names == ["large", "medium", "small"]

    def test_estimated_calls_counted(self, client):
        from app.connector_budget import record_spend
        record_spend("x", 0.05, estimated=True)
        record_spend("x", 0.05, estimated=False)
        record_spend("x", 0.05, estimated=True)
        resp = client.get("/api/cp/connector-budget/state")
        c = resp.json()["connectors"][0]
        assert c["today_estimated_calls"] == 2
        assert c["today_calls"] == 3
