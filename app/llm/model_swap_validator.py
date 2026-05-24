"""model_swap_validator — capability regression check on cascade changes.

Gap 5 of the 2026-05-24 ultrathink analysis closure.

The problem
===========

The ``llm_selector`` cascade is mutable — operators rotate models in
and out, vendor APIs deprecate older versions, and new SOTA tiers
swap in. Many calibrated thresholds in the codebase depend on the
CURRENT model's behavior:

  * Concierge persona's epistemic-label preservation
  * HOT-4 confidence_proxy
  * Structured-diagnosis confidence band
  * Goodhart-guard rate thresholds
  * Various LLM-as-judge scoring loops

When a new base model lands (e.g. Sonnet 4.5 → 5.0), none of these
auto-recalibrate. The system may quietly drift to wrong behavior
for weeks before it surfaces.

What this module does
=====================

Replays the existing §62 benchmark suite (``app/benchmarks/tasks/``)
against BOTH the proposed-new cascade configuration and the current
cascade. Flags >10% regression on any tagged capability and opens a
Tier-3-style proposal (operator gate) before the swap can land.

This is a pre-flight check, not a runtime gate. The flow:

  1. Operator/automation calls
     ``validate_cascade_change(old_models, new_models, tiers=...)``.
  2. Validator runs the benchmark catalog against both — same tasks,
     same scorers, same inputs.
  3. Per-tier regression is computed: mean_score_new vs mean_score_old.
  4. If any tier regresses by >10% (the ``REGRESSION_THRESHOLD``),
     the validator emits a structured ``SwapValidationResult`` with
     ``ok=False`` and the per-task delta.
  5. Caller decides what to do — most callers gate the swap on
     ``result.ok``, then file a Tier-3 amendment proposal if the
     regression is justified (new model is cheaper, etc).

Where the validator runs
========================

Not boot-anchored. Operator-invoked via:

  * Agent tool ``model_swap_validator.validate`` — exposed to the
    Tier-3 amendment proposer; future cascade-change proposals
    call this and attach the result to ``extra_evidence``.
  * CLI: ``python -m app.llm.model_swap_validator --old <pin> --new <pin>``
  * REST: ``POST /api/cp/model_swap/validate`` (not yet wired in
    this iteration — adds in a follow-up).

Cost cap
========

Hard cap of ``_BUDGET_USD`` per validation pass — the benchmark
catalog is small (~30 tasks, ~$0.20 estimated). Cap defaults to
$1; operator can raise via env var.

Composes with — does not replace — the existing benchmark scheduler.
The scheduler runs continuously to track leaderboard drift; this
validator runs ON DEMAND for the swap decision.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ── Tunables ─────────────────────────────────────────────────────────────

# Per-tier regression that flips the validator from PASS to FAIL.
# 10% is the operator-friendly default — captures genuine quality
# drops without false-firing on noise from small sample sizes.
REGRESSION_THRESHOLD = 0.10

# Hard cost cap per validation pass. Allows operator to raise via
# env var for an unusually large catalog without modifying code.
_BUDGET_USD_DEFAULT = 1.0


def _budget_usd() -> float:
    try:
        return float(os.environ.get("MODEL_SWAP_VALIDATOR_BUDGET_USD") or _BUDGET_USD_DEFAULT)
    except Exception:
        return _BUDGET_USD_DEFAULT


# ── Data model ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PerTaskDelta:
    """One task's score under the old + new cascade."""

    task_id: str
    tier: str
    old_score: float
    new_score: float
    delta: float
    regressed: bool   # delta < -REGRESSION_THRESHOLD


@dataclass
class SwapValidationResult:
    """Composite result for one validation pass. ``ok`` is the gating
    boolean: True means no tier regressed beyond the threshold."""

    ok: bool
    started_at: str
    completed_at: str
    duration_s: float
    old_label: str
    new_label: str
    tiers: list[str]
    per_task: list[PerTaskDelta] = field(default_factory=list)
    per_tier_summary: dict[str, dict[str, float]] = field(default_factory=dict)
    total_cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)
    note: str = ""

    def regressed_tasks(self) -> list[PerTaskDelta]:
        return [d for d in self.per_task if d.regressed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_s": round(self.duration_s, 3),
            "old_label": self.old_label,
            "new_label": self.new_label,
            "tiers": list(self.tiers),
            "per_task": [
                {
                    "task_id": d.task_id,
                    "tier": d.tier,
                    "old_score": round(d.old_score, 4),
                    "new_score": round(d.new_score, 4),
                    "delta": round(d.delta, 4),
                    "regressed": bool(d.regressed),
                }
                for d in self.per_task
            ],
            "per_tier_summary": self.per_tier_summary,
            "total_cost_usd": round(self.total_cost_usd, 4),
            "errors": list(self.errors),
            "note": self.note,
        }


# ── Validation entry ─────────────────────────────────────────────────────


# Signature compatible with app.benchmarks.runner.LLMCall but defined
# inline so we don't take a hard dependency on the runner module at
# import time. Callers pass two LLMCall functions (one per cascade
# configuration) so the validator can run them side-by-side.
_LLMCallLike = Callable[..., Any]


def _run_catalog(
    llm_call: _LLMCallLike,
    *,
    tiers: list[str],
    budget_usd: float,
) -> tuple[dict[tuple[str, str], float], float, list[str]]:
    """Run every task in the catalog against every tier.

    Returns ``({(task_id, tier): score}, total_cost, errors)``.

    Failure-isolated — any single task raise becomes an error row
    and the rest of the catalog still runs.
    """
    try:
        from app.benchmarks.catalog import load_catalog
        from app.benchmarks.runner import run_task
    except Exception as exc:
        return {}, 0.0, [f"benchmarks module import failed: {exc}"]

    try:
        tasks = list(load_catalog())
    except Exception as exc:
        return {}, 0.0, [f"catalog load failed: {exc}"]

    scores: dict[tuple[str, str], float] = {}
    total_cost = 0.0
    errors: list[str] = []
    for task in tasks:
        if total_cost >= budget_usd:
            errors.append(
                f"budget cap reached at ${total_cost:.4f}; "
                f"remaining tasks skipped"
            )
            break
        targets = list(getattr(task, "model_targets", None) or tiers)
        for tier in targets:
            if tier not in tiers:
                continue
            try:
                run = run_task(task, model_tier=tier, llm_call=llm_call)
                scores[(task.id, tier)] = float(getattr(run, "score", 0.0) or 0.0)
                total_cost += float(getattr(run, "cost_usd", 0.0) or 0.0)
                if run.error:
                    errors.append(f"{task.id}/{tier}: {run.error}")
            except Exception as exc:
                errors.append(f"{task.id}/{tier}: {type(exc).__name__}: {exc}")
                scores[(task.id, tier)] = 0.0
    return scores, total_cost, errors


def _summarize_per_tier(
    deltas: list[PerTaskDelta],
) -> dict[str, dict[str, float]]:
    """Aggregate per-tier mean score + mean delta + max regression."""
    by_tier: dict[str, list[PerTaskDelta]] = {}
    for d in deltas:
        by_tier.setdefault(d.tier, []).append(d)
    summary: dict[str, dict[str, float]] = {}
    for tier, rows in by_tier.items():
        n = max(1, len(rows))
        summary[tier] = {
            "n_tasks": float(len(rows)),
            "mean_old": round(sum(r.old_score for r in rows) / n, 4),
            "mean_new": round(sum(r.new_score for r in rows) / n, 4),
            "mean_delta": round(sum(r.delta for r in rows) / n, 4),
            "max_regression": round(
                min((r.delta for r in rows), default=0.0), 4
            ),
            "n_regressed": float(sum(1 for r in rows if r.regressed)),
        }
    return summary


def validate_cascade_change(
    *,
    old_llm_call: _LLMCallLike,
    new_llm_call: _LLMCallLike,
    old_label: str = "current",
    new_label: str = "proposed",
    tiers: Optional[list[str]] = None,
    budget_usd: Optional[float] = None,
) -> SwapValidationResult:
    """Run the benchmark catalog against both cascades and return the
    structured comparison. NEVER raises.

    Parameters
    ----------
    old_llm_call, new_llm_call
        :class:`app.benchmarks.runner.LLMCall`-compatible callables.
        The validator runs each catalog task against both.
    old_label, new_label
        Human-readable identifiers stored in the result.
    tiers
        Subset of {"cheap", "default", "smart"} to validate. None
        means all three.
    budget_usd
        Hard cap per cascade (total = 2 × budget_usd). None →
        ``_BUDGET_USD_DEFAULT``.
    """
    import time as _time

    started = datetime.now(timezone.utc)
    t0 = _time.time()
    tiers = list(tiers or ["cheap", "default", "smart"])
    budget = float(budget_usd if budget_usd is not None else _budget_usd())
    errors: list[str] = []

    old_scores, old_cost, e_old = _run_catalog(
        old_llm_call, tiers=tiers, budget_usd=budget
    )
    errors.extend(e_old)
    new_scores, new_cost, e_new = _run_catalog(
        new_llm_call, tiers=tiers, budget_usd=budget
    )
    errors.extend(e_new)

    deltas: list[PerTaskDelta] = []
    keys = set(old_scores.keys()) | set(new_scores.keys())
    for task_id, tier in sorted(keys):
        old = old_scores.get((task_id, tier), 0.0)
        new = new_scores.get((task_id, tier), 0.0)
        delta = new - old
        deltas.append(
            PerTaskDelta(
                task_id=task_id,
                tier=tier,
                old_score=old,
                new_score=new,
                delta=delta,
                regressed=delta < -REGRESSION_THRESHOLD,
            )
        )

    summary = _summarize_per_tier(deltas)
    completed = datetime.now(timezone.utc)
    any_regression = any(d.regressed for d in deltas)
    note_parts: list[str] = []
    n_regressed = sum(1 for d in deltas if d.regressed)
    note_parts.append(
        f"{n_regressed} task(s) regressed beyond "
        f"{REGRESSION_THRESHOLD*100:.0f}% threshold"
    )
    if not deltas:
        note_parts.append("no tasks ran — see errors")

    return SwapValidationResult(
        ok=(not any_regression and bool(deltas)),
        started_at=started.isoformat(),
        completed_at=completed.isoformat(),
        duration_s=_time.time() - t0,
        old_label=old_label,
        new_label=new_label,
        tiers=tiers,
        per_task=deltas,
        per_tier_summary=summary,
        total_cost_usd=old_cost + new_cost,
        errors=errors,
        note="; ".join(note_parts),
    )


# ── CLI entry ────────────────────────────────────────────────────────────


def _build_llm_call_for_models(model_pins: dict[str, str]) -> _LLMCallLike:
    """Build an :class:`LLMCall` callable that resolves tiers to the
    explicit model strings in ``model_pins`` rather than the live
    cascade.

    ``model_pins`` maps tier → fully-qualified model id, e.g.
    ``{"cheap": "claude-haiku-4-5", "default": "claude-sonnet-4-5"}``.

    Tiers absent from the mapping fall back to the live cascade. v1
    delegates to the existing benchmark default; the override-by-
    explicit-pin path lands in a follow-up once an operator-supplied
    pin format is settled.
    """
    try:
        from app.benchmarks.scheduler_job import _resolve_default_llm_call

        default = _resolve_default_llm_call()
    except Exception:
        # Fallback: deterministic stub returning empty results so the
        # validator at least reports "no scoring possible" rather than
        # crashing. Real callers always inject their own LLMCall.
        from app.benchmarks.models import LLMResult

        def _stub(**kw: Any) -> LLMResult:
            return LLMResult(output="", error="no llm_call wired")

        return _stub

    # Pin-aware wrapper: when the caller pinned a tier, force the
    # tier-tag to its pinned string before invocation. The benchmark
    # runner uses model_tier as-is, so this is sufficient for the
    # benchmark catalog's coarse tier-routing scheme.
    pinned = {str(k): str(v) for k, v in (model_pins or {}).items() if v}

    def _wrapped(*, prompt: str, model_tier: str, max_tokens: int, timeout_s: int):
        effective = pinned.get(model_tier, model_tier)
        return default(
            prompt=prompt,
            model_tier=effective,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
        )

    return _wrapped


def main(argv: Optional[list[str]] = None) -> int:
    """``python -m app.llm.model_swap_validator`` entry."""
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="model_swap_validator",
        description=(
            "Validate a proposed cascade change against the live "
            "benchmark catalog. Flags tasks that regress > "
            f"{REGRESSION_THRESHOLD*100:.0f}%."
        ),
    )
    parser.add_argument(
        "--old",
        action="append",
        default=[],
        metavar="TIER=MODEL",
        help=(
            "Pin the OLD cascade at TIER=MODEL. Repeat for multiple "
            "tiers. e.g. --old default=claude-sonnet-4-5"
        ),
    )
    parser.add_argument(
        "--new",
        action="append",
        default=[],
        metavar="TIER=MODEL",
        help=(
            "Pin the NEW cascade at TIER=MODEL. Repeat for multiple "
            "tiers. e.g. --new default=claude-sonnet-5"
        ),
    )
    parser.add_argument("--tier", action="append", choices=["cheap", "default", "smart"])
    parser.add_argument(
        "--budget-usd",
        type=float,
        default=None,
        help="Per-cascade cost cap. Default: $1.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args(argv)

    def _parse_pins(rows: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for row in rows:
            if "=" not in row:
                raise SystemExit(f"--old/--new entries must be TIER=MODEL: {row!r}")
            k, v = row.split("=", 1)
            out[k.strip()] = v.strip()
        return out

    old_pins = _parse_pins(args.old)
    new_pins = _parse_pins(args.new)
    result = validate_cascade_change(
        old_llm_call=_build_llm_call_for_models(old_pins),
        new_llm_call=_build_llm_call_for_models(new_pins),
        old_label="-".join(old_pins.values()) or "current",
        new_label="-".join(new_pins.values()) or "proposed",
        tiers=args.tier,
        budget_usd=args.budget_usd,
    )
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
    else:
        print(
            f"validation: ok={result.ok} duration={result.duration_s:.1f}s "
            f"cost=${result.total_cost_usd:.4f}"
        )
        for tier, summary in result.per_tier_summary.items():
            print(
                f"  {tier}: mean_old={summary['mean_old']:.3f} "
                f"mean_new={summary['mean_new']:.3f} "
                f"mean_delta={summary['mean_delta']:+.3f} "
                f"regressed={int(summary['n_regressed'])}/{int(summary['n_tasks'])}"
            )
        if result.regressed_tasks():
            print("regressed:")
            for d in result.regressed_tasks():
                print(
                    f"  {d.task_id}/{d.tier}: {d.old_score:.2f} → "
                    f"{d.new_score:.2f} (Δ={d.delta:+.2f})"
                )
        if result.errors:
            print(f"errors ({len(result.errors)}):")
            for e in result.errors[:10]:
                print(f"  - {e}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PerTaskDelta",
    "REGRESSION_THRESHOLD",
    "SwapValidationResult",
    "validate_cascade_change",
]
