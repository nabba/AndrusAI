"""Driver — advance an ExecutorRun by one logical unit of work.

The driver is the **state-machine engine** for the autonomous executor.
It consumes the foundation shipped in Phase 2 piece 1 (models, store,
budget) and the planner shipped alongside this module.

Design choices:

* **Single function, multiple state pathways.** ``advance_one_step``
  inspects the run's current ``status`` and dispatches to the right
  handler. The function is idempotent given terminal-state input —
  callers can re-invoke safely.

* **Step execution is injectable** via ``commander_fn``. The driver
  never imports the Commander orchestrator directly — Phase 2 piece 2b
  will provide a thin wrapper that calls ``Commander.handle()`` and
  reports back cost + tokens. v1 ships with the seam open; tests inject
  a mock.

* **Failure isolation.** A step that fails does NOT fail the whole run
  immediately. The driver marks the step ``FAILED`` and moves on. After
  all steps are processed, the run transitions to:

    * ``COMPLETED`` if every step is ``COMPLETED`` or ``SKIPPED``.
    * ``FAILED`` if any step is ``FAILED``.
    * ``BUDGET_EXHAUSTED`` if budget ran out before all pending steps
      were processed.

  This rule lets a multi-step plan partially succeed without losing
  the work — operators get a clear picture of which steps completed.

* **Re-entrant safety.** ``advance_one_step`` mutates the run in-place
  but always returns it. Caller is responsible for ``store.save(run)``.
  Concurrent calls on the same run are NOT safe — caller must serialise
  via a per-run lock (the idle-scheduler integration in Phase 2 piece 2b
  will use the scheduler's existing job-lock primitive).

The driver does NOT integrate with the idle scheduler in this chunk.
That wiring (one job tuple in ``app.idle_scheduler._default_jobs``)
ships in Phase 2 piece 2b alongside the Commander adapter and the
``/delegate`` slash command.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from app.autonomous_executor.models import (
    ExecutorRun,
    ExecutorStatus,
    ExecutorStep,
    StepStatus,
)
from app.autonomous_executor.planner import plan as default_plan

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommanderResult:
    """Result of a single step dispatch.

    ``text`` is the user-visible answer the Commander produced.
    ``cost_usd`` + ``tokens_used`` are the resource consumption reported
    by the LLM call(s) inside the dispatch. The driver feeds these into
    ``run.budget.consume``.
    """

    text: str
    cost_usd: float = 0.0
    tokens_used: int = 0


# Type alias for the injectable commander adapter.
CommanderFn = Callable[[ExecutorStep, ExecutorRun], CommanderResult]

# Type alias for the injectable planner. Matches ``planner.plan``.
PlannerFn = Callable[[str, ExecutorRun], list[ExecutorStep]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def advance_one_step(
    run: ExecutorRun,
    *,
    commander_fn: Optional[CommanderFn] = None,
    planner_fn: Optional[PlannerFn] = None,
) -> ExecutorRun:
    """Advance ``run`` by one logical unit of work. See module docstring.

    Parameters
    ----------
    run
        The run to advance. Mutated in-place. Caller persists via
        ``store.save(run)`` after the call returns.

    commander_fn
        Injectable adapter that executes one ExecutorStep. Returns a
        :class:`CommanderResult`. If ``None`` and the run has work to
        do, the call raises ``RuntimeError`` (a missing commander_fn
        is a programming error, not a runtime condition).

    planner_fn
        Injectable planner. Defaults to :func:`app.autonomous_executor.planner.plan`.

    Returns
    -------
    ExecutorRun
        The same run object (mutated). Returned for chaining
        convenience and to make the function easy to spot in stack
        traces.
    """
    # Terminal: no-op. Idempotent — safe to call on completed runs.
    if run.is_terminal:
        return run

    # ── CREATED → PLANNING → RUNNING in one call ────────────────────
    # Planning is synchronous (deterministic in v1, single LLM call in
    # v2), so we collapse the two transitions into one driver step.
    if run.status is ExecutorStatus.CREATED:
        run.transition(ExecutorStatus.PLANNING)
        return _handle_planning(run, planner_fn or default_plan)

    # ── PLANNING → RUNNING (drive the planner if no plan yet) ───────
    # Defensive — recovers from a crash that left a run in PLANNING
    # without any steps; also handles callers who manually transition
    # to PLANNING before invoking advance_one_step.
    if run.status is ExecutorStatus.PLANNING:
        return _handle_planning(run, planner_fn or default_plan)

    # ── RUNNING: execute next pending step (or finalise) ────────────
    if run.status is ExecutorStatus.RUNNING:
        if commander_fn is None:
            raise RuntimeError(
                "advance_one_step: commander_fn is required when run "
                f"is RUNNING (run_id={run.run_id!r})",
            )
        return _handle_running(run, commander_fn)

    # ── BLOCKED / PAUSED: caller must resume explicitly ─────────────
    # The driver does NOT auto-resume — operator decides when to
    # transition back to RUNNING.
    return run


# ── State handlers ──────────────────────────────────────────────────


def _handle_planning(run: ExecutorRun, planner_fn: PlannerFn) -> ExecutorRun:
    """PLANNING: invoke planner (if no plan yet) and transition to
    RUNNING. On planner error → FAILED with diagnostic reason."""
    # Plan is already present (e.g. operator pre-populated it before
    # invoking advance_one_step) → straight to RUNNING.
    if run.plan:
        run.transition(ExecutorStatus.RUNNING)
        return run

    try:
        steps = planner_fn(run.goal, run)
    except Exception as exc:
        logger.debug(
            "autonomous_executor: planner failed: %s", exc, exc_info=True,
        )
        run.transition(
            ExecutorStatus.FAILED,
            reason=f"planner failed: {exc!r}",
        )
        return run

    if not steps:
        run.transition(
            ExecutorStatus.FAILED,
            reason="planner returned an empty plan",
        )
        return run

    # Add each step via add_step (which assigns deterministic IDs).
    # The planner-supplied step_ids are intentionally overridden so the
    # ID space is owned by the run, not the planner.
    for step in steps:
        run.add_step(
            description=step.description,
            crew_hint=step.crew_hint,
        )

    run.transition(ExecutorStatus.RUNNING)
    return run


def _handle_running(
    run: ExecutorRun,
    commander_fn: CommanderFn,
) -> ExecutorRun:
    """RUNNING: pick next PENDING step (or finalise if none left).

    Outcomes:
      1. PENDING step exists + budget OK → execute it, consume budget,
         then check budget again. Budget-exhausted check tips the
         run into BUDGET_EXHAUSTED if pending steps remain.
      2. No PENDING step → finalise the run (COMPLETED / FAILED based
         on per-step outcomes).
    """
    pending = _next_pending_step(run)

    # No pending step: finalise the run.
    if pending is None:
        return _finalise(run)

    # Budget pre-check — refuse to start a step if we're already
    # exhausted. The driver doesn't try to "fit" a partial step;
    # budget.is_exhausted treats elapsed wall-clock as terminal.
    if run.budget.is_exhausted():
        run.transition(
            ExecutorStatus.BUDGET_EXHAUSTED,
            reason=f"budget exhausted before step {pending.step_id!r}",
        )
        return run

    # Execute the step. Failures are isolated to the step — the run
    # continues until all steps are processed (or budget runs out).
    _execute_step(run, pending, commander_fn)

    # Verified Implementation Plan Gap #2 (2026-05-22) — blocker
    # detection. Two trigger conditions:
    #   (a) Commander text starts with ``BLOCKED:`` — explicit marker
    #       the Commander can emit when it needs operator input it
    #       can't synthesize on its own (missing creds, ambiguous
    #       request, manual confirmation required).
    #   (b) ``_BLOCKER_FAILURE_THRESHOLD`` consecutive failures on the
    #       same step description — the executor is provably stuck.
    blocker_reason = _detect_blocker(run, pending)
    if blocker_reason:
        run.transition(
            ExecutorStatus.BLOCKED, reason=blocker_reason,
        )
        return run

    # Post-step decisions, in priority order:
    #   1. Budget exhausted + steps remain → BUDGET_EXHAUSTED.
    #   2. No pending steps left → finalise to COMPLETED / FAILED.
    #   3. Otherwise stay in RUNNING for the next advance call.
    next_pending = _next_pending_step(run)
    if run.budget.is_exhausted() and next_pending is not None:
        run.transition(
            ExecutorStatus.BUDGET_EXHAUSTED,
            reason=(
                f"budget exhausted after step {pending.step_id!r}; "
                f"{_count_pending(run)} step(s) skipped"
            ),
        )
        return run

    if next_pending is None:
        # All steps processed (with or without failures) — finalise.
        return _finalise(run)

    return run


# ── Blocker detection ────────────────────────────────────────────────


# Repeated failures on the SAME description across N attempts → BLOCKED.
# Threshold chosen so transient flakes don't trip the gate but a
# genuinely stuck executor surfaces to the operator within a few ticks.
_BLOCKER_FAILURE_THRESHOLD = 3

# Markers the Commander (or an agent invoked through it) can emit to
# signal "I need operator input to make progress." Case-insensitive
# prefix-match against the trimmed result text.
_BLOCKER_MARKERS = (
    "BLOCKED:",
    "NEEDS_OPERATOR_INPUT:",
    "NEEDS_OPERATOR:",
    "AWAITING_OPERATOR:",
)


def _detect_blocker(
    run: "ExecutorRun", just_ran_step: "ExecutorStep",
) -> str:
    """Return a human-readable reason when the run should transition
    to BLOCKED, or an empty string when execution should continue.

    Two detection paths, in priority order:

      1. **Explicit Commander marker.** If the last step's
         ``result_text`` starts with one of ``_BLOCKER_MARKERS``
         (case-insensitive), the agent is asking the operator to
         intervene. The marker prefix is stripped from the returned
         reason — only the body becomes the blocked_reason.

      2. **Repeated failure on same description.** If the last
         ``_BLOCKER_FAILURE_THRESHOLD`` step attempts share the same
         normalised description AND all failed, the executor is
         provably stuck. Return a diagnostic reason naming the
         failure count + description.

    Returns ``""`` when neither condition fires — caller continues
    with the normal post-step routing.
    """
    # Path 1: explicit marker
    text = (just_ran_step.result_text or "").lstrip()
    upper = text.upper()
    for marker in _BLOCKER_MARKERS:
        if upper.startswith(marker):
            # Strip the marker + any whitespace + colon-and-space
            body = text[len(marker):].lstrip(": \t\n")
            return body[:200] if body else f"agent emitted {marker}"

    # Path 2: repeated failure
    if just_ran_step.status is not StepStatus.FAILED:
        return ""
    description = (just_ran_step.description or "").strip().lower()
    if not description:
        return ""
    # Walk the plan from newest backwards; count consecutive
    # SAME-description FAILED steps including the one that just ran.
    count = 0
    for step in reversed(run.plan):
        if step.status is not StepStatus.FAILED:
            break
        if (step.description or "").strip().lower() != description:
            break
        count += 1
        if count >= _BLOCKER_FAILURE_THRESHOLD:
            break
    if count >= _BLOCKER_FAILURE_THRESHOLD:
        return (
            f"{count} consecutive failures on "
            f"{description[:120]!r}: "
            f"{(just_ran_step.failure_reason or '')[:120]}"
        )
    return ""


def _execute_step(
    run: ExecutorRun,
    step: ExecutorStep,
    commander_fn: CommanderFn,
) -> None:
    """Call commander_fn for one step. Mutates the step in-place. The
    run's budget is updated with the reported cost regardless of step
    outcome — partial-success failures still count for budget."""
    step.status = StepStatus.RUNNING
    step.started_at = _now_iso()

    try:
        result = commander_fn(step, run)
    except Exception as exc:
        logger.debug(
            "autonomous_executor: step %s commander_fn raised: %s",
            step.step_id, exc, exc_info=True,
        )
        step.status = StepStatus.FAILED
        step.failure_reason = f"{type(exc).__name__}: {exc}"
        step.ended_at = _now_iso()
        return

    if not isinstance(result, CommanderResult):
        # Defensive — caller bug. Mark the step failed with a clear
        # diagnostic so the post-mortem trail is honest.
        step.status = StepStatus.FAILED
        step.failure_reason = (
            f"commander_fn returned {type(result).__name__!r}; "
            "expected CommanderResult"
        )
        step.ended_at = _now_iso()
        return

    step.result_text = result.text
    step.cost_usd = float(result.cost_usd)
    step.tokens_used = int(result.tokens_used)
    step.status = StepStatus.COMPLETED
    step.ended_at = _now_iso()

    # Charge the run-level budget. Defensive against negative cost
    # values (commander_fn should never produce them, but cost.consume
    # validates).
    try:
        run.budget.consume(
            usd=max(0.0, float(result.cost_usd)),
            tokens=max(0, int(result.tokens_used)),
        )
    except Exception as exc:
        logger.debug(
            "autonomous_executor: budget.consume failed: %s", exc,
            exc_info=True,
        )

    # Phase A.2 closure (2026-05-22) — CR observability.
    # After the step completes, scan the change-request store for
    # entries this step produced (requestor matches executor:<run>:
    # prefix AND created_at falls in step's time window). Populates
    # step.cr_ids for operator visibility. Failure-isolated.
    try:
        from app.autonomous_executor.coding_session_bridge import (
            attribute_crs_to_step,
        )
        cr_ids = attribute_crs_to_step(
            run_id=run.run_id,
            step_started_at=step.started_at,
            step_ended_at=step.ended_at,
        )
        if cr_ids:
            step.cr_ids = list(cr_ids)
    except Exception as exc:
        logger.debug(
            "autonomous_executor: CR attribution failed: %s",
            exc, exc_info=True,
        )


def _finalise(run: ExecutorRun) -> ExecutorRun:
    """No more pending steps — pick a terminal outcome based on the
    per-step results.

    Rule:
      * Any FAILED step → run.FAILED with a summary of failures.
      * All COMPLETED (or SKIPPED) → run.COMPLETED.
      * Empty plan with no completed steps shouldn't reach here
        (planner-error path goes straight to FAILED above), but
        handle defensively → FAILED.

    Phase 2 piece 2h (2026-05-20): after the run reaches a terminal
    state, the coding-session bridge cleans up any worktrees the
    executor spawned. Best-effort; never blocks finalisation.
    """
    if not run.plan:
        run.transition(
            ExecutorStatus.FAILED,
            reason="no plan to finalise (defensive — should not occur)",
        )
        _cleanup_bridge_sessions(run)
        return run

    failed = [s for s in run.plan if s.status is StepStatus.FAILED]
    if failed:
        details = ", ".join(
            f"{s.step_id}:{s.failure_reason[:60]}"
            for s in failed[:3]
        )
        reason = f"{len(failed)} step(s) failed: {details}"
        if len(failed) > 3:
            reason += f" (and {len(failed) - 3} more)"
        run.transition(ExecutorStatus.FAILED, reason=reason)
        _cleanup_bridge_sessions(run)
        return run

    # All non-failed → COMPLETED.
    run.transition(ExecutorStatus.COMPLETED)
    _cleanup_bridge_sessions(run)
    return run


def _cleanup_bridge_sessions(run: ExecutorRun) -> None:
    """Best-effort: discard any executor-tagged coding sessions when
    the run reaches a terminal state. Never raises."""
    try:
        from app.autonomous_executor.coding_session_bridge import (
            cleanup_sessions_for_run,
        )
        cleanup_sessions_for_run(run.run_id)
    except Exception:
        logger.debug(
            "autonomous_executor: bridge cleanup failed for run %s",
            run.run_id, exc_info=True,
        )


def _next_pending_step(run: ExecutorRun) -> Optional[ExecutorStep]:
    """First step still in PENDING. Returns ``None`` when every step
    has been processed (regardless of outcome)."""
    for step in run.plan:
        if step.status is StepStatus.PENDING:
            return step
    return None


def _count_pending(run: ExecutorRun) -> int:
    return sum(1 for s in run.plan if s.status is StepStatus.PENDING)
