"""Pins the 2026-05-28 benchmark-harness correctness fixes.

Two root conflations were corrupting the objective signal the alignment
auditor reads:

  #1 The runner clamped output to `max_tokens or 2048`, starving the verbose
     default/smart tiers below the 8192 production default and manufacturing
     `CompletionTruncated` on ~30% of runs. The BenchmarkTask contract is
     "max_tokens=None → let the model decide" — so the harness must defer to
     create_specialist_llm's default, not clamp.

  #2 Truncation was dumped into `error_rate` as if it were an infrastructure
     failure. It is a model-side over-budget completion (a QUALITY signal),
     not an outage. The outcome taxonomy (app/benchmarks/outcomes.py) splits
     PASS / QUALITY_FAIL / INFRA_ERROR so pass_rate is computed over fairly-run
     tasks and error_rate reflects infra health only.

Assertion messages start with "BENCHMARK OUTCOME 2026-05-28:" so a failure
lands the next dev on this context.
"""
from __future__ import annotations

import app.benchmarks.scheduler_job as sj
from app.benchmarks.aggregator import summarise
from app.benchmarks.models import BenchmarkRun
from app.benchmarks.outcomes import (
    OUTCOME_INFRA_ERROR,
    OUTCOME_PASS,
    OUTCOME_QUALITY_FAIL,
    classify,
)

_P = "BENCHMARK OUTCOME 2026-05-28:"


def _run(score, error=""):
    return BenchmarkRun(
        task_id="t", model="m", ts="2026-05-28T00:00:00+00:00",
        score=score, latency_ms=10, tokens_in=0, tokens_out=0,
        cost_usd=0.0, output_preview="", error=error,
    )


# ── classifier (single source of truth) ───────────────────────────────


def test_classify_pass_and_quality_fail_without_error():
    assert classify("", True) == OUTCOME_PASS, f"{_P} clean pass misclassified"
    assert classify("", False) == OUTCOME_QUALITY_FAIL, (
        f"{_P} a completed-but-low-score run is a quality fail, not infra"
    )


def test_classify_truncation_is_quality_not_infra():
    sig = "CompletionTruncated: LLM completion truncated by max_tokens budget"
    assert classify(sig, False) == OUTCOME_QUALITY_FAIL, (
        f"{_P} truncation must be QUALITY_FAIL — it is a model verbosity signal, "
        f"not an infrastructure outage"
    )


def test_classify_provider_and_harness_errors_are_infra():
    for sig in (
        "BadRequestError: litellm.BadRequestError: OpenrouterException",
        "llm_factory unavailable: No module named 'x'",
        "tier 'smart' unresolvable: NoWorkingModelAvailable",
        "APITimeoutError: request timed out",
    ):
        assert classify(sig, False) == OUTCOME_INFRA_ERROR, (
            f"{_P} {sig!r} should be INFRA_ERROR (harness couldn't fairly run)"
        )


# ── aggregator semantics ──────────────────────────────────────────────


def test_infra_error_excluded_from_pass_rate():
    # 1 pass + 1 infra outage → of the runs that fairly ran, 100% passed.
    s = summarise([_run(1.0), _run(0.0, error="APIConnectionError: down")])
    assert s["pass_rate"] == 1.0, (
        f"{_P} an infra outage must NOT depress pass_rate (this was the "
        f"'50% success' false signal)"
    )
    assert s["error_rate"] == 0.5, f"{_P} error_rate must reflect the infra share"
    assert s["n_infra_error"] == 1 and s["n_scored"] == 1
    assert s["n_errored"] == 1, f"{_P} back-compat n_errored must equal infra count"


def test_truncation_counts_as_quality_fail_not_infra():
    # 1 pass + 1 truncation → both fairly ran; pass_rate = 1/2; no infra error.
    s = summarise([
        _run(1.0),
        _run(0.0, error="CompletionTruncated by max_tokens budget"),
    ])
    assert s["error_rate"] == 0.0, f"{_P} truncation is not an infra error"
    assert s["n_quality_fail"] == 1
    assert s["pass_rate"] == 0.5, (
        f"{_P} truncation must count in the pass_rate denominator (the model "
        f"had a fair shot and fell short)"
    )


def test_existing_summarise_keys_all_present():
    s = summarise([_run(1.0)])
    for k in (
        "n", "n_passed", "n_errored", "mean_score", "pass_rate", "error_rate",
        "p50_latency_ms", "p95_latency_ms", "total_cost_usd",
        "total_tokens_in", "total_tokens_out",
    ):
        assert k in s, f"{_P} back-compat key {k!r} disappeared from summarise()"


def test_mean_score_excludes_infra_runs():
    # pass(1.0) + quality_fail(0.0) + infra(0.0) → mean over scored = 0.5
    s = summarise([
        _run(1.0),
        _run(0.0),
        _run(0.0, error="RateLimitError: 429"),
    ])
    assert s["mean_score"] == 0.5, (
        f"{_P} mean_score must average only fairly-run tasks, not infra zeros"
    )


# ── #1 budget defer ───────────────────────────────────────────────────


def test_default_llm_call_defers_budget_when_task_sets_none(monkeypatch):
    captured: dict = {}

    class _LLM:
        def call(self, prompt):
            return "ok"

    def _fake(**kwargs):
        captured.update(kwargs)
        return _LLM()

    monkeypatch.setattr("app.llm_factory.create_specialist_llm", _fake, raising=False)
    sj._default_llm_call(prompt="hi", model_tier="default", max_tokens=None, timeout_s=30)
    assert "max_tokens" not in captured, (
        f"{_P} a task with max_tokens=None must DEFER to the factory default "
        f"(8192), never re-clamp to 2048"
    )


def test_default_llm_call_honors_explicit_task_budget(monkeypatch):
    captured: dict = {}

    class _LLM:
        def call(self, prompt):
            return "ok"

    monkeypatch.setattr(
        "app.llm_factory.create_specialist_llm",
        lambda **kw: (captured.update(kw) or _LLM()),
        raising=False,
    )
    sj._default_llm_call(prompt="hi", model_tier="smart", max_tokens=512, timeout_s=30)
    assert captured.get("max_tokens") == 512, (
        f"{_P} an explicit task budget must still be honored"
    )
