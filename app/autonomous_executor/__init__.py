"""Autonomous executor — `/delegate <goal>` foundation.

v1 (Phase 2 piece 1, 2026-05-20) ships only the data structures and
the JSON-per-record store. No production callers yet:

  * idle_scheduler integration (`HEAVY` job) — Phase 2 piece 2
  * Signal `/delegate` slash command — Phase 2 piece 2
  * REST `/api/cp/delegate` endpoints — Phase 2 piece 2
  * React `/cp/delegate` page — Phase 2 piece 2
  * Planner + driver (the loop that calls Commander) — Phase 2 piece 2

This module is the **data foundation** every downstream piece consumes.
It is intentionally narrow so the typed state machine, budget tracker,
and persistence layer can be reviewed in isolation before any
behavioural code lands.

The executor composes with — does not replace — existing primitives:

  * **Threads** (`app/threads/`) for long-horizon questions the operator
    is consulting on.
  * **Workflows** (`app/workflows/`) for deterministic DAGs of
    registered-tool calls.
  * **Commander** (`app/agents/commander/orchestrator.py`) for the
    actual LLM dispatch — the executor calls Commander as a library
    via ``Commander.handle()``.

Master switch: ``app.runtime_settings.get_autonomous_executor_enabled``
(default False — the module is a pure library until Phase 2 piece 2
wires the driver).
"""
from __future__ import annotations

from app.autonomous_executor.models import (
    Budget,
    ExecutorRun,
    ExecutorStatus,
    ExecutorStep,
    InvalidExecutorTransition,
    StepStatus,
    TERMINAL_STATUSES,
    assert_can_transition,
)
from app.autonomous_executor.commander_adapter import (
    default_commander_provider,
    make_commander_adapter,
)
from app.autonomous_executor.driver import (
    CommanderFn,
    CommanderResult,
    PlannerFn,
    advance_one_step,
)
from app.autonomous_executor.planner import get_default_planner, plan
from app.autonomous_executor.planner_llm import llm_plan
from app.autonomous_executor.scheduler_job import run_executor_tick
from app.autonomous_executor import store

__all__ = [
    "Budget",
    "CommanderFn",
    "CommanderResult",
    "ExecutorRun",
    "ExecutorStatus",
    "ExecutorStep",
    "InvalidExecutorTransition",
    "PlannerFn",
    "StepStatus",
    "TERMINAL_STATUSES",
    "advance_one_step",
    "assert_can_transition",
    "default_commander_provider",
    "get_default_planner",
    "llm_plan",
    "make_commander_adapter",
    "plan",
    "run_executor_tick",
    "store",
]
