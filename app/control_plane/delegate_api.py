"""Control plane — autonomous-executor endpoints at /api/cp/delegate.

Phase 2 piece 2c (2026-05-20). Operator surface for the autonomous
executor shipped in Phase 2 pieces 1 + 2a + 2b. The driver is wired
to the idle scheduler but ships dormant (master switch
``autonomous_executor_enabled`` default OFF); these endpoints make
it possible to:

  POST   /api/cp/delegate                  create a new run from a goal
  GET    /api/cp/delegate                  list runs (filter: active/terminal/all)
  GET    /api/cp/delegate/{run_id}         get a run's full record
  POST   /api/cp/delegate/{run_id}/abort   operator-initiated abort

Auth: same ``require_gateway_auth`` dependency as the rest of /cp/.

Safety semantics
────────────────
* Creating a run does NOT execute it. The run lands in ``CREATED``
  status. The next scheduler tick (when master switch is ON) picks
  it up and advances it.
* Aborting a run transitions to the terminal ``ABORTED`` state.
  Idempotent: a second abort returns the existing record.
* Budgets are clamped to ``EXECUTOR_BUDGET_CAPS`` at create time
  (hard ceilings — operator widening requires a runtime_settings
  edit, which is itself rate-limited + audited).
* ``/abort`` on a terminal run returns 409 (conflict) — the React UI
  surfaces this as "already terminated" so the operator doesn't get
  a misleading "abort succeeded" toast on a stale view.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.autonomous_executor import (
    ExecutorRun,
    ExecutorStatus,
    store,
)
from app.autonomous_executor.models import Budget
from app.control_plane.auth_dep import require_gateway_auth

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/cp/delegate",
    tags=["control-plane", "autonomous-executor"],
    dependencies=[Depends(require_gateway_auth)],
)


# ── Request models ──────────────────────────────────────────────────


class _CreateBody(BaseModel):
    goal: str = Field(
        min_length=1,
        max_length=4000,
        description=(
            "The goal the autonomous executor should pursue. Becomes "
            "the run's primary description and (in v1) the single "
            "step the deterministic planner produces."
        ),
    )
    budget_usd: float | None = Field(
        default=None,
        ge=0.0,
        description=(
            "Per-run USD cap. Defaults to "
            "runtime_settings.executor_default_budget_usd. Clamped to "
            "EXECUTOR_BUDGET_CAPS['max_usd_per_run']."
        ),
    )
    budget_tokens: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Per-run token cap. Defaults to "
            "runtime_settings.executor_default_budget_tokens."
        ),
    )
    budget_wall_clock_s: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Per-run wall-clock cap in seconds. Defaults to "
            "runtime_settings.executor_default_wall_clock_s."
        ),
    )
    zone: str = Field(
        default="chat",
        description=(
            "Verification zone — feeds the gate_output extension "
            "chain. Most /delegate runs should use 'autonomous'."
        ),
    )
    requestor: str = Field(
        default="react-operator",
        description="Identifier for the operator creating the run.",
    )
    mode: str = Field(
        default="standard",
        description=(
            "'standard' (default) runs the deterministic single-step "
            "planner. 'research' pre-plans the five-step "
            "literature -> hypotheses -> investigate -> draft -> gate "
            "chain (app.research.run.build_research_run) and forces the "
            "'autonomous' zone so the research-evidence gate engages."
        ),
    )
    experiment: bool = Field(
        default=False,
        description=(
            "Research mode only. When True, swap the single 'investigate' "
            "step for the design_experiment -> run_experiment -> "
            "analyze_result spine (Phase C). The run_experiment step still "
            "checks the default-OFF 'research_experiments_enabled' switch at "
            "execution time and records a non-blocking 'skipped' marker when "
            "it is off, so requesting experiments is always safe. Ignored "
            "when mode != 'research'."
        ),
    )
    synthesize: bool = Field(
        default=False,
        description=(
            "Research mode only. When True, append a 'synthesize' step that "
            "bakes a ResearchDossier PDF into the run. Optional — the "
            "on-demand GET /{run_id}/research-dossier endpoint already renders "
            "one from any run state. Ignored when mode != 'research'."
        ),
    )


class _AbortBody(BaseModel):
    reason: str = Field(
        default="operator-abort",
        max_length=200,
        description="Optional explanation surfaced in the run's abort_reason.",
    )
    operator: str = Field(default="react-operator")


class _ResumeBody(BaseModel):
    """Body for POST /api/cp/delegate/{run_id}/resume.

    Verified Implementation Plan Gap #2 (2026-05-22). Resumes a
    BLOCKED run back to RUNNING with an operator-provided
    ``unblock_hint`` that becomes a run note + the new RUNNING
    transition's reason.
    """

    unblock_hint: str = Field(
        default="",
        max_length=500,
        description=(
            "Operator guidance for the executor to unblock the run. "
            "Becomes a run note + the RUNNING transition's reason."
        ),
    )
    operator: str = Field(default="react-operator")
    signal_ts: str | None = Field(
        default=None,
        description=(
            "When set, clears the matching bridge entry so the same "
            "Signal escalation can't be resumed twice."
        ),
    )


# ── Helpers ─────────────────────────────────────────────────────────


def _serialize(run: ExecutorRun | None) -> dict[str, Any]:
    if run is None:
        return {}
    d = run.to_dict()
    d["is_terminal"] = run.is_terminal
    return d


def _budget_for_create(body: _CreateBody) -> Budget:
    """Build a Budget for a fresh run, applying defaults + hard ceilings.

    Defaults come from runtime_settings; hard ceilings from
    EXECUTOR_BUDGET_CAPS. The operator-set value in the body is
    clamped to the ceiling (not refused) — this is the friendlier UX
    for "I typed 100 instead of 1" cases while still enforcing safety.
    """
    from app.runtime_settings import (
        EXECUTOR_BUDGET_CAPS,
        get_executor_default_budget_tokens,
        get_executor_default_budget_usd,
        get_executor_default_wall_clock_s,
    )

    cap_usd = body.budget_usd if body.budget_usd is not None else (
        get_executor_default_budget_usd()
    )
    cap_tokens = body.budget_tokens if body.budget_tokens is not None else (
        get_executor_default_budget_tokens()
    )
    cap_wall = (
        body.budget_wall_clock_s
        if body.budget_wall_clock_s is not None
        else get_executor_default_wall_clock_s()
    )

    # Clamp to hard ceilings (refuse-rather-than-clamp would surprise
    # operators who don't know about the ceiling). React UI will show
    # the clamped value back so it's visible.
    cap_usd = min(float(cap_usd), float(EXECUTOR_BUDGET_CAPS["max_usd_per_run"]))
    cap_tokens = min(
        int(cap_tokens),
        int(EXECUTOR_BUDGET_CAPS["max_tokens_per_run"]),
    )
    cap_wall = min(
        int(cap_wall),
        int(EXECUTOR_BUDGET_CAPS["max_wall_clock_s_per_run"]),
    )
    return Budget(
        cap_usd=cap_usd,
        cap_tokens=cap_tokens,
        cap_wall_clock_s=cap_wall,
    )


# ── Routes ──────────────────────────────────────────────────────────


@router.post("")
def create_run(body: _CreateBody):
    """Create a new run in CREATED status. The scheduler picks it up
    on the next tick when ``autonomous_executor_enabled`` is True.

    ``mode="research"`` routes to ``build_research_run``, which returns
    the run with its research plan already attached (the driver treats a
    pre-populated plan as first-class and skips the planner).
    ``experiment=True`` swaps the single ``investigate`` step for the
    design_experiment -> run_experiment -> analyze_result spine (still gated
    at execution time by the default-OFF ``research_experiments_enabled``
    switch); ``synthesize=True`` appends a dossier-PDF step. The
    research-evidence gate only fires outside the ``chat`` zone, so a
    research run left at the default ``chat`` zone is upgraded to
    ``autonomous``; an explicit non-chat zone is honoured.
    """
    if body.mode == "research":
        from app.research.run import build_research_run

        research_zone = "autonomous" if body.zone == "chat" else body.zone
        run = build_research_run(
            body.goal.strip(),
            requestor=body.requestor,
            zone=research_zone,
            budget=_budget_for_create(body),
            experiment=body.experiment,
            synthesize=body.synthesize,
        )
        # Phase D — bind the run to a Thread so cross-run learning runs for
        # free: create_thread consults the lessons_learned KB for adjacent
        # past closures (dedup at creation), and the eventual closure distils
        # what was tried back into the KB (capture at completion). The
        # back-pointer rides in run.notes and is persisted by the shared
        # store.save below. Failure-isolated — an unbound run still runs.
        from app.research.binding import bind_run_to_thread

        thread_id = bind_run_to_thread(run)
        if thread_id:
            logger.info(
                "delegate_api: bound research run %s to thread %s",
                run.run_id, thread_id,
            )
    else:
        run = ExecutorRun(
            run_id=str(uuid.uuid4()),
            goal=body.goal.strip(),
            requestor=body.requestor,
            zone=body.zone,
            budget=_budget_for_create(body),
        )
    store.save(run)
    logger.info(
        "delegate_api: created run %s (mode=%s, experiment=%s, synthesize=%s, "
        "goal_len=%d, requestor=%s, budget_usd=%.2f)",
        run.run_id, body.mode, body.experiment, body.synthesize,
        len(run.goal), run.requestor, run.budget.cap_usd,
    )
    return _serialize(run)


@router.get("")
def list_runs(
    status: str = Query(
        default="all",
        description="Filter: 'active' (non-terminal), 'terminal', or 'all'.",
    ),
    limit: int = Query(default=50, ge=1, le=500),
):
    if status == "active":
        runs = store.list_active(limit=limit)
    elif status == "terminal":
        runs = store.list_terminal(limit=limit)
    elif status == "all":
        runs = store.list_all(limit=limit)
    else:
        raise HTTPException(
            status_code=400,
            detail=(
                f"invalid status filter {status!r}. "
                "Use 'active', 'terminal', or 'all'."
            ),
        )
    return {
        "count": len(runs),
        "runs": [_serialize(r) for r in runs],
    }


@router.get("/{run_id}")
def get_run(run_id: str):
    run = store.get(run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail=f"run {run_id!r} not found",
        )
    return _serialize(run)


@router.get("/{run_id}/research-summary")
def research_summary(run_id: str):
    """Research-meaningful view of a run's artifacts.

    Pulls literature/hypotheses/draft/gate-verdict out of the run's step
    results via ``app.research.run.summarise_run`` so the operator surface
    doesn't have to know the JSON-in-``result_text`` convention. Defined
    for every run — a non-research run simply summarises to zeros/empty,
    which is the honest answer (it carries no research steps).
    """
    run = store.get(run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail=f"run {run_id!r} not found",
        )
    from app.research.run import summarise_run

    return summarise_run(run).to_dict()


@router.get("/{run_id}/research-dossier")
def research_dossier(run_id: str):
    """Render a run's artifacts into a PDF dossier and return its path.

    On-demand companion to ``research-summary``: where that returns the JSON
    artifacts, this bakes them into a multi-page PDF via
    ``app.research.dossier.render_research_dossier``. Works in ANY run state —
    an operator can pull a dossier from a BLOCKED run mid-flight to read the
    flagged claims; gate escalation surfaces inline as a data-quality flag.

    The PDF is written to the dossier output directory and surfaces in
    ``/cp/files`` for download/send; the response carries its server-side path
    + filename. 404 when the run isn't found; 503 when the PDF toolchain
    (reportlab) is unavailable in this runtime.
    """
    run = store.get(run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail=f"run {run_id!r} not found",
        )
    from app.research.dossier import render_research_dossier

    try:
        path = render_research_dossier(run)
    except RuntimeError as exc:
        # reportlab absent — the only thing render raises deterministically.
        raise HTTPException(status_code=503, detail=str(exc))
    logger.info(
        "delegate_api: rendered research dossier for run %s -> %s",
        run_id, path.name,
    )
    return {
        "run_id": run_id,
        "path": str(path),
        "filename": path.name,
    }


@router.post("/{run_id}/abort")
def abort_run(run_id: str, body: _AbortBody):
    """Transition the run to ABORTED. Idempotent: aborting a
    terminal run returns 409 with the existing status."""
    run = store.get(run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail=f"run {run_id!r} not found",
        )
    if run.is_terminal:
        raise HTTPException(
            status_code=409,
            detail=(
                f"run {run_id!r} is already terminal "
                f"(status={run.status.value}); cannot abort"
            ),
        )
    try:
        run.transition(
            ExecutorStatus.ABORTED,
            reason=body.reason or "operator-abort",
        )
    except Exception as exc:
        # Defensive — should not happen given the is_terminal guard
        # above, but a race between read + transition could land here.
        raise HTTPException(
            status_code=409,
            detail=f"abort failed: {exc!r}",
        )
    store.save(run)
    logger.info(
        "delegate_api: aborted run %s (operator=%s, reason=%s)",
        run.run_id, body.operator, body.reason,
    )
    return _serialize(run)


@router.post("/{run_id}/resume")
def resume_run(run_id: str, body: _ResumeBody):
    """Transition the run from BLOCKED back to RUNNING.

    Verified Implementation Plan Gap #2 (2026-05-22). The BLOCKED
    state previously had no canonical resume path — the only
    operator-actionable transition was abort. This endpoint closes
    that loop. Returns 404 when the run isn't found, 409 when the
    run isn't currently BLOCKED, 200 with the updated serialised
    run on success.

    Delegates to :func:`escalation.resume_blocker` so the Signal
    reaction handler and the React UI share the same logic.
    """
    from app.autonomous_executor.escalation import resume_blocker

    result = resume_blocker(
        run_id=run_id,
        unblock_hint=body.unblock_hint,
        operator=body.operator,
        signal_ts=body.signal_ts,
    )

    if not result["ok"]:
        # Map result errors → HTTP statuses
        status = result.get("status", "unknown")
        if status == "missing":
            raise HTTPException(
                status_code=404,
                detail=f"run {run_id!r} not found",
            )
        # Anything else is a state conflict (not BLOCKED / illegal
        # transition / etc.)
        raise HTTPException(
            status_code=409,
            detail=result.get("error", "resume failed"),
        )

    # Re-fetch the run for a fresh serialisation
    run = store.get(run_id)
    logger.info(
        "delegate_api: resumed run %s (operator=%s, hint=%s)",
        run_id, body.operator, body.unblock_hint[:80],
    )
    return _serialize(run)
