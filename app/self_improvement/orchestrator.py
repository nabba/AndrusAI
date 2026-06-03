"""orchestrator — drives the verified mutation engine and files operator-gated CRs.

This is the activation layer that replaces the legacy ``evolution.py`` code+skill
mutation path (hard-cut behind ``evolution_verified_engine_enabled``). For each
cycle it:

  1. plans a (target_file, approach) — reusing the existing AVO planner, which
     produces good hypotheses; only the broken implement/measure path is dropped.
  2. spawns an ephemeral evolver container (``evolver_spawn.run_evolver_job``)
     that grounds → implements-in-worktree → evaluates-by-execution → returns a
     verdict + the changed-file contents.
  3. if the verdict is proposable (IMPROVED / INVARIANTS_ONLY), files ONE
     change-request per changed file with a rich evidence trail, routed through
     the standard operator gate (always operator-approved — no auto-deploy).

Tier: OPEN/GENERATION. It never judges improvement itself (that's the immutable
``worktree_eval`` inside the container) and never applies code (that's the
operator-gated change-request system).
"""
from __future__ import annotations

import functools
import json
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ── Concurrency guard ────────────────────────────────────────────────────────
# Exactly one verified self-improvement run at a time. The idle "evolution" job,
# the APScheduler cron, and operator-initiated executor runs all reach the
# engine; two concurrent runs would spawn evolver containers in parallel (cost +
# the §76 OOM-the-gateway failure mode). RLock so a session may call
# run_verified_cycle on its own thread (re-entrant); a *different* thread gets a
# non-blocking skip rather than queueing.
_ENGINE_LOCK = threading.RLock()


def _single_run(skip_return):
    """Serialize verified self-improvement across triggers; skip (never queue)
    when a run is already active on another thread."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not _ENGINE_LOCK.acquire(blocking=False):
                logger.info("verified engine busy — skipping concurrent %s trigger", fn.__name__)
                return skip_return() if callable(skip_return) else skip_return
            try:
                return fn(*args, **kwargs)
            finally:
                _ENGINE_LOCK.release()
        return wrapper
    return deco


def _read_current(path: str) -> str:
    """The currently-deployed content of ``path`` (for the CR diff + rollback).

    The gateway runs from /app; that's the baseline the operator is changing.
    """
    try:
        return (Path("/app") / path).read_text(encoding="utf-8")
    except OSError:
        return ""


def _norm_target(target: str) -> str:
    t = str(target).strip().lstrip("/")
    if not t.startswith("app/") and not t.startswith("tests/"):
        t = f"app/{t}"
    return t


def _evidence_reason(target_file: str, approach: str, verdict: dict, result: dict) -> str:
    """The operator-facing CR rationale — REAL evidence, not a noise delta.

    This is the antidote to the old "Δ +0.0133 — needs review" alert: every line
    here is a fact produced by running the changed code.
    """
    ev = verdict.get("evidence", {}) or {}
    lines = [
        f"Verified self-improvement — {target_file}",
        f"Approach: {approach}",
        f"Verdict: {verdict.get('verdict')} — {verdict.get('reason', '')}",
    ]
    corr = ev.get("correctness")
    if corr:
        lines.append(
            f"Correctness: fixed {len(corr.get('fixes', []))} test(s), "
            f"{len(corr.get('regressions', []))} regression(s) (Δ={corr.get('delta')})"
        )
    qual = ev.get("quality")
    if qual:
        lines.append(
            f"Quality: Δ={qual.get('mean_delta')} "
            f"({qual.get('wins')}↑/{qual.get('losses')}↓, n={qual.get('samples')})"
        )
    diff = result.get("diff", "") or ""
    plus = sum(1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
    minus = sum(1 for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---"))
    lines.append(
        f"Diff: +{plus}/-{minus} lines across {len(result.get('changed_files', []))} file(s)."
    )
    lines.append(
        "Evolver-verified by execution: module imports, covering tests green, "
        "public API preserved."
    )
    return "\n".join(lines)


def _default_cr_filer(**kwargs: Any) -> Any:
    """Production CR filer — lazily imports the (pydantic-gated) change-request
    lifecycle so this module stays importable on a host without the full env."""
    from app.change_requests.lifecycle import create_request

    return create_request(**kwargs)


def _record_evo_outcome(
    approach: str,
    target_file: str,
    *,
    success: bool,
    verdict: Optional[dict] = None,
    reason: str = "",
) -> None:
    """Feed evo_memory so the planner learns across runs.

    Round-5: evo_memory is the verified engine's memory — the planner reads
    ``recall_similar_failures`` (planning.py) to skip re-trying a failed
    approach, so the engine MUST record each genuine verdict here (a
    not-proposable run is a real "this didn't improve" signal; a filed run is
    a success). Keyed on the approach text the planner recalls on. Infra
    failures (no verdict) never reach this. Failure-isolated.
    """
    try:
        from app import evo_memory

        target = _norm_target(target_file)
        key = approach or target
        if success:
            v = verdict or {}
            try:
                delta = float(v.get("quality_delta") or v.get("correctness_delta") or 0.0)
            except (TypeError, ValueError):
                delta = 0.0
            evo_memory.store_success(
                hypothesis=key,
                change_type="code",
                delta=delta,
                files=[target],
                detail=reason[:500],
            )
        else:
            evo_memory.store_failure(
                hypothesis=key,
                change_type="code",
                reason=reason[:500],
            )
    except Exception as exc:  # pragma: no cover - observational
        logger.debug("orchestrator: evo_memory record failed: %s", exc)


@_single_run(lambda: {"ok": False, "skipped": True, "error": "another verified run is in progress"})
def run_verified_cycle(
    target_file: str,
    approach: str,
    *,
    requestor: str = "self_improver",
    budget_usd: float = 5.0,
    spawn: Optional[Callable[[dict], dict]] = None,
    cr_filer: Optional[Callable[..., Any]] = None,
) -> dict:
    """Run ONE verified self-improvement: spawn the evolver, gate on the verdict,
    file change-requests with evidence. Returns a summary dict (never raises).
    """
    from app.self_improvement.evolver_spawn import image_exists, run_evolver_job

    spawn_fn = spawn or run_evolver_job
    if spawn_fn is run_evolver_job and not image_exists():
        return {
            "ok": False,
            "error": (
                "evolver image not built — run: "
                "docker compose --profile evolver build evolver"
            ),
        }

    out = spawn_fn(
        {"target_file": _norm_target(target_file), "approach": approach, "budget_usd": budget_usd}
    )
    if not out.get("ok"):
        return {"ok": False, "stage": "evolver", "error": out.get("error", "unknown")}

    result = out.get("result", {}) or {}
    verdict = result.get("verdict", {}) or {}
    vname = verdict.get("verdict")

    if not result.get("proposable"):
        # Genuine "this approach didn't improve" — record so the planner won't
        # re-try it (recall_similar_failures).
        _record_evo_outcome(
            approach, target_file, success=False,
            reason=f"verified engine: {vname or 'not proposable'} — not an improvement",
        )
        return {"ok": True, "verdict": vname, "filed": [], "note": "not proposable"}

    contents = result.get("changed_file_contents") or {}
    if not contents:
        return {"ok": True, "verdict": vname, "filed": [], "note": "proposable but no changed files"}

    reason = _evidence_reason(_norm_target(target_file), approach, verdict, result)
    filer = cr_filer or _default_cr_filer
    filed: list[str] = []
    for path, new_content in contents.items():
        try:
            cr = filer(
                requestor=requestor,
                path=path,
                new_content=new_content,
                old_content=_read_current(path),
                reason=reason,
            )
            filed.append(getattr(cr, "id", None) or getattr(cr, "request_id", "") or str(cr))
        except Exception as exc:
            logger.warning("orchestrator: CR filing failed for %s: %s", path, exc)

    # Honest self-narrative: record the self-modification in the identity
    # continuity ledger (the verified engine's CR is the proof). Failure-
    # isolated — the consciousness boundary is observational, never blocking.
    if filed:
        # A filed CR = the engine found a real, verified improvement worth
        # proposing — record it as a success the planner can learn from.
        _record_evo_outcome(approach, target_file, success=True, verdict=verdict, reason=reason)
        try:
            from app.identity.continuity_ledger import record_event

            evidence = verdict.get("evidence")
            record_event(
                kind="self_modification",
                actor="self_improver",
                summary=(
                    f"Verified self-modification to {_norm_target(target_file)} "
                    f"({vname}); filed {len(filed)} change-request(s) for operator review"
                ),
                detail={
                    "target_file": _norm_target(target_file),
                    "approach": approach,
                    "verdict": vname,
                    "cr_ids": filed,
                    "evidence": evidence,
                },
            )
        except Exception as exc:  # pragma: no cover - observational
            logger.debug("orchestrator: self_modification ledger emit failed: %s", exc)

    return {"ok": True, "verdict": vname, "filed": filed, "evidence": verdict.get("evidence")}


def _plan_target(tried: set[str]) -> Optional[tuple[str, str]]:
    """Use the verified-engine planner (``self_improvement.planning``) to get a
    (target_file, approach) for a CODE change. Returns None when the planner
    declines or proposes a non-code change.
    """
    try:
        from app.self_improvement.planning import _phase_planning, _build_evolution_context
    except Exception as exc:
        logger.warning("orchestrator: planner unavailable: %s", exc)
        return None

    try:
        context = _build_evolution_context()
        plan = _phase_planning(context, "", "", tried)
    except Exception as exc:
        logger.warning("orchestrator: planning failed: %s", exc)
        return None

    if not plan or plan.get("change_type") != "code":
        return None
    targets = [t for t in (plan.get("target_files") or []) if str(t).endswith(".py")]
    if not targets:
        return None
    approach = plan.get("approach") or plan.get("hypothesis") or ""
    hyp = plan.get("hypothesis", "")
    if hyp:
        tried.add(hyp)
    return _norm_target(targets[0]), approach


@_single_run("Verified self-improvement skipped: another run is already in progress")
def run_verified_session(max_iterations: int = 5) -> str:
    """Plan + run up to ``max_iterations`` verified cycles. Drop-in replacement
    for ``evolution.run_evolution_session`` when the verified engine is on."""
    try:
        from app.runtime_settings import get_evolution_verified_per_cycle_budget_usd

        budget = get_evolution_verified_per_cycle_budget_usd()
    except Exception:
        budget = 5.0

    summaries: list[str] = []
    tried: set[str] = set()

    for i in range(max_iterations):
        try:
            from app.idle_scheduler import should_yield

            if should_yield():
                summaries.append(f"[yielded after {i} iterations]")
                break
        except Exception:
            pass

        planned = _plan_target(tried)
        if planned is None:
            summaries.append(f"[{i + 1}] no code target proposed")
            continue
        target, approach = planned
        out = run_verified_cycle(target, approach, budget_usd=budget)
        if not out.get("ok"):
            summaries.append(f"[{i + 1}] {target}: error — {out.get('error', '')[:80]}")
        else:
            summaries.append(
                f"[{i + 1}] {target}: {out.get('verdict')} "
                f"(filed {len(out.get('filed', []))} CR(s))"
            )

    return "Verified mutation session:\n" + "\n".join(summaries) if summaries else (
        "Verified mutation session: no iterations run"
    )


# ── autonomous_executor routing (operator-initiated self-improvement) ────────


def _maybe_self_improve_job(description: str) -> Optional[dict]:
    """Detect a self-improvement job encoded as JSON in a step description."""
    try:
        data = json.loads(description)
    except (TypeError, ValueError):
        return None
    if isinstance(data, dict) and data.get("target_file"):
        return data
    return None


def make_self_improvement_adapter(
    default_adapter: Optional[Callable[..., Any]] = None
) -> Callable[..., Any]:
    """A drop-in autonomous_executor commander adapter that dispatches
    self-improvement runs (a step whose description is a JSON job with a
    ``target_file``) deterministically through ``run_verified_cycle``, and
    delegates every other run to the normal Commander adapter.

    This is how an operator's ``/delegate`` self-improvement request is "routed
    via autonomous_executor" (audit + budget + /cp/delegate visibility) while
    still running the verified pipeline deterministically — NOT as a prose
    Commander step that could be mis-routed.
    """
    base = default_adapter

    def _adapter(step: Any, run: Any) -> Any:
        from app.autonomous_executor.driver import CommanderResult

        job = _maybe_self_improve_job(getattr(step, "description", "") or "")
        if job is not None:
            out = run_verified_cycle(
                job["target_file"],
                job.get("approach", ""),
                requestor=getattr(run, "requestor", "") or "self_improver",
                budget_usd=float(job.get("budget_usd", 5.0) or 5.0),
            )
            return CommanderResult(text=json.dumps(out), cost_usd=0.0, tokens_used=0)

        nonlocal base
        if base is None:
            from app.autonomous_executor.commander_adapter import make_commander_adapter

            base = make_commander_adapter()
        return base(step, run)

    return _adapter


def enqueue_self_improvement(
    target_file: str,
    approach: str,
    *,
    budget_usd: float = 5.0,
    requestor: str = "self_improver",
) -> dict:
    """Create an autonomous_executor run for one self-improvement so it appears
    in /cp/delegate and is tracked by the executor's audit + budget. When the
    executor advances it, ``make_self_improvement_adapter`` runs the verified
    pipeline. Used by the operator-facing ``/delegate improve …`` path."""
    job = {
        "target_file": _norm_target(target_file),
        "approach": approach,
        "budget_usd": budget_usd,
    }
    from app.autonomous_executor.tools.delegate_tool import delegate_goal

    return delegate_goal(json.dumps(job), budget_usd=budget_usd, requestor=requestor)
