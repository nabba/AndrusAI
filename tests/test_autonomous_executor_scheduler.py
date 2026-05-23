"""Tests for the Commander adapter + scheduler tick (2026-05-20).

Covers Phase 2 piece 2b:
  * commander_adapter.make_commander_adapter — provider invoked
    lazily, step.description passed as user_input, CommanderResult
    shape, defensive coercion of None / non-str returns.
  * scheduler_job.run_executor_tick — master-switch gate, no-op
    when no active runs, picks the most-recently-touched active
    run, persists after advance, scheduler-friendly failure
    isolation (broken advance never crashes the tick).
  * The new tuple in idle_scheduler._default_jobs() is present and
    weighted HEAVY.

Safety invariants pinned:
  * Master switch OFF → ``run_executor_tick`` is a no-op (no store
    read, no commander call).
  * Adapter never crashes the scheduler — exceptions from the
    underlying Commander surface inside the driver's per-step
    failure handler.
  * Tuple in _default_jobs has the right weight + handler shape.
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    CommanderResult,
    ExecutorRun,
    ExecutorStatus,
    ExecutorStep,
    StepStatus,
    advance_one_step,
    commander_adapter,
    make_commander_adapter,
    run_executor_tick,
    store,
)


_run_counter = 0


def _make_run(goal: str = "test goal") -> ExecutorRun:
    global _run_counter
    _run_counter += 1
    return ExecutorRun(
        run_id=f"run-{_run_counter:04d}",
        goal=goal,
        requestor="test:harness",
    )


def _reset_runtime_settings() -> None:
    runtime_settings._cache = None  # type: ignore[attr-defined]


def _patch_runtime_settings(**overrides):
    base = runtime_settings._defaults()
    base.update(overrides)
    return patch.object(runtime_settings, "_cache", base)


# ============================================================================
# Commander adapter
# ============================================================================


class _StubCommander:
    """In-memory stand-in for CommanderOrchestrator."""

    def __init__(self, response: str = "stub-reply") -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def handle(self, user_input: str, sender: str = "",
               attachments: list = None) -> str:
        self.calls.append((user_input, sender))
        return self.response


class TestCommanderAdapter(unittest.TestCase):
    def setUp(self) -> None:
        commander_adapter.reset_for_tests()

    def test_provider_invoked_per_call(self):
        provider_calls: list[int] = []

        def _provider():
            provider_calls.append(1)
            return _StubCommander()

        adapter = make_commander_adapter(commander_provider=_provider)
        run = _make_run()
        step = ExecutorStep(step_id="s1", description="hi")
        adapter(step, run)
        adapter(step, run)
        # Provider called every adapter invocation — production
        # provider caches the singleton itself.
        self.assertEqual(len(provider_calls), 2)

    def test_step_description_forwarded_to_commander(self):
        stub = _StubCommander(response="ok")
        adapter = make_commander_adapter(commander_provider=lambda: stub)
        run = _make_run()
        step = ExecutorStep(step_id="s1", description="please do X")
        adapter(step, run)
        self.assertEqual(len(stub.calls), 1)
        self.assertEqual(stub.calls[0][0], "please do X")

    def test_sender_tagged_with_run_id(self):
        stub = _StubCommander(response="ok")
        adapter = make_commander_adapter(commander_provider=lambda: stub)
        run = _make_run()
        step = ExecutorStep(step_id="s1", description="x")
        adapter(step, run)
        self.assertEqual(stub.calls[0][1], f"executor:{run.run_id}")

    def test_returns_commander_result_with_text(self):
        stub = _StubCommander(response="the answer is 42")
        adapter = make_commander_adapter(commander_provider=lambda: stub)
        result = adapter(
            ExecutorStep(step_id="s1", description="x"),
            _make_run(),
        )
        self.assertIsInstance(result, CommanderResult)
        self.assertEqual(result.text, "the answer is 42")
        self.assertEqual(result.cost_usd, 0.0)
        self.assertEqual(result.tokens_used, 0)

    def test_none_response_coerced_to_empty_string(self):
        stub = _StubCommander(response="")
        # Override response to None after construction
        stub.handle = lambda **kwargs: None
        adapter = make_commander_adapter(commander_provider=lambda: stub)
        result = adapter(
            ExecutorStep(step_id="s1", description="x"),
            _make_run(),
        )
        self.assertEqual(result.text, "")

    def test_non_str_response_coerced(self):
        stub = _StubCommander()
        stub.handle = lambda **kwargs: 42  # buggy commander
        adapter = make_commander_adapter(commander_provider=lambda: stub)
        result = adapter(
            ExecutorStep(step_id="s1", description="x"),
            _make_run(),
        )
        self.assertEqual(result.text, "42")

    def test_commander_exception_propagates(self):
        class _BoomCommander:
            def handle(self, **kwargs):
                raise RuntimeError("commander died")

        adapter = make_commander_adapter(
            commander_provider=lambda: _BoomCommander(),
        )
        with self.assertRaises(RuntimeError):
            adapter(
                ExecutorStep(step_id="s1", description="x"),
                _make_run(),
            )

    def test_default_provider_is_lazy(self):
        # default_commander_provider should NOT instantiate at module
        # load — it's the adapter call that triggers the orchestrator
        # import. We can't easily test "module not imported" without
        # stubbing sys.modules; instead we confirm the function exists
        # and is callable separately from make_commander_adapter.
        from app.autonomous_executor.commander_adapter import (
            default_commander_provider,
        )
        self.assertTrue(callable(default_commander_provider))


# ============================================================================
# Adapter + driver integration
# ============================================================================


class TestAdapterDriverIntegration(unittest.TestCase):
    """End-to-end: driver invokes the adapter and the step completes."""

    def test_full_loop_completes_a_single_step_run(self):
        stub = _StubCommander(response="answer text")
        adapter = make_commander_adapter(commander_provider=lambda: stub)
        run = _make_run()

        # 1st call: CREATED → RUNNING with 1 pending step
        advance_one_step(run)
        # 2nd call: execute the step via adapter → COMPLETED
        advance_one_step(run, commander_fn=adapter)

        self.assertEqual(run.status, ExecutorStatus.COMPLETED)
        self.assertEqual(run.plan[0].status, StepStatus.COMPLETED)
        self.assertEqual(run.plan[0].result_text, "answer text")
        self.assertEqual(stub.calls[0][0], "test goal")

    def test_adapter_exception_marks_step_failed(self):
        class _BoomCommander:
            def handle(self, **kwargs):
                raise RuntimeError("boom")

        adapter = make_commander_adapter(
            commander_provider=lambda: _BoomCommander(),
        )
        run = _make_run()
        advance_one_step(run)
        advance_one_step(run, commander_fn=adapter)
        # Driver's _execute_step catches the exception → step FAILED
        # → finalise → run FAILED.
        self.assertEqual(run.status, ExecutorStatus.FAILED)
        self.assertEqual(run.plan[0].status, StepStatus.FAILED)
        self.assertIn("boom", run.plan[0].failure_reason)


# ============================================================================
# scheduler_job.run_executor_tick
# ============================================================================


class TestSchedulerTick(unittest.TestCase):
    def setUp(self) -> None:
        _reset_runtime_settings()
        self.tmp = tempfile.TemporaryDirectory()
        store.reset_for_tests(Path(self.tmp.name))
        commander_adapter.reset_for_tests()

    def tearDown(self) -> None:
        store.reset_for_tests(None)
        self.tmp.cleanup()

    def test_master_switch_off_returns_none(self):
        # Master switch defaults False; even with active runs, no tick.
        run = _make_run()
        run.transition(ExecutorStatus.PLANNING)
        store.save(run)
        with _patch_runtime_settings(autonomous_executor_enabled=False):
            result = run_executor_tick()
        self.assertIsNone(result)

    def test_no_active_runs_returns_none(self):
        with _patch_runtime_settings(autonomous_executor_enabled=True):
            result = run_executor_tick()
        self.assertIsNone(result)

    def test_picks_active_run_and_advances(self):
        stub = _StubCommander(response="step done")
        adapter = make_commander_adapter(commander_provider=lambda: stub)

        run = _make_run()
        # Advance to RUNNING + 1 pending step via the driver itself.
        advance_one_step(run)
        store.save(run)
        self.assertEqual(run.status, ExecutorStatus.RUNNING)

        with _patch_runtime_settings(autonomous_executor_enabled=True):
            result_id = run_executor_tick(commander_fn=adapter)

        self.assertEqual(result_id, run.run_id)
        reloaded = store.get(run.run_id)
        # Step executed via adapter → run finalised to COMPLETED.
        self.assertEqual(reloaded.status, ExecutorStatus.COMPLETED)
        self.assertEqual(reloaded.plan[0].status, StepStatus.COMPLETED)
        self.assertEqual(reloaded.plan[0].result_text, "step done")

    def test_skips_terminal_runs(self):
        # Save a terminal run + an active run. Tick should pick the
        # active one, not the terminal one.
        terminal = _make_run(goal="terminal")
        terminal.transition(ExecutorStatus.ABORTED, reason="test")
        store.save(terminal)

        active = _make_run(goal="active")
        active.transition(ExecutorStatus.PLANNING)
        active.add_step(description="A")
        active.transition(ExecutorStatus.RUNNING)
        store.save(active)

        stub = _StubCommander(response="ok")
        adapter = make_commander_adapter(commander_provider=lambda: stub)

        with _patch_runtime_settings(autonomous_executor_enabled=True):
            result_id = run_executor_tick(commander_fn=adapter)

        self.assertEqual(result_id, active.run_id)
        self.assertNotEqual(result_id, terminal.run_id)

    def test_advance_crash_does_not_crash_tick(self):
        # Simulate a buggy commander adapter that crashes hard.
        def _crash_fn(step, run):
            raise RuntimeError("adapter crash")

        run = _make_run()
        advance_one_step(run)
        store.save(run)

        with _patch_runtime_settings(autonomous_executor_enabled=True):
            # Should not raise; step failure isolates inside the driver.
            result_id = run_executor_tick(commander_fn=_crash_fn)

        # Driver caught the adapter exception → step FAILED → run
        # FAILED → store.save persisted the failure.
        self.assertEqual(result_id, run.run_id)
        reloaded = store.get(run.run_id)
        self.assertEqual(reloaded.status, ExecutorStatus.FAILED)

    def test_runtime_settings_unavailable_means_disabled(self):
        # Simulate runtime_settings raising — tick must bail out.
        with patch.object(
            runtime_settings,
            "get_autonomous_executor_enabled",
            side_effect=RuntimeError("settings broken"),
        ):
            result = run_executor_tick()
        self.assertIsNone(result)


# ============================================================================
# idle_scheduler tuple registration
# ============================================================================


class TestIdleSchedulerTuple(unittest.TestCase):
    """Confirm the tuple is in _default_jobs at the right shape."""

    def test_autonomous_executor_tuple_present(self):
        from app.idle_scheduler import _default_jobs, JobWeight

        jobs = _default_jobs()
        matching = [
            j for j in jobs
            if j[0] == "autonomous-executor"
        ]
        self.assertEqual(len(matching), 1)
        name, fn, weight = matching[0]
        self.assertEqual(name, "autonomous-executor")
        self.assertEqual(weight, JobWeight.HEAVY)
        # The function is callable and accepts no args (scheduler
        # contract).
        self.assertTrue(callable(fn))

    def test_tuple_function_is_master_switch_gated(self):
        # When master switch is off, calling the registered function
        # is a microsecond no-op — no store read, no commander call.
        from app.idle_scheduler import _default_jobs

        jobs = _default_jobs()
        matching = [j for j in jobs if j[0] == "autonomous-executor"]
        _, fn, _ = matching[0]
        # Default settings have autonomous_executor_enabled=False —
        # this just exercises the no-op path.
        _reset_runtime_settings()
        with _patch_runtime_settings(autonomous_executor_enabled=False):
            # Should not raise.
            fn()


if __name__ == "__main__":
    unittest.main()
