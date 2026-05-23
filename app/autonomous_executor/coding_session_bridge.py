"""Executor ↔ coding-session bridge (Phase 2 piece 2h, 2026-05-20).

Ties the autonomous executor (``app.autonomous_executor``) to the
coding-session subsystem (``app.coding_session``) so executor-spawned
sessions:

  1. **Tag themselves** with the run_id (``agent_id="executor:<run>:coder"``).
  2. **Default to durable=True** so the reconciler doesn't expire them
     mid-run when a step takes minutes.
  3. **Get cleaned up** on the executor run's terminal transition so
     no worktree leaks past run lifetime.

The mechanism is a single ContextVar (``_executor_run_id``) plus three
public helpers. The Commander adapter (Phase 2 piece 2b) sets it for
the duration of one Commander.handle call; the
``coding_session_start`` tool wrapper reads it; the driver's
``_finalise`` invokes the cleanup.

Design choices:

* **ContextVar, not module global.** Concurrent runs on the same
  process (future expansion) need isolated executor identities.
  ContextVar gives that for free.

* **Cleanup is force-discard, not submit.** Sessions reaching the
  bridge's cleanup hook haven't been submitted by the agent — that
  signals either an abort (operator pulled the plug) or a failure
  (the agent never made it to ``coding_session_submit``). Either
  way, the worktree is throwaway. Submission is the operator-gated
  escape hatch and is always explicit.

* **Defensive in all directions.** A broken executor context never
  crashes ``coding_session_start``; a broken cleanup never crashes
  the executor's terminal transition.
"""
from __future__ import annotations

import contextvars
import logging
from contextlib import contextmanager
from typing import Iterator, Optional

logger = logging.getLogger(__name__)


_executor_run_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "autonomous_executor_run_id",
    default=None,
)


# ── Context API ─────────────────────────────────────────────────────


@contextmanager
def set_executor_context(run_id: str) -> Iterator[None]:
    """Bind ``run_id`` to the executor ContextVar for the duration
    of the ``with`` block. The Commander adapter wraps every
    ``commander.handle()`` invocation in this so downstream tools
    (specifically ``coding_session_start``) can detect the executor
    origin.

    Empty / falsy ``run_id`` is a no-op (the var stays None) — keeps
    accidental bare strings from polluting the context.
    """
    if not run_id:
        yield
        return
    token = _executor_run_id.set(run_id)
    try:
        yield
    finally:
        _executor_run_id.reset(token)


def current_executor_run_id() -> Optional[str]:
    """Return the currently-bound executor run_id, or None when no
    executor context is active. Pure read — safe to call from any
    thread or tool wrapper."""
    return _executor_run_id.get()


def is_executor_active() -> bool:
    """Convenience predicate. True iff an executor context is currently
    bound (i.e. the caller is inside an executor-driven dispatch)."""
    return bool(current_executor_run_id())


# ── Cleanup ─────────────────────────────────────────────────────────


_EXECUTOR_AGENT_PREFIX = "executor:"


def executor_agent_id(run_id: str, role: str = "coder") -> str:
    """Build the agent_id the coding-session tool wrapper records when
    invoked under an executor context. Format:

        executor:<run_id>:<role>

    The run_id is the full UUID (not the 8-char operator display);
    role defaults to "coder" because the coder is the only agent
    with coding_session tools in its inventory today. Stable across
    versions so cleanup can match by prefix.
    """
    if not run_id:
        raise ValueError("executor_agent_id: run_id cannot be empty")
    return f"{_EXECUTOR_AGENT_PREFIX}{run_id}:{role}"


def attribute_crs_to_step(
    *,
    run_id: str,
    step_started_at: str,
    step_ended_at: str = "",
) -> list[str]:
    """Find change requests this executor step produced.

    Scans the change-request store for CRs whose:
      * ``requestor`` starts with ``executor:<run_id>:`` (the agent_id
        the coding_session tool wrapper records under an executor
        context — see :func:`executor_agent_id`)
      * ``created_at`` falls in the closed interval
        ``[step_started_at, step_ended_at]`` (or ``[step_started_at, now]``
        when ended_at is empty — step still running)

    Returns the list of CR ids attributable to the step.

    The two filters together close the executor → CR observability
    loop: each step's CR ids tell the operator exactly which gate
    entries came from this run.

    Failure-isolated: a sick change-request store returns an empty
    list rather than crashing the driver's post-step pass.
    """
    if not run_id or not step_started_at:
        return []
    try:
        from app.change_requests.store import list_all
    except Exception:
        logger.debug(
            "attribute_crs_to_step: change_requests.store unimportable",
            exc_info=True,
        )
        return []
    try:
        all_crs = list_all() or []
    except Exception:
        logger.debug(
            "attribute_crs_to_step: list_all failed", exc_info=True,
        )
        return []

    prefix = f"{_EXECUTOR_AGENT_PREFIX}{run_id}:"
    started = step_started_at
    # When step hasn't ended, allow CRs up to "now" as the upper bound.
    ended = step_ended_at or "9999-12-31T23:59:59+00:00"

    out: list[str] = []
    for cr in all_crs:
        requestor = getattr(cr, "requestor", "") or ""
        if not requestor.startswith(prefix):
            continue
        created = getattr(cr, "created_at", "") or ""
        if not (started <= created <= ended):
            continue
        cr_id = getattr(cr, "id", "") or ""
        if cr_id:
            out.append(cr_id)
    return out


def cleanup_sessions_for_run(
    run_id: str,
    *,
    manager: Optional[object] = None,
) -> dict[str, int]:
    """Force-discard every ACTIVE coding session tagged for ``run_id``.

    Called from the driver's terminal-transition path. Idempotent —
    re-running on the same run is a no-op once everything is cleaned.

    Parameters
    ----------
    run_id
        The executor run UUID. Sessions whose ``agent_id`` starts with
        ``f"executor:{run_id}:"`` are scoped to this run.
    manager
        Injectable for tests. Defaults to
        ``coding_session.runtime.get_manager()`` lazily.

    Returns
    -------
    dict[str, int]
        ``{"scanned": N, "discarded": M, "skipped_terminal": K, "errors": E}``.
        ``scanned`` is the number of sessions whose agent_id matched;
        ``discarded`` is the subset successfully transitioned to
        DISCARDED; ``skipped_terminal`` covers race-against-agent
        submissions that finished before cleanup ran; ``errors``
        tracks discard failures (kept for operator visibility).

    Never raises. Errors are logged + reflected in the counts.
    """
    summary = {
        "scanned": 0,
        "discarded": 0,
        "skipped_terminal": 0,
        "errors": 0,
    }
    if not run_id:
        return summary

    if manager is None:
        try:
            from app.coding_session import runtime as cs_runtime
            manager = cs_runtime.get_manager()
        except Exception:
            logger.debug(
                "bridge.cleanup: coding_session runtime unavailable",
                exc_info=True,
            )
            return summary

    prefix = f"{_EXECUTOR_AGENT_PREFIX}{run_id}:"
    try:
        from app.coding_session import Status as CsStatus
        from app.coding_session import store as cs_store
        # ``list_all`` is the canonical lister; filter to ACTIVE here
        # because the cleanup intent is "tear down still-running
        # sessions". Terminal sessions are accounted for separately
        # via the is_active check below.
        candidates = cs_store.list_all(status=CsStatus.ACTIVE, limit=500)
    except Exception:
        logger.debug(
            "bridge.cleanup: list_all failed for run %s",
            run_id, exc_info=True,
        )
        return summary

    for cs in candidates:
        agent = getattr(cs, "agent_id", "") or ""
        if not agent.startswith(prefix):
            continue
        summary["scanned"] += 1
        try:
            if not cs.is_active:
                summary["skipped_terminal"] += 1
                continue
            manager.discard(
                cs.id,
                reason=f"executor run {run_id[:8]} terminated; auto-cleanup",
            )
            summary["discarded"] += 1
            # Best-effort worktree teardown — same shape submit /
            # discard flows use. Manager exposes remove_worktree on
            # the active manager instance.
            try:
                manager.remove_worktree(cs)
            except Exception:
                # Worktree teardown is best-effort; the discard
                # transition has already landed. Worktree retention
                # monitor will sweep later.
                logger.debug(
                    "bridge.cleanup: remove_worktree failed for %s",
                    cs.id, exc_info=True,
                )
        except Exception:
            summary["errors"] += 1
            logger.warning(
                "bridge.cleanup: failed to discard session %s for run %s",
                cs.id, run_id, exc_info=True,
            )

    if summary["scanned"]:
        logger.info(
            "executor_bridge: cleanup for run %s — scanned=%d "
            "discarded=%d skipped_terminal=%d errors=%d",
            run_id[:8],
            summary["scanned"],
            summary["discarded"],
            summary["skipped_terminal"],
            summary["errors"],
        )
    return summary
