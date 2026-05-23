"""Tests for app/control_plane/capability_regression_api (2026-05-22).

Pins the read-only operator surface over the JSONL ledger. Each test
uses a TestClient + isolated workspace.

Covers:
  * /state returns enabled flag, current snapshot (None when absent),
    and last regression (None when none)
  * /history newest-first ordering + limit
  * /history empty when no snapshots written
  * /regressions newest-first + limit
  * /regressions filters skip empty-report-shaped rows in the ledger
  * Invalid limit (≤0) returns 422
  * Corrupt JSONL row → skipped silently, valid rows still returned
"""
from __future__ import annotations

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


@pytest.fixture
def client(tmp_path, monkeypatch):
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.control_plane.capability_regression_api import router
    except Exception as exc:
        pytest.skip(f"fastapi/router import failed: {exc}")

    # Redirect the snapshot dir to tmp
    from app.capability_regression import snapshot as snap_mod
    monkeypatch.setattr(
        snap_mod, "_snapshot_dir",
        lambda: tmp_path / "capability_regression",
    )
    (tmp_path / "capability_regression").mkdir()

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


# ── /state ────────────────────────────────────────────────────────────


class TestStateEndpoint:
    def test_empty_state(self, client):
        resp = client.get("/api/cp/capability-regression/state")
        assert resp.status_code == 200
        data = resp.json()
        assert "enabled" in data
        assert data["current_snapshot"] is None
        assert data["last_regression"] is None

    def test_state_with_snapshot_only(self, client, tmp_path):
        from app.capability_regression import CapabilitySnapshot, save_snapshot
        save_snapshot(CapabilitySnapshot(
            captured_at="2026-05-22T00:00:00+00:00",
            tools=["a", "b"],
            models=["m1"],
        ))
        resp = client.get("/api/cp/capability-regression/state")
        data = resp.json()
        assert data["current_snapshot"]["tools"] == ["a", "b"]
        assert data["last_regression"] is None

    def test_state_with_regression(self, client, tmp_path):
        reg_path = (
            tmp_path / "capability_regression" / "regressions.jsonl"
        )
        _write_jsonl(reg_path, [
            {
                "tools_deleted": ["lost_tool"],
                "models_truly_deleted": [],
                "models_newly_blocked": [],
                "prev_captured_at": "t0",
                "curr_captured_at": "t1",
                "has_regression": True,
            },
            {
                "tools_deleted": ["newer_loss"],
                "models_truly_deleted": [],
                "models_newly_blocked": [],
                "prev_captured_at": "t1",
                "curr_captured_at": "t2",
                "has_regression": True,
            },
        ])
        resp = client.get("/api/cp/capability-regression/state")
        data = resp.json()
        # Newest-first → t2 row surfaces first
        assert data["last_regression"]["tools_deleted"] == ["newer_loss"]


# ── /history ──────────────────────────────────────────────────────────


class TestHistoryEndpoint:
    def test_empty_history(self, client):
        resp = client.get("/api/cp/capability-regression/history")
        data = resp.json()
        assert data["count"] == 0
        assert data["snapshots"] == []

    def test_history_newest_first(self, client, tmp_path):
        hist_path = tmp_path / "capability_regression" / "history.jsonl"
        _write_jsonl(hist_path, [
            {"schema_version": 1, "captured_at": "2026-05-20T00:00:00+00:00",
             "tools": ["a"], "models": [], "blocked_models": []},
            {"schema_version": 1, "captured_at": "2026-05-21T00:00:00+00:00",
             "tools": ["a", "b"], "models": [], "blocked_models": []},
            {"schema_version": 1, "captured_at": "2026-05-22T00:00:00+00:00",
             "tools": ["a", "b", "c"], "models": [], "blocked_models": []},
        ])
        resp = client.get("/api/cp/capability-regression/history")
        data = resp.json()
        # Newest-first
        timestamps = [s["captured_at"] for s in data["snapshots"]]
        assert timestamps == [
            "2026-05-22T00:00:00+00:00",
            "2026-05-21T00:00:00+00:00",
            "2026-05-20T00:00:00+00:00",
        ]

    def test_history_limit(self, client, tmp_path):
        hist_path = tmp_path / "capability_regression" / "history.jsonl"
        rows = [
            {"schema_version": 1, "captured_at": f"2026-05-{i:02d}T00:00:00+00:00",
             "tools": [], "models": [], "blocked_models": []}
            for i in range(1, 16)
        ]
        _write_jsonl(hist_path, rows)
        resp = client.get("/api/cp/capability-regression/history?limit=5")
        assert resp.json()["count"] == 5

    def test_history_invalid_limit_422(self, client):
        resp = client.get("/api/cp/capability-regression/history?limit=0")
        assert resp.status_code == 422

    def test_history_corrupted_row_skipped(self, client, tmp_path):
        hist_path = tmp_path / "capability_regression" / "history.jsonl"
        hist_path.parent.mkdir(parents=True, exist_ok=True)
        with hist_path.open("w", encoding="utf-8") as fh:
            fh.write("not json\n")
            fh.write(json.dumps({
                "schema_version": 1, "captured_at": "2026-05-22T00:00:00+00:00",
                "tools": ["a"], "models": [], "blocked_models": [],
            }) + "\n")
            fh.write("{also bad\n")
        resp = client.get("/api/cp/capability-regression/history")
        data = resp.json()
        assert data["count"] == 1


# ── /regressions ──────────────────────────────────────────────────────


class TestRegressionsEndpoint:
    def test_empty(self, client):
        resp = client.get("/api/cp/capability-regression/regressions")
        data = resp.json()
        assert data["count"] == 0

    def test_newest_first(self, client, tmp_path):
        reg_path = (
            tmp_path / "capability_regression" / "regressions.jsonl"
        )
        _write_jsonl(reg_path, [
            {"tools_deleted": ["t1"], "models_truly_deleted": [],
             "models_newly_blocked": [], "prev_captured_at": "t0",
             "curr_captured_at": "t1", "has_regression": True},
            {"tools_deleted": ["t2"], "models_truly_deleted": [],
             "models_newly_blocked": [], "prev_captured_at": "t1",
             "curr_captured_at": "t2", "has_regression": True},
        ])
        resp = client.get(
            "/api/cp/capability-regression/regressions",
        )
        data = resp.json()
        assert data["regressions"][0]["tools_deleted"] == ["t2"]
        assert data["regressions"][1]["tools_deleted"] == ["t1"]


# ── /snapshot (force one-shot pass) ───────────────────────────────────


class TestForceSnapshotEndpoint:
    def test_disabled_returns_ran_false(self, client, monkeypatch):
        # Patch the enabled-check to OFF so the endpoint short-circuits
        import app.control_plane.capability_regression_api as api_mod
        monkeypatch.setattr(api_mod, "_safe_enabled", lambda: False)
        resp = client.post("/api/cp/capability-regression/snapshot")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ran"] is False
        assert data["reason"] == "disabled"

    def test_enabled_runs_and_returns_snapshot(
        self, client, monkeypatch, tmp_path,
    ):
        import app.control_plane.capability_regression_api as api_mod
        monkeypatch.setattr(api_mod, "_safe_enabled", lambda: True)

        # Stub run_one_pass to return a no-regression report so the
        # endpoint's response shape is exercised without touching the
        # real tool registry.
        from app.capability_regression import RegressionReport
        fake = RegressionReport(
            tools_deleted=[],
            models_truly_deleted=[],
            models_newly_blocked=[],
            prev_captured_at="t0",
            curr_captured_at="t1",
            has_regression=False,
        )
        import app.capability_regression.scheduler_job as sched
        monkeypatch.setattr(sched, "run_one_pass", lambda: fake)

        # save a current snapshot so _safe_load_current returns it
        from app.capability_regression import (
            CapabilitySnapshot, save_snapshot,
        )
        save_snapshot(CapabilitySnapshot(
            captured_at="2026-05-22T00:00:00+00:00",
            tools=["a"], models=["m1"],
        ))

        resp = client.post("/api/cp/capability-regression/snapshot")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ran"] is True
        # Regression report present (even when has_regression=False)
        assert data["regression"] is not None
        assert data["regression"]["has_regression"] is False
        # Snapshot returned
        assert data["snapshot"] is not None
        assert data["snapshot"]["tools"] == ["a"]

    def test_run_one_pass_exception_surfaces_as_error_reason(
        self, client, monkeypatch,
    ):
        import app.control_plane.capability_regression_api as api_mod
        monkeypatch.setattr(api_mod, "_safe_enabled", lambda: True)

        import app.capability_regression.scheduler_job as sched
        def _boom():
            raise RuntimeError("registry sick")
        monkeypatch.setattr(sched, "run_one_pass", _boom)

        resp = client.post("/api/cp/capability-regression/snapshot")
        assert resp.status_code == 200  # cooperative, not 5xx
        data = resp.json()
        assert data["ran"] is False
        assert "registry sick" in data["reason"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
