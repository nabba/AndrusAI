"""Tests for the executor_milestone identity-continuity event kind
(Verified Implementation Plan Gap #3, 2026-05-22).

Pins:
  * "executor_milestone" is in IDENTITY_EVENT_KINDS (kind #20).
  * Every ExecutorRun.transition() emits one milestone row.
  * The emission is failure-isolated (ledger import / write errors
    don't propagate into the run's transition path).
  * summarise_drift's dynamic Counter picks the kind up.
  * The transition's side effects (status, ended_at, etc.) STILL
    happen even when the ledger is unavailable.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    try:
        spec.loader.exec_module(m)
    except Exception:
        return None
    return m


cl = _load("_cl_g3", "app/identity/continuity_ledger.py")
models = _load("_models_g3", "app/autonomous_executor/models.py")


# ── Kind registered ─────────────────────────────────────────────────


@pytest.mark.skipif(cl is None, reason="continuity_ledger not loadable")
def test_executor_milestone_in_known_kinds():
    assert "executor_milestone" in cl.IDENTITY_EVENT_KINDS


# ── Emission on transition ──────────────────────────────────────────


@pytest.mark.skipif(
    models is None or cl is None,
    reason="models / continuity_ledger not loadable",
)
class TestTransitionEmits:
    def _make_run(self, **overrides):
        from app.autonomous_executor.models import (  # noqa: E402
            Budget, ExecutorRun, ExecutorStatus,
        )
        # In our isolated load path, the names are on the loaded module
        ExecutorRun = models.ExecutorRun
        Budget = models.Budget
        base = dict(
            run_id="run-g3-test",
            goal="exercise milestone emission",
            requestor="operator:signal:test",
            status=models.ExecutorStatus.CREATED,
            budget=Budget(cap_usd=1.0),
        )
        base.update(overrides)
        return ExecutorRun(**base)

    def test_transition_records_milestone(self, tmp_path, monkeypatch):
        # Point the ledger at a tmp file
        monkeypatch.setattr(cl, "_path_override", tmp_path / "ledger.jsonl")
        monkeypatch.setattr(cl, "_enabled", lambda: True)
        # Wire models' lazy import to point at the loaded ledger module
        monkeypatch.setitem(
            sys.modules, "app.identity.continuity_ledger", cl,
        )

        run = self._make_run()
        run.transition(models.ExecutorStatus.PLANNING)

        # Read the ledger
        lines = (tmp_path / "ledger.jsonl").read_text().strip().splitlines()
        assert len(lines) == 1, "expected exactly one milestone row"
        row = json.loads(lines[0])
        assert row["kind"] == "executor_milestone"
        assert "created → planning" in row["summary"]
        assert row["detail"]["run_id"] == "run-g3-test"
        assert row["detail"]["from"] == "created"
        assert row["detail"]["to"] == "planning"

    def test_each_transition_records_one_row(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr(cl, "_path_override", tmp_path / "ledger.jsonl")
        monkeypatch.setattr(cl, "_enabled", lambda: True)
        monkeypatch.setitem(
            sys.modules, "app.identity.continuity_ledger", cl,
        )

        run = self._make_run()
        run.transition(models.ExecutorStatus.PLANNING)
        run.transition(models.ExecutorStatus.RUNNING)
        run.transition(models.ExecutorStatus.COMPLETED)

        lines = (tmp_path / "ledger.jsonl").read_text().strip().splitlines()
        assert len(lines) == 3
        kinds = [json.loads(line)["detail"]["to"] for line in lines]
        assert kinds == ["planning", "running", "completed"]

    def test_blocked_transition_carries_reason(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr(cl, "_path_override", tmp_path / "ledger.jsonl")
        monkeypatch.setattr(cl, "_enabled", lambda: True)
        monkeypatch.setitem(
            sys.modules, "app.identity.continuity_ledger", cl,
        )

        run = self._make_run()
        run.transition(models.ExecutorStatus.PLANNING)
        run.transition(models.ExecutorStatus.RUNNING)
        run.transition(
            models.ExecutorStatus.BLOCKED,
            reason="waiting on AWS creds",
        )

        lines = (tmp_path / "ledger.jsonl").read_text().strip().splitlines()
        # Find the BLOCKED row
        blocked_rows = [
            json.loads(line) for line in lines
            if json.loads(line)["detail"]["to"] == "blocked"
        ]
        assert len(blocked_rows) == 1
        assert blocked_rows[0]["detail"]["reason"] == "waiting on AWS creds"
        assert "waiting on AWS creds" in blocked_rows[0]["summary"]


# ── Failure isolation ───────────────────────────────────────────────


@pytest.mark.skipif(models is None, reason="models not loadable")
class TestFailureIsolation:
    def _make_run(self):
        return models.ExecutorRun(
            run_id="run-g3-iso",
            goal="test",
            requestor="operator:signal:test",
            status=models.ExecutorStatus.CREATED,
            budget=models.Budget(cap_usd=1.0),
        )

    def test_ledger_unavailable_does_not_break_transition(
        self, monkeypatch,
    ):
        # Remove the ledger module from sys.modules and ensure
        # importlib.import_module fails
        if "app.identity.continuity_ledger" in sys.modules:
            monkeypatch.delitem(
                sys.modules, "app.identity.continuity_ledger",
            )

        # Inject an import_module that raises for the ledger
        import importlib

        original = importlib.import_module

        def _broken(name, *args, **kwargs):
            if name == "app.identity.continuity_ledger":
                raise ImportError("simulated unavailable")
            return original(name, *args, **kwargs)

        monkeypatch.setattr(importlib, "import_module", _broken)

        # Transition must still work
        run = self._make_run()
        run.transition(models.ExecutorStatus.PLANNING)
        assert run.status is models.ExecutorStatus.PLANNING
        assert run.started_at  # side effect of first PLANNING entry

    def test_ledger_write_error_does_not_break_transition(
        self, tmp_path, monkeypatch,
    ):
        # Force record_event to raise
        if cl is not None:
            def _raises(**kwargs):
                raise OSError("disk full")

            monkeypatch.setattr(cl, "record_event", _raises)
            monkeypatch.setitem(
                sys.modules, "app.identity.continuity_ledger", cl,
            )

        run = self._make_run()
        run.transition(models.ExecutorStatus.PLANNING)
        # Side effect must still have happened
        assert run.status is models.ExecutorStatus.PLANNING


# ── summarise_drift picks up the new kind ───────────────────────────


@pytest.mark.skipif(cl is None, reason="continuity_ledger not loadable")
def test_summarise_drift_surfaces_executor_milestone(tmp_path, monkeypatch):
    # Seed the ledger directly with 3 executor_milestone events
    log = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(cl, "_path_override", log)
    monkeypatch.setattr(cl, "_enabled", lambda: True)
    log.parent.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with log.open("w", encoding="utf-8") as fp:
        for i in range(3):
            fp.write(json.dumps({
                "ts": now, "kind": "executor_milestone",
                "actor": "autonomous_executor",
                "summary": f"row {i}", "detail": {},
            }) + "\n")
    summary = cl.summarise_drift(window_days=30)
    # DriftSummary dataclass — auto-includes any kind that appears
    by_kind = summary.by_kind
    assert by_kind.get("executor_milestone") == 3


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
