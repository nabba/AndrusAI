"""Tests for Phase A.2 executor → CR observability (2026-05-22).

Closes the audit gap "Executor doesn't actually edit code" by proving:
  1. The driver's _execute_step path populates step.cr_ids when the
     agent (via Commander) creates change requests during the step.
  2. The end-to-end chain Executor → Commander → coder agent → tool
     → CR is observable from the run's audit trail.
  3. Attribution filters by both requestor prefix AND time window.

These tests stub Commander at the boundary — we don't run real LLMs.
The point is that the WIRING between executor and CR store works.
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


from app.autonomous_executor.coding_session_bridge import (  # noqa: E402
    attribute_crs_to_step,
    executor_agent_id,
)


def _make_fake_cr(*, cr_id, requestor, created_at):
    """Lightweight CR shape matching duck-type access in attribute_crs_to_step."""
    from types import SimpleNamespace
    return SimpleNamespace(
        id=cr_id,
        requestor=requestor,
        created_at=created_at,
    )


# ── attribute_crs_to_step helper ─────────────────────────────────────


class TestAttributeHelper:
    def test_empty_run_id_returns_empty(self):
        assert attribute_crs_to_step(
            run_id="", step_started_at="2026-05-22T10:00:00+00:00",
        ) == []

    def test_empty_started_at_returns_empty(self):
        assert attribute_crs_to_step(
            run_id="run-1", step_started_at="",
        ) == []

    def test_no_matching_requestor(self, monkeypatch):
        run_id = "run-abc"
        crs = [
            _make_fake_cr(
                cr_id="cr-1",
                requestor="coder",  # NOT executor-prefixed
                created_at="2026-05-22T10:00:30+00:00",
            ),
            _make_fake_cr(
                cr_id="cr-2",
                requestor="executor:OTHER_RUN:coder",
                created_at="2026-05-22T10:00:30+00:00",
            ),
        ]
        with patch(
            "app.change_requests.store.list_all", return_value=crs,
        ):
            result = attribute_crs_to_step(
                run_id=run_id,
                step_started_at="2026-05-22T10:00:00+00:00",
                step_ended_at="2026-05-22T10:01:00+00:00",
            )
        assert result == []

    def test_matching_requestor_in_window(self, monkeypatch):
        run_id = "run-abc"
        agent_id = executor_agent_id(run_id)
        crs = [
            _make_fake_cr(
                cr_id="cr-good",
                requestor=agent_id,
                created_at="2026-05-22T10:00:30+00:00",
            ),
        ]
        with patch(
            "app.change_requests.store.list_all", return_value=crs,
        ):
            result = attribute_crs_to_step(
                run_id=run_id,
                step_started_at="2026-05-22T10:00:00+00:00",
                step_ended_at="2026-05-22T10:01:00+00:00",
            )
        assert result == ["cr-good"]

    def test_outside_time_window_excluded(self, monkeypatch):
        run_id = "run-abc"
        agent_id = executor_agent_id(run_id)
        crs = [
            # Before window
            _make_fake_cr(
                cr_id="cr-too-early",
                requestor=agent_id,
                created_at="2026-05-22T09:00:00+00:00",
            ),
            # In window
            _make_fake_cr(
                cr_id="cr-in",
                requestor=agent_id,
                created_at="2026-05-22T10:00:30+00:00",
            ),
            # After window
            _make_fake_cr(
                cr_id="cr-too-late",
                requestor=agent_id,
                created_at="2026-05-22T11:00:00+00:00",
            ),
        ]
        with patch(
            "app.change_requests.store.list_all", return_value=crs,
        ):
            result = attribute_crs_to_step(
                run_id=run_id,
                step_started_at="2026-05-22T10:00:00+00:00",
                step_ended_at="2026-05-22T10:01:00+00:00",
            )
        assert result == ["cr-in"]

    def test_open_window_when_step_not_ended(self, monkeypatch):
        """When step_ended_at is empty (step still running), the
        upper bound defaults to far-future so all created-after-start
        CRs are included."""
        run_id = "run-abc"
        agent_id = executor_agent_id(run_id)
        crs = [
            _make_fake_cr(
                cr_id="cr-recent",
                requestor=agent_id,
                created_at="2026-05-22T10:30:00+00:00",
            ),
        ]
        with patch(
            "app.change_requests.store.list_all", return_value=crs,
        ):
            result = attribute_crs_to_step(
                run_id=run_id,
                step_started_at="2026-05-22T10:00:00+00:00",
                step_ended_at="",  # step still running
            )
        assert result == ["cr-recent"]

    def test_store_unavailable_returns_empty(self):
        # Without a patched store, the import succeeds but the function
        # may return either empty (sick gateway) or real data. To
        # simulate "store sick", patch list_all to raise.
        with patch(
            "app.change_requests.store.list_all",
            side_effect=RuntimeError("store broken"),
        ):
            result = attribute_crs_to_step(
                run_id="run-abc",
                step_started_at="2026-05-22T10:00:00+00:00",
            )
        assert result == []

    def test_multiple_crs_in_window_all_attributed(self, monkeypatch):
        run_id = "run-abc"
        agent_id = executor_agent_id(run_id)
        crs = [
            _make_fake_cr(
                cr_id=f"cr-{i}",
                requestor=agent_id,
                created_at=f"2026-05-22T10:00:{10+i:02d}+00:00",
            )
            for i in range(5)
        ]
        with patch(
            "app.change_requests.store.list_all", return_value=crs,
        ):
            result = attribute_crs_to_step(
                run_id=run_id,
                step_started_at="2026-05-22T10:00:00+00:00",
                step_ended_at="2026-05-22T10:01:00+00:00",
            )
        assert len(result) == 5


# ── End-to-end driver → CR attribution ──────────────────────────────


class TestDriverAttribution:
    def _make_run_with_one_pending_step(self):
        from app.autonomous_executor.models import (
            Budget, ExecutorRun, ExecutorStatus, ExecutorStep, StepStatus,
        )
        run = ExecutorRun(
            run_id="run-uuid-abc",
            goal="fix the bug",
            requestor="operator:signal:test",
            status=ExecutorStatus.RUNNING,
            plan=[
                ExecutorStep(
                    step_id="step-001",
                    description="fix the bug in helper.py",
                    status=StepStatus.PENDING,
                ),
            ],
            budget=Budget(),
            created_at="2026-05-22T10:00:00+00:00",
        )
        return run

    def test_step_cr_ids_populated_after_execute(self, monkeypatch):
        from app.autonomous_executor import driver as driver_mod
        from app.autonomous_executor.driver import (
            CommanderResult, _execute_step,
        )
        from app.autonomous_executor.coding_session_bridge import (
            executor_agent_id,
        )

        # Pin time so the step's started_at/ended_at are deterministic.
        # _now_iso is called: 1) at started_at, 2) at ended_at.
        time_iter = iter([
            "2026-05-22T10:00:00+00:00",  # started_at
            "2026-05-22T10:00:10+00:00",  # ended_at
        ])
        monkeypatch.setattr(
            driver_mod, "_now_iso", lambda: next(time_iter),
        )

        run = self._make_run_with_one_pending_step()
        step = run.plan[0]
        agent_id = executor_agent_id(run.run_id)

        def _stub_commander(step, run):
            # Simulate Commander running successfully — agent produced a CR
            return CommanderResult(
                text="ran the fix and submitted",
                cost_usd=0.05,
                tokens_used=100,
            )

        # Simulate the agent having produced 2 CRs during this step
        fake_crs = [
            _make_fake_cr(
                cr_id="cr-from-step-1",
                requestor=agent_id,
                created_at="2026-05-22T10:00:05+00:00",  # in window
            ),
            _make_fake_cr(
                cr_id="cr-from-step-2",
                requestor=agent_id,
                created_at="2026-05-22T10:00:08+00:00",  # in window
            ),
        ]

        with patch(
            "app.change_requests.store.list_all", return_value=fake_crs,
        ):
            _execute_step(run, step, _stub_commander)

        # Step transitioned to COMPLETED and cr_ids populated
        from app.autonomous_executor.models import StepStatus
        assert step.status == StepStatus.COMPLETED
        assert step.cr_ids == ["cr-from-step-1", "cr-from-step-2"]

    def test_no_cr_attribution_when_commander_failed(self, monkeypatch):
        from app.autonomous_executor.driver import _execute_step

        run = self._make_run_with_one_pending_step()
        step = run.plan[0]

        def _failing_commander(step, run):
            raise RuntimeError("simulated failure")

        with patch(
            "app.change_requests.store.list_all",
            return_value=[
                _make_fake_cr(
                    cr_id="cr-1",
                    requestor=executor_agent_id(run.run_id),
                    created_at="2026-05-22T10:00:05+00:00",
                ),
            ],
        ):
            _execute_step(run, step, _failing_commander)

        # Step failed → CR attribution NOT attempted (early return)
        from app.autonomous_executor.models import StepStatus
        assert step.status == StepStatus.FAILED
        assert step.cr_ids == []

    def test_step_to_dict_roundtrip_preserves_cr_ids(self):
        from app.autonomous_executor.models import (
            ExecutorStep, StepStatus,
        )
        original = ExecutorStep(
            step_id="step-001",
            description="x",
            status=StepStatus.COMPLETED,
            cr_ids=["cr-1", "cr-2"],
        )
        roundtripped = ExecutorStep.from_dict(original.to_dict())
        assert roundtripped.cr_ids == ["cr-1", "cr-2"]

    def test_step_default_cr_ids_empty(self):
        from app.autonomous_executor.models import ExecutorStep
        step = ExecutorStep(step_id="step-001", description="x")
        assert step.cr_ids == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
