"""Tests for the autonomous-executor planner + driver (2026-05-20).

Covers Phase 2 piece 2a:
  * planner: deterministic single-step output, error paths
  * driver.advance_one_step state pathways:
      - CREATED → planning+RUNNING in one call
      - RUNNING → execute next step
      - All steps done → COMPLETED (or FAILED on any step failure)
      - Budget exhaustion → BUDGET_EXHAUSTED
      - Step failure isolation
      - Terminal idempotency
      - Multi-step end-to-end via repeated advance_one_step
"""
from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import MagicMock

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


from app.autonomous_executor import (  # noqa: E402
    CommanderResult,
    ExecutorRun,
    ExecutorStatus,
    ExecutorStep,
    StepStatus,
    advance_one_step,
    plan,
)


_run_counter = 0


def _make_run(goal: str = "test goal", **kwargs) -> ExecutorRun:
    global _run_counter
    _run_counter += 1
    return ExecutorRun(
        run_id=f"run-{_run_counter:04d}",
        goal=goal,
        requestor="test:harness",
        **kwargs,
    )


# ============================================================================
# Planner
# ============================================================================


class TestPlanner(unittest.TestCase):
    def test_single_step_for_simple_goal(self):
        run = _make_run("do the thing")
        steps = plan("do the thing", run)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].description, "do the thing")
        self.assertEqual(steps[0].status, StepStatus.PENDING)

    def test_goal_is_stripped(self):
        run = _make_run("   spacey goal   ")
        steps = plan("   spacey goal   ", run)
        self.assertEqual(steps[0].description, "spacey goal")

    def test_empty_goal_rejected(self):
        run = _make_run("x")
        with self.assertRaises(ValueError):
            plan("", run)
        with self.assertRaises(ValueError):
            plan("   ", run)

    def test_non_string_goal_rejected(self):
        run = _make_run("x")
        with self.assertRaises(ValueError):
            plan(None, run)  # type: ignore[arg-type]

    def test_step_id_assigned(self):
        run = _make_run("x")
        steps = plan("x", run)
        self.assertTrue(steps[0].step_id.startswith("step-"))


# ============================================================================
# Driver: CREATED → RUNNING (planning collapsed)
# ============================================================================


class TestDriverCreatedToRunning(unittest.TestCase):
    def test_one_advance_call_drives_created_to_running(self):
        run = _make_run()
        out = advance_one_step(run)
        # No commander_fn needed because we don't execute a step yet.
        self.assertEqual(out.status, ExecutorStatus.RUNNING)
        self.assertEqual(len(out.plan), 1)
        self.assertEqual(out.plan[0].status, StepStatus.PENDING)

    def test_planner_returning_empty_transitions_to_failed(self):
        run = _make_run()
        out = advance_one_step(
            run,
            planner_fn=lambda goal, run: [],
        )
        self.assertEqual(out.status, ExecutorStatus.FAILED)
        self.assertIn("empty plan", out.failure_reason)

    def test_planner_raising_transitions_to_failed(self):
        run = _make_run()

        def _boom(goal, run):
            raise RuntimeError("planner died")

        out = advance_one_step(run, planner_fn=_boom)
        self.assertEqual(out.status, ExecutorStatus.FAILED)
        self.assertIn("planner failed", out.failure_reason)

    def test_planner_returning_multiple_steps(self):
        run = _make_run()

        def _multi(goal, run):
            return [
                ExecutorStep(step_id="x", description="step A"),
                ExecutorStep(step_id="y", description="step B"),
                ExecutorStep(step_id="z", description="step C"),
            ]

        out = advance_one_step(run, planner_fn=_multi)
        self.assertEqual(len(out.plan), 3)
        # The driver overrides the planner's step_ids deterministically.
        self.assertEqual(out.plan[0].step_id, "step-001")
        self.assertEqual(out.plan[1].step_id, "step-002")
        self.assertEqual(out.plan[2].step_id, "step-003")


# ============================================================================
# Driver: PLANNING recovery (defensive)
# ============================================================================


class TestDriverPlanningRecovery(unittest.TestCase):
    def test_planning_with_no_plan_invokes_planner(self):
        # Synthesise the unusual state: a run stuck in PLANNING with
        # no steps. This is what a crash mid-planning would leave.
        run = _make_run()
        run.transition(ExecutorStatus.PLANNING)
        self.assertEqual(run.plan, [])
        out = advance_one_step(run)
        self.assertEqual(out.status, ExecutorStatus.RUNNING)
        self.assertEqual(len(out.plan), 1)

    def test_planning_with_existing_plan_transitions_to_running(self):
        run = _make_run()
        run.transition(ExecutorStatus.PLANNING)
        run.add_step(description="manual step")
        out = advance_one_step(run)
        self.assertEqual(out.status, ExecutorStatus.RUNNING)


# ============================================================================
# Driver: RUNNING — step execution
# ============================================================================


def _stub_commander(text: str = "done", cost: float = 0.01, tokens: int = 100):
    """Build a commander_fn that always returns the same canned result."""
    def _fn(step, run):
        return CommanderResult(text=text, cost_usd=cost, tokens_used=tokens)
    return _fn


class TestDriverRunningExecution(unittest.TestCase):
    def test_running_executes_next_pending_step(self):
        run = _make_run()
        advance_one_step(run)  # CREATED → RUNNING with 1 step
        out = advance_one_step(run, commander_fn=_stub_commander("result"))
        # Single step completed → run finalised to COMPLETED
        self.assertEqual(out.status, ExecutorStatus.COMPLETED)
        self.assertEqual(out.plan[0].status, StepStatus.COMPLETED)
        self.assertEqual(out.plan[0].result_text, "result")

    def test_running_charges_budget_per_step(self):
        run = _make_run()
        advance_one_step(run)
        commander = _stub_commander(cost=0.25, tokens=500)
        advance_one_step(run, commander_fn=commander)
        self.assertAlmostEqual(run.budget.spent_usd, 0.25)
        self.assertEqual(run.budget.spent_tokens, 500)

    def test_running_step_failure_isolated_to_step(self):
        run = _make_run()
        advance_one_step(run)

        def _boom(step, run):
            raise RuntimeError("step exploded")

        out = advance_one_step(run, commander_fn=_boom)
        # Single step → after the failure, finalise runs → FAILED.
        self.assertEqual(out.status, ExecutorStatus.FAILED)
        self.assertEqual(out.plan[0].status, StepStatus.FAILED)
        self.assertIn("step exploded", out.plan[0].failure_reason)

    def test_running_missing_commander_raises(self):
        run = _make_run()
        advance_one_step(run)
        # Now RUNNING with a pending step — commander_fn is required.
        with self.assertRaises(RuntimeError):
            advance_one_step(run)

    def test_running_with_no_pending_steps_finalises(self):
        # Synthesise the state: RUNNING but all steps already done.
        run = _make_run()
        advance_one_step(run)
        # Mark the only step COMPLETED manually so finalise fires.
        run.plan[0].status = StepStatus.COMPLETED
        out = advance_one_step(run, commander_fn=_stub_commander())
        self.assertEqual(out.status, ExecutorStatus.COMPLETED)

    def test_invalid_commander_return_marks_step_failed(self):
        run = _make_run()
        advance_one_step(run)

        def _bad(step, run):
            return "not a CommanderResult"  # bug

        out = advance_one_step(run, commander_fn=_bad)
        self.assertEqual(out.plan[0].status, StepStatus.FAILED)
        self.assertIn("CommanderResult", out.plan[0].failure_reason)


# ============================================================================
# Driver: multi-step end-to-end
# ============================================================================


class TestDriverMultiStep(unittest.TestCase):
    def test_three_step_plan_completes(self):
        run = _make_run()

        def _planner(goal, run):
            return [
                ExecutorStep(step_id="a", description="A"),
                ExecutorStep(step_id="b", description="B"),
                ExecutorStep(step_id="c", description="C"),
            ]

        commander = _stub_commander(cost=0.01, tokens=10)
        # 1st call: CREATED → RUNNING with 3 pending
        advance_one_step(run, planner_fn=_planner)
        self.assertEqual(len(run.plan), 3)
        self.assertEqual(run.status, ExecutorStatus.RUNNING)

        # 2nd, 3rd, 4th call: execute each step
        advance_one_step(run, commander_fn=commander)
        advance_one_step(run, commander_fn=commander)
        advance_one_step(run, commander_fn=commander)

        # All steps complete; finalise (last advance call already did it
        # because all-steps-done check runs after consume).
        self.assertEqual(run.status, ExecutorStatus.COMPLETED)
        for step in run.plan:
            self.assertEqual(step.status, StepStatus.COMPLETED)
        self.assertAlmostEqual(run.budget.spent_usd, 0.03)
        self.assertEqual(run.budget.spent_tokens, 30)

    def test_partial_failure_transitions_to_failed(self):
        run = _make_run()

        def _planner(goal, run):
            return [
                ExecutorStep(step_id="a", description="A"),
                ExecutorStep(step_id="b", description="B"),
            ]

        advance_one_step(run, planner_fn=_planner)

        # Step A succeeds, Step B fails.
        commander_calls: list[str] = []

        def _commander(step, run):
            commander_calls.append(step.description)
            if step.description == "B":
                raise RuntimeError("B broke")
            return CommanderResult(text="A ok", cost_usd=0.0, tokens_used=0)

        advance_one_step(run, commander_fn=_commander)  # A
        advance_one_step(run, commander_fn=_commander)  # B (fails)

        self.assertEqual(run.status, ExecutorStatus.FAILED)
        self.assertEqual(run.plan[0].status, StepStatus.COMPLETED)
        self.assertEqual(run.plan[1].status, StepStatus.FAILED)
        self.assertIn("step-002", run.failure_reason)


# ============================================================================
# Driver: budget exhaustion
# ============================================================================


class TestDriverBudgetExhaustion(unittest.TestCase):
    def test_budget_exhausted_stops_remaining_steps(self):
        run = _make_run()
        # Tight USD budget — first step will exhaust it.
        run.budget.cap_usd = 0.05

        def _planner(goal, run):
            return [
                ExecutorStep(step_id="a", description="A"),
                ExecutorStep(step_id="b", description="B"),
                ExecutorStep(step_id="c", description="C"),
            ]

        advance_one_step(run, planner_fn=_planner)
        # Step that consumes the entire budget on the first run.
        commander = _stub_commander(cost=0.05, tokens=1)
        advance_one_step(run, commander_fn=commander)
        # First step completes, budget hits exactly the cap, run goes
        # to BUDGET_EXHAUSTED because steps B and C remain pending.
        self.assertEqual(run.status, ExecutorStatus.BUDGET_EXHAUSTED)
        self.assertEqual(run.plan[0].status, StepStatus.COMPLETED)
        self.assertEqual(run.plan[1].status, StepStatus.PENDING)
        self.assertEqual(run.plan[2].status, StepStatus.PENDING)
        self.assertIn("budget exhausted", run.failure_reason or "")
        # ``failure_reason`` is for FAILED; BUDGET_EXHAUSTED doesn't
        # set it. Sanity check ended_at is populated for terminal state.
        self.assertNotEqual(run.ended_at, "")

    def test_pre_check_refuses_step_when_already_exhausted(self):
        run = _make_run()
        run.budget.cap_usd = 0.01
        run.budget.consume(usd=0.01)  # already at cap

        def _planner(goal, run):
            return [ExecutorStep(step_id="a", description="A")]

        advance_one_step(run, planner_fn=_planner)
        # Now RUNNING with budget already exhausted.
        out = advance_one_step(run, commander_fn=_stub_commander())
        self.assertEqual(out.status, ExecutorStatus.BUDGET_EXHAUSTED)
        self.assertEqual(out.plan[0].status, StepStatus.PENDING)


# ============================================================================
# Driver: terminal idempotency
# ============================================================================


class TestDriverIdempotency(unittest.TestCase):
    def test_terminal_completed_is_noop(self):
        run = _make_run()
        advance_one_step(run)
        advance_one_step(run, commander_fn=_stub_commander())
        # Run is now COMPLETED.
        self.assertEqual(run.status, ExecutorStatus.COMPLETED)
        # Further advances are no-ops; status unchanged, no exception.
        before_status = run.status
        before_touched = run.last_touched_at
        advance_one_step(run, commander_fn=_stub_commander())
        advance_one_step(run, commander_fn=_stub_commander())
        self.assertEqual(run.status, before_status)
        # touch() is not called on terminal — last_touched_at stable.
        self.assertEqual(run.last_touched_at, before_touched)

    def test_terminal_aborted_is_noop(self):
        run = _make_run()
        run.transition(ExecutorStatus.ABORTED, reason="test")
        advance_one_step(run)  # no commander_fn needed for noop
        self.assertEqual(run.status, ExecutorStatus.ABORTED)

    def test_blocked_is_noop_pending_resume(self):
        run = _make_run()
        advance_one_step(run)  # RUNNING with 1 step
        run.transition(ExecutorStatus.BLOCKED, reason="op decision")
        before_status = run.status
        advance_one_step(run)
        # Driver does NOT auto-resume — operator must transition
        # back to RUNNING explicitly.
        self.assertEqual(run.status, before_status)


if __name__ == "__main__":
    unittest.main()
