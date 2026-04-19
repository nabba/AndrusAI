"""
app.trajectory.logger — Per-crew trajectory capture (contextvars-scoped).

The logger is pure data capture. It makes no LLM calls, allocates no
background threads, and has one invariant: **any failure in this module
must not break crew execution**. Every entrypoint wraps in try/except
and returns a safe default.

Scoping: `contextvars.ContextVar` isolates the "active trajectory" per
asyncio task / thread-with-copy_context. The commander orchestrator's
concurrent dispatch pool (`_ctx_pool`) inherits parent context on
submission, so each crew call sees its own trajectory.

Public surface:

    begin_trajectory(task_id, crew_name, task_description) -> Trajectory
    capture_step(step: TrajectoryStep) -> None
    capture_observer_prediction(prediction: dict, *, recommendation_followed=False)
    end_trajectory(outcome_summary: dict) -> Trajectory | None
    on_crew_complete(outcome, trajectory) -> None   # convenience fan-out

Every function is a no-op when `settings.trajectory_enabled` is False.

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import contextvars
import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.trajectory.types import (
    Trajectory, TrajectoryStep,
    STEP_PHASE_OBSERVER,
)

logger = logging.getLogger(__name__)

# Per-call isolation. Inherited across ThreadPoolExecutor.submit via
# copy_context (the default when contextvars.Context.run is used).
_current_trajectory: contextvars.ContextVar[Optional[Trajectory]] = contextvars.ContextVar(
    "current_trajectory", default=None,
)


# ── Flag check (single source of truth) ─────────────────────────────────────

def _enabled() -> bool:
    try:
        from app.config import get_settings
        return bool(get_settings().trajectory_enabled)
    except Exception:
        return False


# ── Hash helper ─────────────────────────────────────────────────────────────

def _sha(text: str) -> str:
    """Stable 12-char digest — matches map_elites_wiring's hashing idiom."""
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()[:12]


# ── Entry points ────────────────────────────────────────────────────────────

def begin_trajectory(
    task_id: str,
    crew_name: str,
    task_description: str,
) -> Optional[Trajectory]:
    """Start a new trajectory for the current execution context.

    Returns the Trajectory on success, or None when capture is disabled /
    an existing trajectory is already active in this context. The second
    case means a reentrant call — we don't nest trajectories (the outer
    one is the authoritative top-level record).
    """
    if not _enabled():
        return None
    try:
        if _current_trajectory.get() is not None:
            # Reentrant — leave the outer trajectory alone.
            return None
        traj = Trajectory(
            trajectory_id=f"traj_{uuid.uuid4().hex[:16]}",
            task_id=str(task_id or ""),
            crew_name=str(crew_name or ""),
            task_description=str(task_description or "")[:2000],
        )
        _current_trajectory.set(traj)
        return traj
    except Exception:
        logger.debug("begin_trajectory failed", exc_info=True)
        return None


def current_trajectory() -> Optional[Trajectory]:
    """Return the active Trajectory for this context (or None)."""
    try:
        return _current_trajectory.get()
    except Exception:
        return None


def capture_step(step: TrajectoryStep) -> bool:
    """Append a step to the active trajectory. Returns True on success."""
    if not _enabled():
        return False
    try:
        traj = _current_trajectory.get()
        if traj is None:
            return False
        # Fill hashes from samples if caller didn't provide them — keeps
        # the call site terse while preserving the invariant that each
        # sample has a stable digest.
        if step.tool_args_sample and not step.tool_args_hash:
            step.tool_args_hash = _sha(step.tool_args_sample)
        if step.output_sample and not step.output_hash:
            step.output_hash = _sha(step.output_sample)
        traj.append_step(step)
        return True
    except Exception:
        logger.debug("capture_step failed", exc_info=True)
        return False


def capture_observer_prediction(
    prediction: dict,
    *,
    agent_role: str = "",
    recommendation_followed: bool = False,
    mcsv_snapshot: str = "",
) -> bool:
    """Record an Observer firing as a dedicated STEP_PHASE_OBSERVER step.

    The prediction dict is the exact payload returned by
    `app.agents.observer.predict_failure`. Capturing it verbatim keeps
    the attribution and calibration pipelines aligned with whatever the
    Observer actually produced — no re-interpretation.
    """
    if not _enabled():
        return False
    try:
        traj = _current_trajectory.get()
        if traj is None:
            return False
        step = TrajectoryStep(
            step_idx=-1,  # assigned by Trajectory.append_step
            agent_role=agent_role or traj.crew_name,
            phase=STEP_PHASE_OBSERVER,
            planned_action=(prediction or {}).get("recommendation", "")[:400],
            observer_prediction=dict(prediction or {}),
            observer_recommendation_followed=bool(recommendation_followed),
            mcsv_snapshot=mcsv_snapshot,
        )
        traj.append_step(step)
        return True
    except Exception:
        logger.debug("capture_observer_prediction failed", exc_info=True)
        return False


def note_injected_skills(skill_ids: list[str]) -> bool:
    """Record which SkillRecord ids were surfaced into this trajectory's
    prompt by the context_builder. Phase 6 — effectiveness correlation.

    Idempotent: repeated calls merge unique ids into the active trajectory.
    No-op when trajectory capture is off.
    """
    if not _enabled() or not skill_ids:
        return False
    try:
        traj = _current_trajectory.get()
        if traj is None:
            return False
        existing = set(traj.injected_skill_ids)
        added = [sid for sid in skill_ids if sid and sid not in existing]
        if added:
            traj.injected_skill_ids.extend(added)
        return True
    except Exception:
        logger.debug("note_injected_skills failed", exc_info=True)
        return False


def end_trajectory(outcome_summary: Optional[dict] = None) -> Optional[Trajectory]:
    """Close the active trajectory and clear the context var.

    Returns the finalised Trajectory (caller decides whether to persist).
    Safe to call with no active trajectory — returns None.
    """
    if not _enabled():
        return None
    try:
        traj = _current_trajectory.get()
        if traj is None:
            return None
        traj.ended_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if outcome_summary:
            traj.outcome_summary = dict(outcome_summary)
        _current_trajectory.set(None)
        return traj
    except Exception:
        logger.debug("end_trajectory failed", exc_info=True)
        return None


# ── Convenience fan-out — called from post-crew telemetry ──────────────────

def on_crew_complete(outcome, trajectory: Optional[Trajectory]) -> None:
    """Persist + (optionally) trigger attribution for a finished trajectory.

    Called from the commander's post-crew telemetry hook AFTER
    `record_crew_outcome(outcome)`. Keeps map_elites_wiring untouched —
    the two concerns are fanned out side by side at the call site.

    This function only persists the trajectory and dispatches attribution.
    Attribution in turn may emit a LearningGap, which the Self-Improver
    picks up on its next idle sweep. No synchronous dependency on tip
    synthesis here — that's loose-coupled through the gap pipeline.
    """
    if trajectory is None:
        return
    try:
        # Attach the outcome summary we already have in hand so the
        # Attribution Analyzer doesn't need to re-derive it. Strictly
        # additive — doesn't clobber an outcome_summary that end_trajectory
        # already wrote.
        if outcome is not None and not trajectory.outcome_summary:
            try:
                trajectory.outcome_summary = {
                    "crew_name": getattr(outcome, "crew_name", ""),
                    "duration_s": float(getattr(outcome, "duration_s", 0.0) or 0.0),
                    "difficulty": int(getattr(outcome, "difficulty", 0) or 0),
                    "confidence": str(getattr(outcome, "confidence", "") or ""),
                    "completeness": str(getattr(outcome, "completeness", "") or ""),
                    "passed_quality_gate": bool(getattr(outcome, "passed_quality_gate", True)),
                    "has_result": bool(getattr(outcome, "has_result", True)),
                    "is_failure_pattern": bool(getattr(outcome, "is_failure_pattern", False)),
                    "retries": int(getattr(outcome, "retries", 0) or 0),
                    "reflexion_exhausted": bool(getattr(outcome, "reflexion_exhausted", False)),
                    "result_sample": str(getattr(outcome, "result", "") or "")[:400],
                }
            except Exception:
                logger.debug("on_crew_complete: outcome summary build failed", exc_info=True)

        from app.trajectory.store import persist_trajectory
        persist_trajectory(trajectory)
    except Exception:
        logger.debug("on_crew_complete: persistence failed", exc_info=True)

    # Phase 2 — attribution. Imported lazily and guarded by its own flag
    # so Phase-1-only rollouts don't pay the cost.
    attribution = None
    try:
        from app.config import get_settings
        if get_settings().attribution_enabled:
            from app.trajectory.attribution import maybe_analyze
            attribution = maybe_analyze(trajectory)
    except Exception:
        logger.debug("on_crew_complete: attribution dispatch failed", exc_info=True)

    # Phase 6 — tip effectiveness correlation. Runs regardless of whether
    # attribution fired: baseline runs still contribute to the "use" count
    # so effectiveness ratios aren't biased toward problem trajectories.
    # No-op when trajectory_enabled is False or no tips were injected.
    try:
        if trajectory.injected_skill_ids:
            from app.trajectory.effectiveness import record_use
            record_use(trajectory, attribution)
    except Exception:
        logger.debug("on_crew_complete: effectiveness record failed", exc_info=True)
