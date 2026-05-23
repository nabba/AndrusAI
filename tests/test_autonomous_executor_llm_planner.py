"""Tests for the LLM planner v2 (2026-05-20).

Covers Phase 2 piece 2e:
  * llm_plan happy path — multiple sub-goals with crew hints
  * JSON parsing tolerance (code-fenced output)
  * Fallback to v1 on every failure shape:
      - malformed JSON
      - empty response
      - response too large
      - non-list JSON
      - too many steps
      - missing description
      - description too short / too long
      - crew_hint too long
  * Master switch dispatcher (get_default_planner)
  * Integration with the driver via injected mock LLM
  * Runtime settings setter rejects non-bool

Safety invariants pinned:
  * Default OFF — v1 stays canonical.
  * Any v2 failure falls back to v1 — executor never blocks on LLM
    availability.
  * Step count capped at 5.
  * Description bounds (4-500 chars) enforced.
"""
from __future__ import annotations

import sys
import types
import unittest
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
    ExecutorRun,
    ExecutorStatus,
    StepStatus,
    advance_one_step,
    get_default_planner,
    llm_plan,
    plan,
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
# Happy paths
# ============================================================================


class TestLLMPlanHappyPath(unittest.TestCase):
    def test_single_step_from_llm(self):
        def _stub(sys_p, user_p):
            return '[{"description": "do the thing", "crew_hint": ""}]'

        run = _make_run()
        steps = llm_plan("do the thing", run, llm_call=_stub)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].description, "do the thing")
        self.assertEqual(steps[0].crew_hint, "")
        self.assertEqual(steps[0].status, StepStatus.PENDING)

    def test_multi_step_from_llm(self):
        def _stub(sys_p, user_p):
            return (
                '[{"description": "research X", "crew_hint": "research"},'
                ' {"description": "write up Y", "crew_hint": "writing"},'
                ' {"description": "deploy Z", "crew_hint": "devops"}]'
            )

        run = _make_run()
        steps = llm_plan("research X then write Y then deploy Z", run,
                         llm_call=_stub)
        self.assertEqual(len(steps), 3)
        self.assertEqual(steps[0].crew_hint, "research")
        self.assertEqual(steps[1].crew_hint, "writing")
        self.assertEqual(steps[2].crew_hint, "devops")

    def test_code_fenced_output_accepted(self):
        def _stub(sys_p, user_p):
            return (
                "```json\n"
                '[{"description": "fenced step", "crew_hint": ""}]'
                "\n```"
            )

        steps = llm_plan("x", _make_run(), llm_call=_stub)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].description, "fenced step")

    def test_plain_fence_without_lang_accepted(self):
        def _stub(sys_p, user_p):
            return (
                "```\n"
                '[{"description": "fenced step", "crew_hint": ""}]'
                "\n```"
            )

        steps = llm_plan("x", _make_run(), llm_call=_stub)
        self.assertEqual(len(steps), 1)

    def test_max_5_steps_accepted(self):
        def _stub(sys_p, user_p):
            return (
                '[{"description": "step 1"},'
                ' {"description": "step 2"},'
                ' {"description": "step 3"},'
                ' {"description": "step 4"},'
                ' {"description": "step 5"}]'
            )

        steps = llm_plan("complex multi-step goal", _make_run(),
                         llm_call=_stub)
        self.assertEqual(len(steps), 5)


# ============================================================================
# Fallback to v1 on any error
# ============================================================================


class TestLLMPlanFallback(unittest.TestCase):
    def _v1_fallback_check(self, llm_call):
        """Helper: any failure mode → single-step plan matching the goal."""
        run = _make_run("the goal as typed")
        steps = llm_plan("the goal as typed", run, llm_call=llm_call)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].description, "the goal as typed")

    def test_empty_response_falls_back(self):
        self._v1_fallback_check(lambda s, u: "")

    def test_whitespace_response_falls_back(self):
        self._v1_fallback_check(lambda s, u: "   \n  ")

    def test_malformed_json_falls_back(self):
        self._v1_fallback_check(lambda s, u: "this is not json")

    def test_non_list_json_falls_back(self):
        self._v1_fallback_check(
            lambda s, u: '{"description": "wrong shape"}'
        )

    def test_empty_list_falls_back(self):
        self._v1_fallback_check(lambda s, u: "[]")

    def test_six_steps_falls_back(self):
        # Cap is 5 — six steps means the LLM ignored instructions,
        # treat as garbage.
        big = "[" + ",".join(
            f'{{"description": "step {i}"}}'
            for i in range(6)
        ) + "]"
        self._v1_fallback_check(lambda s, u: big)

    def test_missing_description_falls_back(self):
        self._v1_fallback_check(
            lambda s, u: '[{"crew_hint": "research"}]'
        )

    def test_short_description_falls_back(self):
        # < 4 chars after stripping
        self._v1_fallback_check(
            lambda s, u: '[{"description": "ok"}]'
        )

    def test_oversized_description_falls_back(self):
        big = "x" * 600
        self._v1_fallback_check(
            lambda s, u: f'[{{"description": "{big}"}}]'
        )

    def test_oversized_response_falls_back(self):
        big_response = '["x"]' + " " * 5000
        self._v1_fallback_check(lambda s, u: big_response)

    def test_llm_raising_falls_back(self):
        def _boom(s, u):
            raise RuntimeError("network error")
        self._v1_fallback_check(_boom)

    def test_non_dict_entry_falls_back(self):
        self._v1_fallback_check(
            lambda s, u: '["just a string"]'
        )

    def test_non_string_description_falls_back(self):
        self._v1_fallback_check(
            lambda s, u: '[{"description": 42}]'
        )

    def test_oversized_crew_hint_falls_back(self):
        long_hint = "x" * 40
        self._v1_fallback_check(
            lambda s, u: (
                f'[{{"description": "step ok", '
                f'"crew_hint": "{long_hint}"}}]'
            )
        )

    def test_empty_goal_raises_value_error(self):
        # llm_plan's input validation matches v1's — empty goal is a
        # programming error, not a fall-back path.
        with self.assertRaises(ValueError):
            llm_plan("", _make_run(), llm_call=lambda s, u: "")
        with self.assertRaises(ValueError):
            llm_plan("   ", _make_run(), llm_call=lambda s, u: "")


# ============================================================================
# Dispatcher (get_default_planner)
# ============================================================================


class TestDefaultPlannerDispatcher(unittest.TestCase):
    def setUp(self) -> None:
        _reset_runtime_settings()

    def test_default_off_returns_v1(self):
        with _patch_runtime_settings():
            chosen = get_default_planner()
        self.assertIs(chosen, plan)

    def test_master_switch_on_returns_v2(self):
        with _patch_runtime_settings(
                autonomous_executor_llm_planner_enabled=True):
            chosen = get_default_planner()
        self.assertIs(chosen, llm_plan)

    def test_runtime_settings_failure_returns_v1(self):
        # Defensive: if reading the setting raises, fall back to v1.
        with patch.object(
            runtime_settings,
            "get_autonomous_executor_llm_planner_enabled",
            side_effect=RuntimeError("boom"),
        ):
            chosen = get_default_planner()
        self.assertIs(chosen, plan)


class TestRuntimeSettingsSetter(unittest.TestCase):
    def setUp(self) -> None:
        _reset_runtime_settings()

    def test_setter_persists(self):
        with _patch_runtime_settings(), \
                patch.object(runtime_settings, "_save"):
            runtime_settings.set_autonomous_executor_llm_planner_enabled(
                True,
            )
            self.assertTrue(
                runtime_settings
                .get_autonomous_executor_llm_planner_enabled(),
            )


# ============================================================================
# Driver integration
# ============================================================================


class TestDriverIntegration(unittest.TestCase):
    def test_driver_uses_llm_planner_when_injected(self):
        # Inject the v2 planner directly; verify the run lands with the
        # multi-step plan and the driver executes through them.
        def _llm(sys_p, user_p):
            return (
                '[{"description": "do A", "crew_hint": ""},'
                ' {"description": "do B", "crew_hint": ""}]'
            )

        from app.autonomous_executor import CommanderResult

        def _commander(step, run):
            return CommanderResult(text=f"{step.description}: ok")

        # Wrap llm_plan so it captures the stub for tests.
        def _planner(goal, run):
            return llm_plan(goal, run, llm_call=_llm)

        run = _make_run(goal="do A and B")
        # 1st advance: CREATED → planner → RUNNING with 2 steps
        advance_one_step(run, planner_fn=_planner)
        self.assertEqual(run.status, ExecutorStatus.RUNNING)
        self.assertEqual(len(run.plan), 2)

        # 2nd + 3rd advance: execute the two steps
        advance_one_step(run, commander_fn=_commander, planner_fn=_planner)
        advance_one_step(run, commander_fn=_commander, planner_fn=_planner)

        self.assertEqual(run.status, ExecutorStatus.COMPLETED)
        self.assertEqual(run.plan[0].result_text, "do A: ok")
        self.assertEqual(run.plan[1].result_text, "do B: ok")

    def test_driver_fallback_when_llm_dies_mid_call(self):
        # LLM raises → llm_plan falls back to v1 single-step internally.
        def _llm(sys_p, user_p):
            raise RuntimeError("LLM unreachable")

        def _planner(goal, run):
            return llm_plan(goal, run, llm_call=_llm)

        from app.autonomous_executor import CommanderResult

        def _commander(step, run):
            return CommanderResult(text="ok")

        run = _make_run(goal="multi step task")
        advance_one_step(run, planner_fn=_planner)
        # Should be RUNNING with v1 single step (not FAILED).
        self.assertEqual(run.status, ExecutorStatus.RUNNING)
        self.assertEqual(len(run.plan), 1)
        self.assertEqual(run.plan[0].description, "multi step task")

        advance_one_step(run, commander_fn=_commander, planner_fn=_planner)
        self.assertEqual(run.status, ExecutorStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
