"""Agent-callable ``delegate_goal`` tool (Verified Plan Gap I closure,
2026-05-22).

The plan called for ``tools/delegate_tool.py`` — the agent-side
counterpart of the Signal ``/delegate`` command + the React
``/cp/delegate`` page. Without this, only humans can delegate; an
internal agent that detects "this is a long-running multi-step goal
better suited to the autonomous executor" had no path to escalate.

Surface
───────

  * :func:`delegate_goal(goal, *, budget_usd, requestor)` creates a
    new :class:`ExecutorRun` in CREATED state and returns its
    ``run_id``. The idle-scheduler tick picks it up on the next
    cadence (default ~30 min, or earlier if the executor's HEAVY
    cadence is shorter).

  * The CrewAI ``@tool``-decorated wrapper :func:`delegate_goal_tool`
    is the agent-facing surface. Returns a single-line string the
    agent can include in its reply.

Failure-isolated end-to-end — bad inputs / store errors return a
clear error string rather than raising.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# CrewAI's ``tool`` decorator is what the agents see; lazy-import so
# this module is testable without the heavy crewai bootstrap.
try:
    from crewai.tools import tool
except Exception:  # pragma: no cover — stripped test env
    def tool(name: str):
        def _decorator(fn):
            return fn
        return _decorator


# Hard caps — match the REST endpoint's defaults so the agent surface
# can't escape the operator-set ceilings.
_DEFAULT_BUDGET_USD = 5.0
_MAX_BUDGET_USD = 10.0
_DEFAULT_REQUESTOR = "agent"
_MAX_GOAL_LENGTH = 1000


def delegate_goal(
    goal: str,
    *,
    budget_usd: float = _DEFAULT_BUDGET_USD,
    requestor: str = _DEFAULT_REQUESTOR,
) -> dict:
    """Create a new ``ExecutorRun`` in CREATED state. Returns a dict.

    Parameters
    ----------
    goal
        The high-level goal to delegate. The planner will decompose
        into steps. Capped at 1000 chars.
    budget_usd
        Per-run USD ceiling. Defaults to $5.00, clamped to $10.00 max.
    requestor
        Caller identifier — typically the agent's id. Surfaces in
        the run's audit trail.

    Returns
    -------
    dict
        ``{"ok": bool, "run_id": str, "error": str}``. On success,
        ``run_id`` is non-empty; on refusal, ``error`` explains.
    """
    g = (goal or "").strip()
    if not g:
        return {
            "ok": False, "run_id": "",
            "error": "goal cannot be empty",
        }
    if len(g) > _MAX_GOAL_LENGTH:
        return {
            "ok": False, "run_id": "",
            "error": f"goal exceeds {_MAX_GOAL_LENGTH}-char cap",
        }
    try:
        b = float(budget_usd)
    except (TypeError, ValueError):
        return {
            "ok": False, "run_id": "",
            "error": "budget_usd must be a number",
        }
    if b <= 0:
        return {
            "ok": False, "run_id": "",
            "error": "budget_usd must be positive",
        }
    b = min(b, _MAX_BUDGET_USD)
    req = (requestor or _DEFAULT_REQUESTOR).strip()[:80]

    try:
        import importlib
        import uuid
        # importlib re-resolves via sys.modules each call — robust
        # against test fixtures that stuff fakes into the package's
        # attribute slot (the ``from X import Y`` form binds stale
        # attributes from import time).
        store = importlib.import_module(
            "app.autonomous_executor.store",
        )
        models_mod = importlib.import_module(
            "app.autonomous_executor.models",
        )
        Budget = models_mod.Budget
        ExecutorRun = models_mod.ExecutorRun
        ExecutorStatus = models_mod.ExecutorStatus
    except Exception as exc:
        return {
            "ok": False, "run_id": "",
            "error": f"executor modules unavailable: {exc}",
        }

    run_id = f"run-{uuid.uuid4().hex[:12]}"
    run = ExecutorRun(
        run_id=run_id,
        goal=g,
        requestor=req,
        status=ExecutorStatus.CREATED,
        budget=Budget(cap_usd=b),
    )
    try:
        store.save(run)
    except Exception as exc:
        logger.debug(
            "delegate_goal: store.save failed for %s: %s",
            run_id, exc, exc_info=True,
        )
        return {
            "ok": False, "run_id": "",
            "error": f"persistence failed: {type(exc).__name__}: {exc}",
        }

    # Emit the run_created audit row for the fourth chain
    try:
        from app.autonomous_executor import audit
        audit.record(
            run_id=run_id, kind="run_created",
            actor=f"agent:{req}",
            payload={
                "goal_preview": g[:140],
                "budget_usd": b,
            },
        )
    except Exception:
        logger.debug(
            "delegate_goal: audit emission failed", exc_info=True,
        )

    # RPT-1 producer (2026-05-23 audit follow-up) — register a forecast
    # "this executor run will reach COMPLETED (vs FAILED / BUDGET_
    # EXHAUSTED / ABORTED)". Resolution window 7 days — long enough
    # for multi-step plans, short enough to catch stuck runs.
    try:
        from datetime import datetime, timedelta, timezone
        from app.sentience_experiments.rpt1_self_calibration import (
            register_prediction,
        )
        register_prediction(
            claim_kind="executor_run_success",
            claim_text=(
                f"executor run {run_id[:12]} (goal preview: {g[:80]}) "
                f"by {req} will COMPLETE"
            ),
            predicted_p=0.5,
            resolution_at=datetime.now(timezone.utc) + timedelta(days=7),
            scorer_ref="executor_run_success",
            scorer_args={"run_id": run_id},
        )
    except Exception:
        logger.debug(
            "delegate_goal: RPT-1 forecast registration failed",
            exc_info=True,
        )

    return {
        "ok": True, "run_id": run_id, "error": "",
    }


@tool("delegate_goal")
def delegate_goal_tool(
    goal: str,
    budget_usd: float = _DEFAULT_BUDGET_USD,
    requestor: str = _DEFAULT_REQUESTOR,
) -> str:
    """
    Delegate a multi-step goal to the autonomous executor.

    The executor will plan the goal into steps, drive each step via
    the Commander, and report progress through /cp/delegate. Use
    this when:

      * The goal has clear sub-steps (research → analyse → write).
      * You expect it to take more than 2-3 minutes of work.
      * You don't need the result inline — it can complete
        asynchronously.

    Args:
      goal: The high-level goal description (max 1000 chars).
      budget_usd: USD ceiling for the run (default $5, max $10).
      requestor: Caller identifier (default 'agent').

    Returns:
      One-line summary. On success: 'Delegated run <run_id>; budget
      $X.XX. Track at /cp/delegate.' On refusal: 'Delegation refused:
      <reason>'.
    """
    result = delegate_goal(goal, budget_usd=budget_usd, requestor=requestor)
    if not result["ok"]:
        return f"Delegation refused: {result['error']}"
    return (
        f"Delegated run {result['run_id']}; budget "
        f"${min(float(budget_usd or _DEFAULT_BUDGET_USD), _MAX_BUDGET_USD):.2f}. "
        f"Track at /cp/delegate/{result['run_id']}."
    )


__all__ = ["delegate_goal", "delegate_goal_tool"]
