"""Planner — turn a goal string into a sequence of executable steps.

v1 (Phase 2 piece 2a, 2026-05-20) ships a **deterministic single-step
planner**: every goal becomes exactly one ExecutorStep whose
description IS the goal. This is the safest possible plan and lets the
driver loop be tested end-to-end without depending on an LLM call.

v2 will add:

  * LLM-based decomposition (Anthropic Haiku 4.5) when goal complexity
    warrants 2-5 sub-goals — gated by an additional master switch so
    operators can stay on v1 while v2 soaks.
  * crew_hint extraction from goal text ("research X" → ``crew_hint="research"``).
  * Detection of trivially-single-step goals (no LLM call needed).
  * Re-planning when a step fails (driver-side hook).

The signature is stable across versions so v2 is a drop-in:

    def plan(goal: str, run: ExecutorRun) -> list[ExecutorStep]:

The ``run`` parameter is included so v2 can look at existing budget /
status / zone when deciding how many sub-goals to produce. v1 ignores it.

Returns
-------
list[ExecutorStep]
    Non-empty list of steps. Each step has ``status=PENDING``.

Raises
------
ValueError
    If goal is empty after stripping.
"""
from __future__ import annotations

from app.autonomous_executor.models import (
    ExecutorRun,
    ExecutorStep,
    StepStatus,
)


def plan(goal: str, run: ExecutorRun) -> list[ExecutorStep]:
    """Deterministic single-step planner. See module docstring."""
    if not isinstance(goal, str):
        raise ValueError("plan: goal must be a string")
    stripped = goal.strip()
    if not stripped:
        raise ValueError("plan: goal cannot be empty")
    return [
        ExecutorStep(
            step_id="step-001",
            description=stripped,
            crew_hint="",
            status=StepStatus.PENDING,
        ),
    ]


def get_default_planner():
    """Return the planner to use for production runs.

    Consults ``runtime_settings.autonomous_executor_llm_planner_enabled``
    (default False — v1 deterministic stays on). When True, returns
    ``llm_plan`` which calls Claude Haiku 4.5 to decompose the goal.

    Defensive: any failure reading the setting returns v1. Lets
    scheduler_job.run_executor_tick wire a single seam without each
    caller re-implementing the decision.
    """
    try:
        from app.runtime_settings import (
            get_autonomous_executor_llm_planner_enabled,
        )
        if get_autonomous_executor_llm_planner_enabled():
            from app.autonomous_executor.planner_llm import llm_plan
            return llm_plan
    except Exception:
        # runtime_settings unavailable / planner_llm import broken —
        # fall through to v1.
        pass
    return plan
