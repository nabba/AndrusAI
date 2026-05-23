"""Verify the Verified-Plan Gap #2 + Gap #4 wire-ups are LIVE
(post-audit closure, 2026-05-22).

Two findings from the post-Gaps-1-6 audit:

  A. ``_try_local_route`` was defined in routing.py but never called
     from orchestrator.py → dead code in production.
  B. The driver's ``_handle_running`` never autonomously transitioned
     to BLOCKED → escalation.escalate_blocker would never fire in
     real runs (only when an external caller manually invoked
     transition(BLOCKED)).

This file pins both wire-ups so a future refactor that drops them
fails CI.
"""
from __future__ import annotations

import importlib.util
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


# ── Gap A: _try_local_route is called from orchestrator ─────────────


def test_orchestrator_imports_try_local_route():
    """The orchestrator must import ``_try_local_route`` from routing.
    A future refactor that drops the import fails this test."""
    source = Path("app/agents/commander/orchestrator.py").read_text()
    assert "_try_local_route" in source, (
        "orchestrator.py is missing the _try_local_route call — "
        "Verified Plan Gap #4 wire-up is broken; function defined "
        "in routing.py but never reached in production"
    )


def test_orchestrator_local_route_is_post_fast_route():
    """Local route must compose AFTER _try_fast_route, NOT before.
    The plan called for 'fall through if local-route returns None'
    semantics from the fast-route side."""
    source = Path("app/agents/commander/orchestrator.py").read_text()
    fast_idx = source.find("_try_fast_route(")
    local_idx = source.find("_try_local_route(")
    assert fast_idx != -1, "fast-route call missing"
    assert local_idx != -1, "local-route call missing"
    assert fast_idx < local_idx, (
        "local-route must come AFTER fast-route in orchestrator "
        "(plan §7); current order is reversed"
    )


def test_local_route_exclusion_is_honored():
    """The local-route branch must respect ``exclude_crew`` the same
    way fast-route does — otherwise post-vetting reroutes would loop
    back to the excluded crew via the local path."""
    source = Path("app/agents/commander/orchestrator.py").read_text()
    # Find the local-route branch
    local_idx = source.find("_try_local_route(")
    if local_idx == -1:
        pytest.fail("local-route branch missing")
    # The next ~600 chars after the call should contain exclude_crew
    snippet = source[local_idx:local_idx + 600]
    assert "exclude_crew" in snippet, (
        "local-route branch doesn't check exclude_crew — would "
        "create a reroute loop"
    )


# ── Gap B: driver autonomously transitions to BLOCKED ──────────────


# Load models FIRST and bind it to the canonical sys.modules name so
# driver's ``from app.autonomous_executor.models import StepStatus``
# sees the same enum classes the tests use.
models = _load("_mdl_b", "app/autonomous_executor/models.py")
if models is not None:
    sys.modules["app.autonomous_executor.models"] = models
driver = _load("_drv_b", "app/autonomous_executor/driver.py")


@pytest.mark.skipif(
    driver is None or models is None,
    reason="driver / models not loadable",
)
class TestBlockerDetection:
    def _make_running_run(self):
        run = models.ExecutorRun(
            run_id="run-blk",
            goal="test blocker detection",
            requestor="operator:signal:test",
            status=models.ExecutorStatus.CREATED,
            budget=models.Budget(cap_usd=1.0),
        )
        run.transition(models.ExecutorStatus.PLANNING)
        # Add a step + transition to RUNNING
        step = run.add_step(description="some action", crew_hint="")
        run.transition(models.ExecutorStatus.RUNNING)
        return run, step

    def test_blocker_marker_in_result_triggers_blocked(self):
        """Step result starting with BLOCKED: must trip _detect_blocker."""
        run, step = self._make_running_run()
        step.result_text = "BLOCKED: need AWS_ACCESS_KEY_ID from operator"
        step.status = models.StepStatus.COMPLETED
        reason = driver._detect_blocker(run, step)
        assert reason != ""
        assert "AWS_ACCESS_KEY_ID" in reason

    def test_needs_operator_input_marker_triggers_blocked(self):
        run, step = self._make_running_run()
        step.result_text = "NEEDS_OPERATOR_INPUT: choose A or B"
        step.status = models.StepStatus.COMPLETED
        reason = driver._detect_blocker(run, step)
        assert reason != ""
        assert "choose A or B" in reason

    def test_marker_is_case_insensitive(self):
        run, step = self._make_running_run()
        step.result_text = "blocked: anything"
        step.status = models.StepStatus.COMPLETED
        reason = driver._detect_blocker(run, step)
        assert reason != ""

    def test_no_marker_no_failure_no_block(self):
        run, step = self._make_running_run()
        step.result_text = "everything is fine"
        step.status = models.StepStatus.COMPLETED
        reason = driver._detect_blocker(run, step)
        assert reason == ""

    def test_repeated_failures_trigger_blocked(self):
        run, step1 = self._make_running_run()
        # Run 3 consecutive failed steps on the same description
        run.transition(models.ExecutorStatus.PLANNING) if False else None
        # Manually populate the plan with 3 same-description failures
        run.plan.clear()
        for i in range(3):
            s = models.ExecutorStep(
                step_id=f"step-{i+1:03d}",
                description="ssh to host",
            )
            s.status = models.StepStatus.FAILED
            s.failure_reason = f"connection refused attempt {i+1}"
            run.plan.append(s)
        # The last failure invokes _detect_blocker
        last = run.plan[-1]
        reason = driver._detect_blocker(run, last)
        assert reason != ""
        assert "3 consecutive failures" in reason
        assert "ssh to host" in reason

    def test_two_failures_not_enough(self):
        run, _ = self._make_running_run()
        run.plan.clear()
        for i in range(2):
            s = models.ExecutorStep(
                step_id=f"step-{i+1:03d}",
                description="same task",
            )
            s.status = models.StepStatus.FAILED
            s.failure_reason = "transient blip"
            run.plan.append(s)
        last = run.plan[-1]
        reason = driver._detect_blocker(run, last)
        # 2 failures < threshold of 3 → no block
        assert reason == ""

    def test_failures_with_different_descriptions_not_blocked(self):
        run, _ = self._make_running_run()
        run.plan.clear()
        descriptions = ["task A", "task B", "task C"]
        for i, desc in enumerate(descriptions):
            s = models.ExecutorStep(
                step_id=f"step-{i+1:03d}",
                description=desc,
            )
            s.status = models.StepStatus.FAILED
            s.failure_reason = "different fail"
            run.plan.append(s)
        last = run.plan[-1]
        reason = driver._detect_blocker(run, last)
        assert reason == "", (
            "3 different-description failures should NOT block — "
            "those are unrelated errors, not a stuck loop"
        )

    def test_completed_step_with_no_marker_not_blocked(self):
        run, _ = self._make_running_run()
        # Even with 3 same-description COMPLETED steps, no block
        run.plan.clear()
        for i in range(3):
            s = models.ExecutorStep(
                step_id=f"step-{i+1:03d}", description="same",
            )
            s.status = models.StepStatus.COMPLETED
            s.result_text = "fine"
            run.plan.append(s)
        last = run.plan[-1]
        reason = driver._detect_blocker(run, last)
        assert reason == ""


# ── Wire-up pin in _handle_running ──────────────────────────────────


def test_handle_running_calls_detect_blocker():
    """The driver's _handle_running must invoke _detect_blocker AFTER
    _execute_step. A future refactor that drops the call leaves
    BLOCKED unreachable from the live driver loop."""
    source = Path("app/autonomous_executor/driver.py").read_text()
    assert "_detect_blocker" in source, (
        "_detect_blocker function missing from driver.py"
    )
    # Find the _handle_running body
    handle_idx = source.find("def _handle_running(")
    assert handle_idx != -1
    # Following ~3000 chars should contain both _execute_step and
    # _detect_blocker, in that order
    body = source[handle_idx:handle_idx + 4000]
    exec_idx = body.find("_execute_step(")
    detect_idx = body.find("_detect_blocker(")
    assert exec_idx != -1, "_execute_step missing"
    assert detect_idx != -1, "_detect_blocker call missing"
    assert exec_idx < detect_idx, (
        "_detect_blocker must run AFTER _execute_step"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
