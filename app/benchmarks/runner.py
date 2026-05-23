"""Benchmark runner (Phase C.3, 2026-05-22).

Executes one :class:`BenchmarkTask` against one model and persists a
:class:`BenchmarkRun` row. The runner is intentionally generic in how
it talks to the LLM — the caller injects a :class:`LLMCall` callable
that maps ``(prompt, model_tier, max_tokens) -> LLMResult``. That way:

  * Production uses the real cascade selector.
  * Tests use a deterministic stub returning canned strings.
  * Operator-initiated runs from the dashboard can pin to a specific
    model.

Failure mode design
───────────────────

The runner NEVER raises out to its caller. Every exception path
produces a :class:`BenchmarkRun` with ``score=0.0`` and an ``error``
field describing what went wrong. The leaderboard surfaces those as
"this model errored N% of runs against task X" so operators can spot
infrastructure issues, not just model quality.

Cost cap
────────

The scheduler-driven refresh applies a per-pass max-cost-USD guard so
a runaway leaderboard sweep can't drain budgets. The single-task
runner here doesn't enforce that cap — it's the pass-level scheduler
that needs the guard, since each task individually is cheap (well
under $0.01 for v1's prompts).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol

from app.benchmarks.models import BenchmarkRun, BenchmarkTask, LLMResult
from app.benchmarks.scorers import score as compute_score

logger = logging.getLogger(__name__)


# Public type alias for the LLM-call injection point.
class LLMCall(Protocol):
    """Contract the runner expects from whichever LLM-call function
    the caller wires in.

    Implementations should return a :class:`LLMResult` with ``output``
    populated (the model's text), and best-effort populate the
    cost/token/latency fields. On error, return ``LLMResult(output="",
    error="…")`` rather than raising.
    """

    def __call__(
        self,
        *,
        prompt: str,
        model_tier: str,
        max_tokens: Optional[int],
        timeout_s: int,
    ) -> LLMResult: ...


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _preview(text: str, max_chars: int = 200) -> str:
    """Truncate output for the JSONL preview field. Adds an ellipsis
    when truncated so the operator can tell."""
    if not isinstance(text, str):
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


def run_task(
    task: BenchmarkTask,
    *,
    model_tier: str,
    llm_call: LLMCall,
    model_label: Optional[str] = None,
) -> BenchmarkRun:
    """Execute ``task`` against ``model_tier`` and return the resulting
    run record. NEVER raises — failures land in ``run.error``.

    Parameters
    ----------
    task
        The task to run.
    model_tier
        One of ``"cheap"`` / ``"default"`` / ``"smart"``. Passed
        through to the llm_call.
    llm_call
        The :class:`LLMCall` callable that does the actual model
        invocation. The runner is agnostic to whether this is a
        real cascade, a test stub, or a one-shot operator call.
    model_label
        Optional override for the ``model`` field in the run record.
        Useful when ``llm_call`` resolves a tier to a specific model
        and the caller wants that in the record. Defaults to
        ``model_tier``.

    Returns
    -------
    BenchmarkRun
        A fully-populated run record. The caller decides whether to
        persist it (typically via :func:`store.append_run`).
    """
    label = model_label or model_tier
    started_mono = time.monotonic()

    # Step 1: call the model. Failure-isolated.
    try:
        result = llm_call(
            prompt=task.input,
            model_tier=model_tier,
            max_tokens=task.max_tokens,
            timeout_s=task.timeout_s,
        )
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started_mono) * 1000)
        logger.warning(
            "benchmarks.runner: %s on %s raised %s",
            task.id, label, exc,
        )
        return BenchmarkRun(
            task_id=task.id,
            model=label,
            ts=_now_iso(),
            score=0.0,
            latency_ms=elapsed_ms,
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            output_preview="",
            error=f"{type(exc).__name__}: {exc}",
        )

    # The contract is a LLMResult — but we tolerate a stub returning
    # the wrong shape rather than crashing the whole catalog pass.
    if not isinstance(result, LLMResult):
        elapsed_ms = int((time.monotonic() - started_mono) * 1000)
        logger.warning(
            "benchmarks.runner: %s on %s returned %s (expected LLMResult)",
            task.id, label, type(result).__name__,
        )
        return BenchmarkRun(
            task_id=task.id, model=label, ts=_now_iso(),
            score=0.0, latency_ms=elapsed_ms,
            tokens_in=0, tokens_out=0, cost_usd=0.0,
            output_preview="",
            error=f"llm_call returned {type(result).__name__}, "
                  f"expected LLMResult",
        )

    # Step 2: if the llm call itself reported an error, skip scoring
    # and surface the error in the run record.
    if result.error:
        return BenchmarkRun(
            task_id=task.id, model=label, ts=_now_iso(),
            score=0.0,
            latency_ms=result.latency_ms or int(
                (time.monotonic() - started_mono) * 1000,
            ),
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            cost_usd=result.cost_usd,
            output_preview=_preview(result.output),
            error=result.error,
        )

    # Step 3: score. The scorer is total — never raises.
    score_value = compute_score(
        task.scorer,
        result.output,
        task.expected,
        scorer_args=task.scorer_args,
    )

    return BenchmarkRun(
        task_id=task.id, model=label, ts=_now_iso(),
        score=score_value,
        latency_ms=result.latency_ms or int(
            (time.monotonic() - started_mono) * 1000,
        ),
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_usd=result.cost_usd,
        output_preview=_preview(result.output),
        error="",
    )


def run_task_against_all_targets(
    task: BenchmarkTask,
    *,
    llm_call: LLMCall,
) -> list[BenchmarkRun]:
    """Run ``task`` once against each of its ``model_targets``.

    The natural unit of work for a leaderboard pass: every tier the
    operator marked the task as meaningful against gets a fresh row.
    """
    return [
        run_task(task, model_tier=tier, llm_call=llm_call)
        for tier in task.model_targets
    ]


def run_catalog(
    tasks: list[BenchmarkTask],
    *,
    llm_call: LLMCall,
    persist: bool = True,
    max_cost_usd: Optional[float] = None,
) -> list[BenchmarkRun]:
    """Run every task in ``tasks`` against every target tier.

    Parameters
    ----------
    tasks
        Catalog snapshot to run. Caller decides when to refresh.
    llm_call
        The model-call function.
    persist
        When True (default), each run is appended to the store. Set
        False for dry-runs / unit tests.
    max_cost_usd
        Optional pass-level cost cap. When the running total of
        ``cost_usd`` across produced runs exceeds this, the catalog
        pass stops early. The partial result is still returned so the
        caller can see what was done.

    Returns
    -------
    list[BenchmarkRun]
        Every produced run, in the order they were executed.
    """
    from app.benchmarks.store import append_run as _append

    out: list[BenchmarkRun] = []
    spent = 0.0
    for task in tasks:
        for tier in task.model_targets:
            if max_cost_usd is not None and spent >= max_cost_usd:
                logger.info(
                    "benchmarks.runner: cost cap $%.4f reached after "
                    "%d runs — stopping pass",
                    max_cost_usd, len(out),
                )
                return out
            run = run_task(task, model_tier=tier, llm_call=llm_call)
            out.append(run)
            spent += run.cost_usd
            if persist:
                try:
                    _append(run)
                except Exception as exc:
                    logger.warning(
                        "benchmarks.runner: store.append_run failed for "
                        "%s/%s: %s — continuing",
                        task.id, tier, exc,
                    )
    return out


__all__ = [
    "LLMCall",
    "run_catalog",
    "run_task",
    "run_task_against_all_targets",
]
