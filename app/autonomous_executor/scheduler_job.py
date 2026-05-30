"""Scheduler tick — one call into the driver per idle invocation.

Phase 2 piece 2b, 2026-05-20.

Plugs the autonomous executor into ``app.idle_scheduler`` as a single
HEAVY-weight job. Each invocation:

  1. Bails out instantly if the master switch is OFF
     (``runtime_settings.get_autonomous_executor_enabled``).
  2. Picks ONE active run from ``store.list_active`` (most recently
     touched first — same ordering as the threads + workflows stores).
  3. Calls ``advance_one_step`` with the Commander adapter.
  4. Persists the mutated run via ``store.save``.
  5. Logs the outcome for the operator-facing audit trail.

Single-tick semantics
─────────────────────
One ``advance_one_step`` call per scheduler tick. The driver is
re-entrant, so the next tick picks up where this one left off. This
matches the "cooperative interruption" pattern the scheduler already
uses — short ticks, easy to yield.

Concurrency
───────────
The scheduler serialises HEAVY jobs (one at a time, post-idle window),
so concurrent execution of the same run is structurally impossible
under the scheduler's lock. If a future caller invokes
``run_executor_tick`` outside the scheduler, they MUST add their own
per-run lock — this module does not provide one.

Failure isolation
─────────────────
Every layer is wrapped: a broken adapter doesn't crash the scheduler,
a broken planner doesn't crash the adapter, a broken store doesn't
crash the driver. Exceptions surface in the logs; the run's failure
reason is populated by the driver.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.autonomous_executor import store
from app.autonomous_executor.commander_adapter import make_commander_adapter
from app.autonomous_executor.driver import (
    CommanderFn,
    PlannerFn,
    advance_one_step,
)
from app.autonomous_executor.models import ExecutorRun, ExecutorStatus
from app.autonomous_executor.planner import get_default_planner

logger = logging.getLogger(__name__)


def _is_enabled() -> bool:
    """Read the master switch via runtime_settings. Defensive: any
    failure (settings file missing, import error) returns False so
    the executor stays dormant on uncertainty."""
    try:
        from app.runtime_settings import get_autonomous_executor_enabled
        return get_autonomous_executor_enabled()
    except Exception:
        logger.debug(
            "autonomous_executor: runtime_settings unavailable; "
            "treating as disabled",
            exc_info=True,
        )
        return False


def _pick_run() -> Optional[ExecutorRun]:
    """Highest-priority *advanceable* active run. v1 picks the most
    recently touched — same ordering as threads + workflows.

    PENDING_APPROVAL runs are active (so the dashboard still shows them
    as awaiting approval) but are skipped here: the opt-in gate means a
    run never executes until the operator approves it (👍 → CREATED).
    This is the load-bearing line — never advance a run the operator
    hasn't approved.

    Future v2 may add a priority field (operator-set urgency, age
    pressure, budget headroom) — drop-in change here.
    """
    try:
        active = store.list_active(limit=50)
    except Exception:
        logger.debug(
            "autonomous_executor: store.list_active failed",
            exc_info=True,
        )
        return None
    for run in active:
        if run.status is ExecutorStatus.PENDING_APPROVAL:
            continue
        return run
    return None


def run_executor_tick(
    *,
    commander_fn: Optional[CommanderFn] = None,
    planner_fn: Optional[PlannerFn] = None,
) -> Optional[str]:
    """One scheduler invocation. Returns the run_id that advanced, or
    ``None`` if nothing happened (master switch off, no active runs,
    or persistent error).

    The ``commander_fn`` + ``planner_fn`` parameters exist for tests —
    production callers omit them and the adapter + default-planner
    dispatcher wire the real implementations.
    """
    if not _is_enabled():
        return None

    run = _pick_run()
    if run is None:
        return None

    # Self-improvement runs (a JSON job in the step description) are dispatched
    # deterministically through the verified mutation engine; every other run
    # uses the normal Commander adapter. Falls back safely if the orchestrator
    # import is unavailable, so the executor never breaks on this routing.
    if commander_fn is not None:
        adapter: CommanderFn = commander_fn
    else:
        try:
            from app.self_improvement.orchestrator import make_self_improvement_adapter

            adapter = make_self_improvement_adapter()
        except Exception:
            adapter = make_commander_adapter()
    chosen_planner: PlannerFn = planner_fn or get_default_planner()

    try:
        advance_one_step(
            run,
            commander_fn=adapter,
            planner_fn=chosen_planner,
        )
    except Exception:
        logger.exception(
            "autonomous_executor: advance_one_step crashed for run %s",
            run.run_id,
        )
        # Don't crash the scheduler — log + persist the run as-is so
        # the operator-facing surface shows the last-known state.
        try:
            store.save(run)
        except Exception:
            logger.exception(
                "autonomous_executor: failed to persist run %s after "
                "advance crash", run.run_id,
            )
        return run.run_id

    try:
        store.save(run)
    except Exception:
        logger.exception(
            "autonomous_executor: failed to persist run %s after "
            "advance_one_step", run.run_id,
        )
        return run.run_id

    logger.info(
        "autonomous_executor: tick advanced run %s → status=%s "
        "(steps_completed=%d/%d, spent=$%.4f/%.4f)",
        run.run_id,
        run.status.value,
        sum(1 for s in run.plan if s.status.value == "completed"),
        len(run.plan),
        run.budget.spent_usd,
        run.budget.cap_usd,
    )
    return run.run_id
