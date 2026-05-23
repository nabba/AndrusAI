"""Tests for the autonomous-executor foundation (2026-05-20).

Covers Phase 2 piece 1:
  * ExecutorStatus + transition legality
  * ExecutorStep + StepStatus serialisation round-trip
  * ExecutorRun lifecycle + terminal-state immutability
  * Budget tracker (consume monotonic, exhaustion detection, wall-clock)
  * JSON-per-record store (save + get + list_active + list_terminal)
  * runtime_settings (master switch + 3 budget defaults + hard ceilings)

Safety invariants pinned:
  * terminal states are immutable (no outbound transitions allowed)
  * self-transitions are rejected
  * budget caps cannot be raised above EXECUTOR_BUDGET_CAPS via setters
  * budget.consume is monotonic (negative amounts rejected)
  * wall-clock budget survives a save/restore round-trip
  * store.save persists across reset_for_tests when base_dir is reused
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Stubs (defensive — defer to real crewai when available) ──────────
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


from app import runtime_settings  # noqa: E402
from app.autonomous_executor import (  # noqa: E402
    Budget,
    ExecutorRun,
    ExecutorStatus,
    ExecutorStep,
    InvalidExecutorTransition,
    StepStatus,
    TERMINAL_STATUSES,
    assert_can_transition,
    store,
)


def _reset_runtime_settings() -> None:
    runtime_settings._cache = None  # type: ignore[attr-defined]


def _patch_runtime_settings(**overrides):
    base = runtime_settings._defaults()
    base.update(overrides)
    return patch.object(runtime_settings, "_cache", base)


def _make_run(goal: str = "test goal") -> ExecutorRun:
    return ExecutorRun(
        run_id=f"run-{time.time_ns()}",
        goal=goal,
        requestor="test:harness",
    )


# ============================================================================
# State machine
# ============================================================================


class TestExecutorStatusTransitions(unittest.TestCase):
    def test_created_can_advance_to_planning_or_aborted(self):
        assert_can_transition(ExecutorStatus.CREATED, ExecutorStatus.PLANNING)
        assert_can_transition(ExecutorStatus.CREATED, ExecutorStatus.ABORTED)

    def test_created_cannot_skip_to_running(self):
        with self.assertRaises(InvalidExecutorTransition):
            assert_can_transition(ExecutorStatus.CREATED, ExecutorStatus.RUNNING)

    def test_self_transition_rejected(self):
        with self.assertRaises(InvalidExecutorTransition):
            assert_can_transition(ExecutorStatus.RUNNING, ExecutorStatus.RUNNING)

    def test_terminal_states_have_no_outbound(self):
        for terminal in TERMINAL_STATUSES:
            for target in ExecutorStatus:
                if target is terminal:
                    continue
                with self.assertRaises(InvalidExecutorTransition):
                    assert_can_transition(terminal, target)

    def test_running_to_all_terminal_paths_legal(self):
        for target in (
            ExecutorStatus.COMPLETED,
            ExecutorStatus.FAILED,
            ExecutorStatus.BUDGET_EXHAUSTED,
            ExecutorStatus.ABORTED,
        ):
            assert_can_transition(ExecutorStatus.RUNNING, target)

    def test_blocked_can_resume_to_running(self):
        assert_can_transition(ExecutorStatus.BLOCKED, ExecutorStatus.RUNNING)

    def test_paused_can_resume_to_running(self):
        assert_can_transition(ExecutorStatus.PAUSED, ExecutorStatus.RUNNING)

    def test_blocked_cannot_go_to_completed_directly(self):
        # Must resume to RUNNING first, then transition to COMPLETED.
        with self.assertRaises(InvalidExecutorTransition):
            assert_can_transition(
                ExecutorStatus.BLOCKED, ExecutorStatus.COMPLETED,
            )


# ============================================================================
# ExecutorRun lifecycle
# ============================================================================


class TestExecutorRunLifecycle(unittest.TestCase):
    def test_default_status_is_created(self):
        run = _make_run()
        self.assertEqual(run.status, ExecutorStatus.CREATED)
        self.assertFalse(run.is_terminal)

    def test_transition_to_planning_sets_started_at(self):
        run = _make_run()
        self.assertEqual(run.started_at, "")
        run.transition(ExecutorStatus.PLANNING)
        self.assertNotEqual(run.started_at, "")

    def test_transition_to_running_starts_clock(self):
        run = _make_run()
        run.transition(ExecutorStatus.PLANNING)
        self.assertEqual(run.budget.started_at_monotonic, 0.0)
        run.transition(ExecutorStatus.RUNNING)
        self.assertGreater(run.budget.started_at_monotonic, 0.0)

    def test_transition_to_terminal_sets_ended_at(self):
        run = _make_run()
        run.transition(ExecutorStatus.PLANNING)
        run.transition(ExecutorStatus.RUNNING)
        self.assertEqual(run.ended_at, "")
        run.transition(ExecutorStatus.COMPLETED)
        self.assertNotEqual(run.ended_at, "")
        self.assertTrue(run.is_terminal)

    def test_failed_transition_records_reason(self):
        run = _make_run()
        run.transition(ExecutorStatus.PLANNING)
        run.transition(ExecutorStatus.RUNNING)
        run.transition(ExecutorStatus.FAILED, reason="LLM refused")
        self.assertEqual(run.failure_reason, "LLM refused")

    def test_aborted_transition_records_reason(self):
        run = _make_run()
        run.transition(ExecutorStatus.ABORTED, reason="operator cancelled")
        self.assertEqual(run.abort_reason, "operator cancelled")
        self.assertTrue(run.is_terminal)

    def test_terminal_state_cannot_transition(self):
        run = _make_run()
        run.transition(ExecutorStatus.PLANNING)
        run.transition(ExecutorStatus.RUNNING)
        run.transition(ExecutorStatus.COMPLETED)
        with self.assertRaises(InvalidExecutorTransition):
            run.transition(ExecutorStatus.RUNNING)
        with self.assertRaises(InvalidExecutorTransition):
            run.transition(ExecutorStatus.FAILED)

    def test_add_step_only_in_planning(self):
        run = _make_run()
        with self.assertRaises(InvalidExecutorTransition):
            run.add_step(description="too early")
        run.transition(ExecutorStatus.PLANNING)
        step = run.add_step(description="step 1")
        self.assertEqual(step.status, StepStatus.PENDING)
        self.assertEqual(step.step_id, "step-001")
        run.transition(ExecutorStatus.RUNNING)
        with self.assertRaises(InvalidExecutorTransition):
            run.add_step(description="too late")

    def test_record_note_works_in_terminal_state(self):
        # Notes are allowed in terminal states for post-mortem
        # annotation — pinned safety semantics.
        run = _make_run()
        run.transition(ExecutorStatus.ABORTED, reason="test")
        run.record_note("post-mortem note")
        self.assertTrue(any("post-mortem note" in n for n in run.notes))

    def test_record_note_ignores_empty(self):
        run = _make_run()
        run.record_note("   ")
        run.record_note("")
        self.assertEqual(run.notes, [])

    def test_blocked_can_resume(self):
        run = _make_run()
        run.transition(ExecutorStatus.PLANNING)
        run.transition(ExecutorStatus.RUNNING)
        run.transition(ExecutorStatus.BLOCKED, reason="waiting on signal")
        self.assertEqual(run.blocked_reason, "waiting on signal")
        run.transition(ExecutorStatus.RUNNING)
        self.assertEqual(run.status, ExecutorStatus.RUNNING)
        self.assertFalse(run.is_terminal)


# ============================================================================
# Budget tracker
# ============================================================================


class TestBudget(unittest.TestCase):
    def test_default_caps(self):
        b = Budget()
        self.assertAlmostEqual(b.cap_usd, 1.0)
        self.assertEqual(b.cap_tokens, 20_000)
        self.assertEqual(b.cap_wall_clock_s, 600)

    def test_can_afford_under_cap(self):
        b = Budget(cap_usd=1.0)
        self.assertTrue(b.can_afford(usd=0.5))
        b.consume(usd=0.5)
        self.assertTrue(b.can_afford(usd=0.5))

    def test_can_afford_refuses_over_cap(self):
        b = Budget(cap_usd=1.0)
        b.consume(usd=0.8)
        self.assertFalse(b.can_afford(usd=0.5))

    def test_consume_monotonic(self):
        b = Budget()
        b.consume(usd=0.10, tokens=100)
        b.consume(usd=0.05, tokens=50)
        self.assertAlmostEqual(b.spent_usd, 0.15)
        self.assertEqual(b.spent_tokens, 150)

    def test_consume_rejects_negative(self):
        b = Budget()
        with self.assertRaises(ValueError):
            b.consume(usd=-1.0)
        with self.assertRaises(ValueError):
            b.consume(tokens=-1)

    def test_is_exhausted_on_usd(self):
        b = Budget(cap_usd=0.5)
        b.consume(usd=0.5)
        self.assertTrue(b.is_exhausted())

    def test_is_exhausted_on_tokens(self):
        b = Budget(cap_tokens=100)
        b.consume(tokens=100)
        self.assertTrue(b.is_exhausted())

    def test_start_clock_idempotent(self):
        b = Budget()
        b.start_clock()
        first = b.started_at_monotonic
        b.start_clock()
        self.assertEqual(b.started_at_monotonic, first)

    def test_wall_clock_survives_serialisation(self):
        b = Budget(cap_wall_clock_s=600)
        b.start_clock()
        time.sleep(0.05)  # 50ms — short but non-zero elapsed
        before = b.elapsed_s()
        # Round-trip through dict
        d = b.to_dict()
        b2 = Budget.from_dict(d)
        after = b2.elapsed_s()
        # ``after`` should be >= ``before`` (elapsed_s grows past
        # the saved snapshot) and within tens of ms of it.
        self.assertGreaterEqual(after, before - 0.01)

    def test_remaining_calcs(self):
        b = Budget(cap_usd=1.0, cap_tokens=1000)
        b.consume(usd=0.4, tokens=300)
        self.assertAlmostEqual(b.remaining_usd(), 0.6)
        self.assertEqual(b.remaining_tokens(), 700)


# ============================================================================
# JSON store
# ============================================================================


class TestExecutorStore(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        store.reset_for_tests(Path(self.tmp.name))

    def tearDown(self) -> None:
        store.reset_for_tests(None)
        self.tmp.cleanup()

    def test_save_and_get_roundtrip(self):
        run = _make_run()
        run.transition(ExecutorStatus.PLANNING)
        run.add_step(description="step 1")
        store.save(run)

        loaded = store.get(run.run_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.goal, run.goal)
        self.assertEqual(len(loaded.plan), 1)
        self.assertEqual(loaded.plan[0].description, "step 1")
        self.assertEqual(loaded.status, ExecutorStatus.PLANNING)

    def test_get_nonexistent_returns_none(self):
        self.assertIsNone(store.get("nope"))

    def test_list_active_excludes_terminal(self):
        active_run = _make_run(goal="active")
        active_run.transition(ExecutorStatus.PLANNING)
        store.save(active_run)

        terminal_run = _make_run(goal="terminal")
        terminal_run.transition(ExecutorStatus.ABORTED, reason="test")
        store.save(terminal_run)

        active = store.list_active()
        active_ids = {r.run_id for r in active}
        self.assertIn(active_run.run_id, active_ids)
        self.assertNotIn(terminal_run.run_id, active_ids)

    def test_list_terminal_includes_only_terminal(self):
        active = _make_run(goal="active")
        active.transition(ExecutorStatus.PLANNING)
        store.save(active)

        terminal = _make_run(goal="completed")
        terminal.transition(ExecutorStatus.PLANNING)
        terminal.transition(ExecutorStatus.RUNNING)
        terminal.transition(ExecutorStatus.COMPLETED)
        store.save(terminal)

        listed = store.list_terminal()
        listed_ids = {r.run_id for r in listed}
        self.assertIn(terminal.run_id, listed_ids)
        self.assertNotIn(active.run_id, listed_ids)

    def test_persistence_across_reset(self):
        # save → reset_for_tests(same base_dir) → get still works
        run = _make_run()
        run.transition(ExecutorStatus.PLANNING)
        store.save(run)

        store.reset_for_tests(Path(self.tmp.name))
        loaded = store.get(run.run_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.goal, run.goal)

    def test_atomic_write_via_tmp_then_rename(self):
        run = _make_run()
        store.save(run)
        record_path = Path(self.tmp.name) / f"{run.run_id}.json"
        self.assertTrue(record_path.exists())
        # The .tmp file should not linger after a successful save
        self.assertFalse(record_path.with_suffix(".json.tmp").exists())
        # File contents are valid JSON
        loaded = json.loads(record_path.read_text())
        self.assertEqual(loaded["run_id"], run.run_id)


# ============================================================================
# Runtime settings
# ============================================================================


class TestExecutorRuntimeSettings(unittest.TestCase):
    def setUp(self) -> None:
        _reset_runtime_settings()

    def test_master_switch_defaults_off(self):
        with _patch_runtime_settings():
            self.assertFalse(runtime_settings.get_autonomous_executor_enabled())

    def test_master_switch_set_get(self):
        with _patch_runtime_settings(), patch.object(runtime_settings, "_save"):
            runtime_settings.set_autonomous_executor_enabled(True)
            self.assertTrue(
                runtime_settings.get_autonomous_executor_enabled(),
            )

    def test_default_budget_usd_default(self):
        with _patch_runtime_settings():
            self.assertAlmostEqual(
                runtime_settings.get_executor_default_budget_usd(), 1.0,
            )

    def test_default_budget_tokens_default(self):
        with _patch_runtime_settings():
            self.assertEqual(
                runtime_settings.get_executor_default_budget_tokens(),
                20_000,
            )

    def test_default_wall_clock_default(self):
        with _patch_runtime_settings():
            self.assertEqual(
                runtime_settings.get_executor_default_wall_clock_s(), 600,
            )

    def test_setter_refuses_negative_usd(self):
        with _patch_runtime_settings(), patch.object(runtime_settings, "_save"):
            with self.assertRaises(ValueError):
                runtime_settings.set_executor_default_budget_usd(-1.0)

    def test_setter_refuses_above_hard_ceiling_usd(self):
        with _patch_runtime_settings(), patch.object(runtime_settings, "_save"):
            ceiling = runtime_settings.EXECUTOR_BUDGET_CAPS["max_usd_per_run"]
            with self.assertRaises(ValueError) as ctx:
                runtime_settings.set_executor_default_budget_usd(ceiling + 1.0)
            self.assertIn("hard ceiling", str(ctx.exception))

    def test_setter_refuses_above_hard_ceiling_tokens(self):
        with _patch_runtime_settings(), patch.object(runtime_settings, "_save"):
            ceiling = runtime_settings.EXECUTOR_BUDGET_CAPS[
                "max_tokens_per_run"
            ]
            with self.assertRaises(ValueError):
                runtime_settings.set_executor_default_budget_tokens(
                    int(ceiling) + 1,
                )

    def test_setter_refuses_above_hard_ceiling_wall_clock(self):
        with _patch_runtime_settings(), patch.object(runtime_settings, "_save"):
            ceiling = runtime_settings.EXECUTOR_BUDGET_CAPS[
                "max_wall_clock_s_per_run"
            ]
            with self.assertRaises(ValueError):
                runtime_settings.set_executor_default_wall_clock_s(
                    int(ceiling) + 1,
                )

    def test_setter_refuses_zero_wall_clock(self):
        with _patch_runtime_settings(), patch.object(runtime_settings, "_save"):
            with self.assertRaises(ValueError):
                runtime_settings.set_executor_default_wall_clock_s(0)


# ============================================================================
# Serialisation round-trip
# ============================================================================


class TestSerialisationRoundtrip(unittest.TestCase):
    def test_executor_step_roundtrip(self):
        step = ExecutorStep(
            step_id="step-001",
            description="do the thing",
            crew_hint="coding",
            status=StepStatus.COMPLETED,
            result_text="done",
            cost_usd=0.42,
            tokens_used=1234,
        )
        d = step.to_dict()
        reloaded = ExecutorStep.from_dict(d)
        self.assertEqual(reloaded.step_id, step.step_id)
        self.assertEqual(reloaded.description, step.description)
        self.assertEqual(reloaded.crew_hint, step.crew_hint)
        self.assertEqual(reloaded.status, StepStatus.COMPLETED)
        self.assertAlmostEqual(reloaded.cost_usd, 0.42)
        self.assertEqual(reloaded.tokens_used, 1234)

    def test_executor_run_full_roundtrip(self):
        run = _make_run(goal="big task")
        run.transition(ExecutorStatus.PLANNING)
        run.add_step(description="step 1", crew_hint="research")
        run.add_step(description="step 2")
        run.transition(ExecutorStatus.RUNNING)
        run.budget.consume(usd=0.20, tokens=2000)
        run.record_note("midway checkpoint")
        run.transition(ExecutorStatus.COMPLETED)

        d = run.to_dict()
        reloaded = ExecutorRun.from_dict(d)
        self.assertEqual(reloaded.run_id, run.run_id)
        self.assertEqual(reloaded.goal, run.goal)
        self.assertEqual(reloaded.status, ExecutorStatus.COMPLETED)
        self.assertEqual(len(reloaded.plan), 2)
        self.assertEqual(reloaded.plan[0].crew_hint, "research")
        self.assertAlmostEqual(reloaded.budget.spent_usd, 0.20)
        self.assertEqual(reloaded.budget.spent_tokens, 2000)
        self.assertTrue(any("midway" in n for n in reloaded.notes))

    def test_unknown_status_falls_back_to_created(self):
        d = {"run_id": "x", "goal": "y", "requestor": "z", "status": "bogus"}
        reloaded = ExecutorRun.from_dict(d)
        self.assertEqual(reloaded.status, ExecutorStatus.CREATED)


if __name__ == "__main__":
    unittest.main()
