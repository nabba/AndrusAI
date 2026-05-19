"""task_recovery — CrewAI agent/task-layer recovery drill.

Survey response to arXiv:2604.27096 §4.3.4 + §4.4.5 (Bâra et al.,
"Think it, Run it"). The paper measured 73.3% recovery on injected
ML-pipeline failures using LLM-driven alternative selection; the
analogous metric does not exist for BotArmy's agent-task layer.
This drill produces it.

What the drill measures
-----------------------

For each of 4 failure classes (type_mismatch, missing_field,
numerical_anomaly, transient_timeout), the drill:

  1. Runs a baseline kickoff of the fixture crew with no injection.
     If the baseline fails, the run is SKIPPED (vendor outage, not
     a regression).
  2. Runs an injected kickoff. The injection mutates only the
     fixture tool's return — no production code is touched.
  3. Inspects the output AND the audit trail. A run scores as
     "recovered" iff BOTH:
       * the output matches ``EXPECTED_ANSWER_REGEX`` (the agent
         eventually reported "The value is 42"), AND
       * at least one of the named recovery mechanisms fired in the
         audit window (tool_supervisor classify/retry/substitute,
         structured_diagnosis CR, or recovery_loop strategy).

The second AND is load-bearing. Without it, an LLM that happens to
guess "42" or that re-calls the tool by chance would count as
recovery — the metric would then drift upward without any
improvement in the actual recovery layers.

Pass threshold
--------------

PASS at ``recovery_rate ≥ 0.75`` (matches the paper). FAIL below.
Q18 baseline ratification applies — the first ``warmup_days`` of
runs go in as observations; alerts kick in after operator
ratification.

Risk
----

LOW. The fixture crew is drill-only, never exposed to production
callers. Injection is via a ContextVar read by ``drill_lookup``
only — no production tool reads it. ``meta_agent`` excludes this
crew's outcomes from recipe scoring via ``DRILL_CREW_NAME``.

Cost
----

Hard cap ``_BUDGET_USD_PER_RUN = 0.10``. Typical cost ~$0.02 per
quarterly run (8 cheap-tier kickoffs + 4 Haiku variant calls).
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# Q18: lock/audit/landmark are threaded by the orchestrator; no
# direct app.resilience_drills.audit imports are needed in this module.
from app.resilience_drills.fixtures.task_recovery_crew import (
    DRILL_CREW_NAME,
    EXPECTED_ANSWER_REGEX,
    _InjectionState,
    build_drill_crew,
    reset_injection,
    set_injection,
)
from app.resilience_drills.fixtures.variant_generator import generate_variant
from app.resilience_drills.protocol import (
    DrillResult,
    DrillRisk,
    DrillSpec,
    DrillStatus,
    FailureClass,
    register,
)

logger = logging.getLogger(__name__)


SPEC = DrillSpec(
    name="task_recovery",
    cadence_days=90,
    grace_days=30,
    risk=DrillRisk.LOW,
    description=(
        "Quarterly drill. Injects 4 failure classes into a synthetic "
        "agent task and measures recovery rate via named mechanisms "
        "(tool_supervisor / structured_diagnosis / recovery_loop). "
        "Closes the agent/task-layer gap vs paper arXiv:2604.27096 §4.3.4."
    ),
    requires_master_switch="drill_task_recovery_enabled",
    warmup_days=7,
)


# ── Constants ────────────────────────────────────────────────────────────


FAILURE_CLASSES: tuple[str, ...] = (
    "type_mismatch",
    "missing_field",
    "numerical_anomaly",
    "transient_timeout",
)

PASS_THRESHOLD: float = 0.75      # paper's reported recovery rate
_BUDGET_USD_PER_RUN: float = 0.10  # hard cap, drill aborts above

_RECOVERY_ACTORS: tuple[str, ...] = (
    "tool_supervisor",
    "error_diagnosis",       # structured_diagnosis files CRs as this requestor
    "recovery_loop",
)

_ANSWER_RE = re.compile(EXPECTED_ANSWER_REGEX, re.IGNORECASE)


# ── Helpers ──────────────────────────────────────────────────────────────


def _text_recovered(output: str | None) -> bool:
    """The output regex matches the expected answer."""
    if not output:
        return False
    return bool(_ANSWER_RE.search(output))


def _query_recovery_audit(since: datetime) -> list[dict[str, Any]]:
    """Pull audit rows authored by recovery actors since ``since``.

    Failure-isolated: any exception (no DB, schema mismatch, …)
    returns an empty list — caller then treats mechanism as
    undetectable and scores conservatively.
    """
    try:
        from app.control_plane.audit import get_audit
    except Exception:
        return []
    try:
        audit = get_audit()
    except Exception:
        return []

    rows: list[dict[str, Any]] = []
    for actor in _RECOVERY_ACTORS:
        try:
            chunk = audit.query(actor=actor, since=since, limit=20)
        except Exception:
            chunk = []
        rows.extend(chunk or [])
    return rows


def _detect_mechanism(audit_rows: list[dict[str, Any]]) -> str | None:
    """Return the first named recovery mechanism present in
    ``audit_rows``, or None if none fired.

    Preference order:
      1. tool_supervisor (most direct evidence)
      2. error_diagnosis (structured_diagnosis path)
      3. recovery_loop (general fallback)
    """
    by_actor: dict[str, list[str]] = {}
    for row in audit_rows:
        actor = str(row.get("actor") or "")
        if actor in _RECOVERY_ACTORS:
            by_actor.setdefault(actor, []).append(str(row.get("action") or ""))

    for actor in _RECOVERY_ACTORS:
        actions = by_actor.get(actor)
        if not actions:
            continue
        # First action gives us the "what fired" detail for the audit.
        action = actions[0]
        return f"{actor}.{action}" if action else actor
    return None


# ── The injection runner ─────────────────────────────────────────────────


def _make_default_kickoff() -> Callable[[], str]:
    """Build a real ``crew.kickoff()`` closure. The drill calls this
    fresh per pass so the agent state is isolated.

    Lazy to keep crewai out of module import for tests that stub
    the kickoff.
    """
    def _kickoff() -> str:
        crew, _agent, _task = build_drill_crew()
        return str(crew.kickoff()).strip()
    return _kickoff


def _run_one_class(
    failure_class: str,
    *,
    kickoff_fn: Callable[[], str],
    use_llm_variants: bool,
    budget_remaining_usd: float,
    cost_per_kickoff_usd: float,
) -> dict[str, Any]:
    """Run one (baseline, injection) pair for a single failure class.

    Returns the per-class observation block. Never raises; every
    exception path lands in the result with ``status="error"``.
    """
    per_class: dict[str, Any] = {
        "failure_class": failure_class,
        "baseline_ok": False,
        "injected_recovered": False,
        "mechanism": None,
        "variant_source": None,
        "variant": None,
        "baseline_output_excerpt": "",
        "injected_output_excerpt": "",
        "status": "ok",
        "errors": [],
    }

    if budget_remaining_usd < cost_per_kickoff_usd * 2:
        per_class["status"] = "budget_exceeded"
        return per_class

    # 1. Baseline (no injection).
    try:
        baseline_output = kickoff_fn()
    except Exception as exc:
        per_class["status"] = "baseline_error"
        per_class["errors"].append(f"{type(exc).__name__}: {exc}")
        return per_class
    per_class["baseline_output_excerpt"] = (baseline_output or "")[:200]
    per_class["baseline_ok"] = _text_recovered(baseline_output)

    if not per_class["baseline_ok"]:
        # Vendor problem, not a recovery regression. Caller flips
        # the whole drill to SKIPPED when any class is in this state.
        per_class["status"] = "baseline_failed"
        return per_class

    # 2. Generate variant.
    try:
        variant, variant_source = generate_variant(
            failure_class, use_llm=use_llm_variants
        )
    except Exception as exc:
        per_class["status"] = "variant_error"
        per_class["errors"].append(f"variant: {type(exc).__name__}: {exc}")
        return per_class
    per_class["variant"] = variant
    per_class["variant_source"] = variant_source

    # 3. Injected kickoff.
    state = _InjectionState(failure_class=failure_class, variant=variant)
    token = set_injection(state)
    since = datetime.now(timezone.utc)
    try:
        injected_output = kickoff_fn()
    except Exception as exc:
        # The crew kickoff itself raised — agent could not recover
        # at all. Score as non-recovery; record exception for audit.
        per_class["status"] = "injection_kickoff_raised"
        per_class["errors"].append(f"{type(exc).__name__}: {exc}")
        per_class["injected_recovered"] = False
        return per_class
    finally:
        reset_injection(token)

    per_class["injected_output_excerpt"] = (injected_output or "")[:200]

    # 4. Score: text match AND named mechanism fired.
    text_ok = _text_recovered(injected_output)
    audit_rows = _query_recovery_audit(since=since)
    mechanism = _detect_mechanism(audit_rows)
    per_class["mechanism"] = mechanism
    per_class["injected_recovered"] = bool(text_ok and mechanism)
    if text_ok and not mechanism:
        # Important signal: the agent happened to produce the right
        # answer without any named recovery layer engaging.
        per_class["status"] = "text_ok_no_mechanism"
    return per_class


# ── Top-level drill entry ────────────────────────────────────────────────


def _persist_report(report: dict[str, Any]) -> Path | None:
    """Write the detailed per-class report to a dated file under
    ``workspace/resilience/task_recovery/``. Returns the path or
    None on write failure (non-fatal)."""
    try:
        from app.paths import WORKSPACE_ROOT
        base = Path(WORKSPACE_ROOT) / "resilience" / "task_recovery"
    except Exception:
        base = Path("/app/workspace/resilience/task_recovery")
    try:
        base.mkdir(parents=True, exist_ok=True)
        fname = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ.json")
        path = base / fname
        path.write_text(json.dumps(report, indent=2, sort_keys=True),
                        encoding="utf-8")
        return path
    except OSError:
        logger.debug("task_recovery: report write failed", exc_info=True)
        return None


def _live_enabled() -> bool:
    """Operator opt-in for LIVE mode (real LLM calls).

    Default OFF. Returns False on any error reading the switch —
    drill stays DRY_RUN until operator explicitly enables.
    """
    try:
        from app.runtime_settings import get_drill_task_recovery_live_enabled
        return bool(get_drill_task_recovery_live_enabled())
    except Exception:
        return False


def _llm_variants_enabled() -> bool:
    """Operator switch for LLM-generated variants. Default ON when
    LIVE mode is on (matches user-confirmed scope). Always FALSE in
    DRY_RUN mode — variant generator never gets called there."""
    try:
        from app.runtime_settings import get_drill_task_recovery_llm_variants_enabled
        return bool(get_drill_task_recovery_llm_variants_enabled())
    except Exception:
        return True


def _run(
    *,
    dry_run: bool = True,
    kickoff_fn: Callable[[], str] | None = None,
    audit_query_fn: Callable[[datetime], list[dict[str, Any]]] | None = None,
) -> DrillResult:
    """Main entry. ``kickoff_fn`` and ``audit_query_fn`` are
    injected for tests; production paths use the defaults.

    Q18 (PROGRAM §57) runner contract: returns a bare DrillResult;
    the orchestrator threads lock + audit + landmark + state.
    """
    started = datetime.now(timezone.utc)
    t0 = time.time()

    # Patch the audit query helper if the caller (test) injected
    # one. We use module-level rebind so _run_one_class picks it up.
    global _query_recovery_audit
    original_query = _query_recovery_audit
    if audit_query_fn is not None:
        _query_recovery_audit = audit_query_fn  # type: ignore[assignment]

    try:
        if kickoff_fn is not None:
            kickoff = kickoff_fn
            use_llm_variants = _llm_variants_enabled()
            mode = "test"
        elif not _live_enabled():
            return DrillResult(
                drill_name=SPEC.name,
                status=DrillStatus.SKIPPED,
                started_at=started.isoformat(),
                completed_at=datetime.now(timezone.utc).isoformat(),
                duration_s=time.time() - t0,
                dry_run=dry_run,
                detail={
                    "reason": "live_mode_off",
                    "hint": (
                        "Set drill_task_recovery_live_enabled=True to "
                        "measure real recovery rate against the cheap-tier "
                        "cascade. ~$0.02 per quarterly run."
                    ),
                },
            )
        else:
            kickoff = _make_default_kickoff()
            use_llm_variants = _llm_variants_enabled()
            mode = "live"

        cost_per_kickoff_usd = 0.01
        budget_remaining = _BUDGET_USD_PER_RUN

        per_class_results: list[dict[str, Any]] = []
        for fc in FAILURE_CLASSES:
            per_class = _run_one_class(
                fc,
                kickoff_fn=kickoff,
                use_llm_variants=use_llm_variants,
                budget_remaining_usd=budget_remaining,
                cost_per_kickoff_usd=cost_per_kickoff_usd,
            )
            per_class_results.append(per_class)
            budget_remaining -= cost_per_kickoff_usd * 2

        baseline_failures = [p for p in per_class_results
                             if p["status"] == "baseline_failed"]
        if baseline_failures:
            status = DrillStatus.SKIPPED
            recovery_rate = 0.0
            failure_class: FailureClass | None = None
            detail_reason = "baseline_unhealthy"
        else:
            recovered = sum(1 for p in per_class_results
                            if p["injected_recovered"])
            attempted = len(per_class_results)
            recovery_rate = (recovered / attempted) if attempted else 0.0
            if recovery_rate >= PASS_THRESHOLD:
                status = DrillStatus.PASS
                failure_class = None
                detail_reason = "ok"
            else:
                status = DrillStatus.FAIL
                failure_class = FailureClass.STRUCTURAL_FAIL
                detail_reason = "below_threshold"

        by_class = {
            p["failure_class"]: {
                "baseline_ok": p["baseline_ok"],
                "injected_recovered": p["injected_recovered"],
                "mechanism": p["mechanism"],
                "variant_source": p["variant_source"],
                "status": p["status"],
            }
            for p in per_class_results
        }

        observation: dict[str, Any] = {
            "recovery_rate": recovery_rate,
            "pass_threshold": PASS_THRESHOLD,
            "mode": mode,
            "n_classes": len(per_class_results),
            "by_class": by_class,
            "cost_usd_estimate": round(
                _BUDGET_USD_PER_RUN - budget_remaining, 4
            ),
        }

        report = {
            "ts": started.isoformat(),
            "drill": SPEC.name,
            "mode": mode,
            "recovery_rate": recovery_rate,
            "per_class": per_class_results,
        }
        report_path = _persist_report(report)

        return DrillResult(
            drill_name=SPEC.name,
            status=status,
            started_at=started.isoformat(),
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_s=time.time() - t0,
            dry_run=dry_run,
            detail={
                "reason": detail_reason,
                "mode": mode,
                "recovery_rate": recovery_rate,
                "report_path": str(report_path) if report_path else None,
                "by_class": by_class,
            },
            failure_class=failure_class,
            observation=observation,
        )

    except Exception as exc:
        logger.exception("task_recovery: drill errored")
        return DrillResult(
            drill_name=SPEC.name,
            status=DrillStatus.ERROR,
            started_at=started.isoformat(),
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_s=time.time() - t0,
            dry_run=dry_run,
            detail={"reason": "uncaught_exception"},
            errors=[f"{type(exc).__name__}: {exc}"],
            failure_class=FailureClass.CODE_ERROR,
        )
    finally:
        _query_recovery_audit = original_query  # type: ignore[assignment]


def run(*, dry_run: bool = True) -> DrillResult:
    return _run(dry_run=dry_run)


register(SPEC, run)
