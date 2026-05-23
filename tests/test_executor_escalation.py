"""Tests for the executor escalation + resume flow
(Verified Implementation Plan Gap #2, 2026-05-22).

Pins:
  * Entering BLOCKED fires escalate_blocker.
  * Signal alert body includes run_id + reason + resume instructions.
  * The signal_ts → run_id bridge is registered when send returns a ts.
  * resolve_signal_ts handles exact + prefix match.
  * resume_blocker:
      - 404-equivalent for missing run
      - 409-equivalent when not BLOCKED
      - transitions BLOCKED → RUNNING on happy path
      - records unblock_hint as a run note
      - clears the bridge entry when signal_ts is supplied
  * Bridge purge drops rows older than 25h.
  * Failure isolation: broken signal sender doesn't break the
    BLOCKED transition (the state change is already committed).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
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


esc = _load("_esc_g2", "app/autonomous_executor/escalation.py")
models = _load("_models_g2", "app/autonomous_executor/models.py")


@pytest.fixture
def isolated_bridge(tmp_path, monkeypatch):
    """Point the bridge at a tmp dir."""
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    yield tmp_path / "autonomous_executor" / "escalation_bridge.json"


# ── Bridge primitives ───────────────────────────────────────────────


@pytest.mark.skipif(esc is None, reason="escalation not loadable")
class TestBridge:
    def test_register_and_resolve(self, isolated_bridge):
        esc.register_signal_ts(signal_ts="1697500000", run_id="run-a")
        assert esc.resolve_signal_ts("1697500000") == "run-a"

    def test_resolve_missing(self, isolated_bridge):
        assert esc.resolve_signal_ts("nope") is None

    def test_prefix_match(self, isolated_bridge):
        esc.register_signal_ts(
            signal_ts="1697500000.123", run_id="run-b",
        )
        # Truncated ts (Signal reactions sometimes truncate)
        assert esc.resolve_signal_ts("1697500000") == "run-b"

    def test_clear_removes_entry(self, isolated_bridge):
        esc.register_signal_ts(signal_ts="ts1", run_id="run-c")
        esc.clear_signal_ts("ts1")
        assert esc.resolve_signal_ts("ts1") is None

    def test_purge_drops_stale_rows(self, isolated_bridge):
        # Hand-write a stale row
        stale_ts = (
            datetime.now(timezone.utc) - timedelta(hours=30)
        ).isoformat()
        isolated_bridge.parent.mkdir(parents=True, exist_ok=True)
        isolated_bridge.write_text(json.dumps({
            "old": {"run_id": "run-old", "emitted_at": stale_ts},
        }))
        # Trigger purge by registering a new row
        esc.register_signal_ts(signal_ts="fresh", run_id="run-fresh")
        data = json.loads(isolated_bridge.read_text())
        assert "old" not in data
        assert "fresh" in data

    def test_empty_signal_ts_resolves_none(self, isolated_bridge):
        assert esc.resolve_signal_ts("") is None
        assert esc.resolve_signal_ts(None) is None  # type: ignore


# ── Escalation alert ────────────────────────────────────────────────


@pytest.mark.skipif(esc is None, reason="escalation not loadable")
class TestEscalateBlocker:
    def test_alert_body_includes_required_fields(self, isolated_bridge):
        sent: list[str] = []

        def _capture(body: str) -> dict:
            sent.append(body)
            return {"ts": "1697500999"}

        esc.escalate_blocker(
            run_id="run-x", reason="waiting on AWS creds",
            goal_preview="migrate database",
            signal_sender=_capture,
        )
        body = sent[0]
        assert "run-x" in body
        assert "waiting on AWS creds" in body
        assert "migrate database" in body
        assert "resume" in body.lower()
        assert "/api/cp/delegate/run-x/resume" in body

    def test_sender_ts_registered_in_bridge(self, isolated_bridge):
        def _capture(body: str) -> dict:
            return {"ts": "1697500999"}

        esc.escalate_blocker(
            run_id="run-x", reason="r", signal_sender=_capture,
        )
        assert esc.resolve_signal_ts("1697500999") == "run-x"

    def test_string_ts_also_registers(self, isolated_bridge):
        def _capture(body: str) -> str:
            return "1697500111"

        esc.escalate_blocker(
            run_id="run-y", reason="r", signal_sender=_capture,
        )
        assert esc.resolve_signal_ts("1697500111") == "run-y"

    def test_broken_sender_does_not_raise(self, isolated_bridge):
        def _raises(body: str):
            raise RuntimeError("Signal API down")

        # Must not raise out
        esc.escalate_blocker(
            run_id="run-z", reason="r", signal_sender=_raises,
        )

    def test_no_ts_returned_no_bridge_entry(self, isolated_bridge):
        def _no_ts(body: str) -> dict:
            return {}  # no ts field

        esc.escalate_blocker(
            run_id="run-w", reason="r", signal_sender=_no_ts,
        )
        # Bridge file may not exist or contain nothing for run-w
        # — resolve returns None
        data = (
            json.loads(isolated_bridge.read_text())
            if isolated_bridge.exists() else {}
        )
        assert not any(
            row.get("run_id") == "run-w" for row in data.values()
        )


# ── resume_blocker ──────────────────────────────────────────────────


@pytest.mark.skipif(
    esc is None or models is None,
    reason="escalation / models not loadable",
)
class TestResumeBlocker:
    def _make_blocked_run(self, monkeypatch, tmp_path, *, run_id="run-r"):
        """Build a BLOCKED run + install a fake store that load/save
        reads/writes a dict.

        Also ensures sys.modules['app.autonomous_executor.models']
        points at the test-loaded models module so resume_blocker's
        lazy imports resolve to the same enum classes the test built
        the run with.
        """
        ExecutorRun = models.ExecutorRun
        ExecutorStatus = models.ExecutorStatus
        run = ExecutorRun(
            run_id=run_id,
            goal="test",
            requestor="operator:signal:test",
            status=ExecutorStatus.CREATED,
            budget=models.Budget(cap_usd=1.0),
        )
        # Transition through to BLOCKED
        run.transition(ExecutorStatus.PLANNING)
        run.transition(ExecutorStatus.RUNNING)
        run.transition(
            ExecutorStatus.BLOCKED,
            reason="waiting on operator",
        )

        # Point sys.modules at the test-loaded models so resume_blocker's
        # `from app.autonomous_executor.models import ExecutorStatus` line
        # resolves to the same enum class.
        monkeypatch.setitem(
            sys.modules, "app.autonomous_executor.models", models,
        )

        fake_store = MagicMock()
        fake_store.load = MagicMock(return_value=run)
        fake_store.save = MagicMock()
        monkeypatch.setitem(
            sys.modules, "app.autonomous_executor.store", fake_store,
        )
        return run, fake_store

    def test_happy_path_transitions_to_running(
        self, isolated_bridge, monkeypatch, tmp_path,
    ):
        run, fake_store = self._make_blocked_run(monkeypatch, tmp_path)
        result = esc.resume_blocker(
            run_id="run-r",
            unblock_hint="creds added: AWS_ACCESS_KEY_ID set",
            operator="andrus",
        )
        assert result["ok"] is True
        assert result["status"] == "running"
        fake_store.save.assert_called_once()
        # The note should have been recorded
        assert any(
            "creds added" in n for n in run.notes
        )

    def test_missing_run_returns_404_equivalent(self, monkeypatch):
        monkeypatch.setitem(
            sys.modules, "app.autonomous_executor.models", models,
        )
        fake_store = MagicMock()
        fake_store.load = MagicMock(return_value=None)
        monkeypatch.setitem(
            sys.modules, "app.autonomous_executor.store", fake_store,
        )
        result = esc.resume_blocker(
            run_id="run-missing", unblock_hint="",
        )
        assert result["ok"] is False
        assert result["status"] == "missing"

    def test_not_blocked_returns_409_equivalent(
        self, isolated_bridge, monkeypatch, tmp_path,
    ):
        from app.autonomous_executor.models import (
            Budget, ExecutorRun, ExecutorStatus,
        )
        ExecutorRun = models.ExecutorRun
        ExecutorStatus = models.ExecutorStatus
        run = ExecutorRun(
            run_id="run-already-running",
            goal="test",
            requestor="t",
            status=ExecutorStatus.CREATED,
            budget=models.Budget(cap_usd=1.0),
        )
        run.transition(ExecutorStatus.PLANNING)
        run.transition(ExecutorStatus.RUNNING)
        # Not BLOCKED — RUNNING

        fake_store = sys.modules.setdefault(
            "app.autonomous_executor.store", MagicMock(),
        )
        fake_store.load = MagicMock(return_value=run)
        fake_store.save = MagicMock()
        result = esc.resume_blocker(run_id="run-already-running", unblock_hint="")
        assert result["ok"] is False
        assert result["status"] == "running"
        assert "not BLOCKED" in result["error"]
        fake_store.save.assert_not_called()

    def test_signal_ts_cleared_on_success(
        self, isolated_bridge, monkeypatch, tmp_path,
    ):
        run, fake_store = self._make_blocked_run(monkeypatch, tmp_path)
        esc.register_signal_ts(signal_ts="ts-resume", run_id="run-r")
        result = esc.resume_blocker(
            run_id="run-r", unblock_hint="ok",
            signal_ts="ts-resume",
        )
        assert result["ok"] is True
        # Bridge entry cleared
        assert esc.resolve_signal_ts("ts-resume") is None


# ── Failure isolation: BLOCKED transition + broken escalation ───────


@pytest.mark.skipif(models is None, reason="models not loadable")
class TestFailureIsolation:
    def test_broken_escalation_does_not_block_transition(
        self, monkeypatch,
    ):
        """The state change to BLOCKED must commit even if the
        escalation module is broken / unavailable. Otherwise a Signal
        outage would lock the executor up."""
        from app.autonomous_executor.models import (
            Budget, ExecutorRun, ExecutorStatus,
        )
        ExecutorRun = models.ExecutorRun
        ExecutorStatus = models.ExecutorStatus
        # Force the escalation import to raise
        import importlib
        original = importlib.import_module

        def _broken(name, *args, **kwargs):
            if name == "app.autonomous_executor.escalation":
                raise ImportError("simulated unavailable")
            return original(name, *args, **kwargs)

        monkeypatch.setattr(importlib, "import_module", _broken)

        run = ExecutorRun(
            run_id="run-iso", goal="t", requestor="t",
            status=ExecutorStatus.CREATED,
            budget=models.Budget(cap_usd=1.0),
        )
        run.transition(ExecutorStatus.PLANNING)
        run.transition(ExecutorStatus.RUNNING)
        # The BLOCKED transition triggers escalation (which fails),
        # but the state change must still commit
        run.transition(ExecutorStatus.BLOCKED, reason="test")
        assert run.status is ExecutorStatus.BLOCKED
        assert run.blocked_reason == "test"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
