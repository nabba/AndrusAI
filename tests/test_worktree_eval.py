"""Tests for the immutable judgement core (app/self_improvement/worktree_eval.py).

The centrepiece is ``test_within_noise_quality_delta_is_not_improvement``: it
feeds the verdict the same kind of within-noise delta the OLD engine scored
``+0.0133`` and queued as borderline, and proves it returns NO_CHANGE — never
IMPROVED. The rest pin the verdict precedence + benchmark loader + judge seam.
"""
from __future__ import annotations

import json

import pytest

try:
    from app.self_improvement.worktree_eval import (
        CorrectnessResult,
        EvalThresholds,
        QualityResult,
        compute_verdict,
        judge_outputs,
        load_benchmark,
    )
except Exception as exc:  # pragma: no cover
    pytest.skip(f"app import unavailable: {exc}", allow_module_level=True)


# ── Verdict precedence ───────────────────────────────────────────────────────


def test_invariants_failed_always_rejects():
    v = compute_verdict(invariants_ok=False)
    assert v.verdict == "REJECT"
    assert not v.proposable


def test_correctness_regression_rejects_even_with_quality_gain():
    corr = CorrectnessResult(
        baseline_failed=frozenset(),
        candidate_failed=frozenset({"tests/test_x.py::test_a"}),
        ran=True,
    )
    qual = QualityResult(
        baseline_scores=(0.4, 0.4, 0.4, 0.4),
        candidate_scores=(0.9, 0.9, 0.9, 0.9),  # big quality gain...
    )
    v = compute_verdict(invariants_ok=True, correctness=corr, quality=qual)
    assert v.verdict == "REJECT"  # ...but a regression vetoes it
    assert "regression" in v.reason


def test_fixing_a_failing_test_is_improvement():
    corr = CorrectnessResult(
        baseline_failed=frozenset({"tests/test_x.py::test_a"}),
        candidate_failed=frozenset(),
        ran=True,
    )
    v = compute_verdict(invariants_ok=True, correctness=corr)
    assert v.verdict == "IMPROVED"
    assert v.correctness_delta == 1
    assert v.proposable


def test_clear_quality_improvement():
    qual = QualityResult(
        baseline_scores=(0.5, 0.5, 0.5, 0.5, 0.5, 0.5),
        candidate_scores=(0.7, 0.72, 0.68, 0.7, 0.71, 0.69),
    )
    v = compute_verdict(invariants_ok=True, quality=qual)
    assert v.verdict == "IMPROVED"
    assert v.quality_delta is not None and v.quality_delta > 0.05


def test_within_noise_quality_delta_is_not_improvement():
    """THE regression pin: a ~+0.012 mean delta with mixed per-task wins/losses
    is what the old engine wrongly kept. It must read as NO_CHANGE here."""
    qual = QualityResult(
        baseline_scores=(0.50, 0.50, 0.50, 0.50, 0.50, 0.50),
        candidate_scores=(0.52, 0.49, 0.55, 0.48, 0.53, 0.50),  # mean +~0.0117
    )
    v = compute_verdict(invariants_ok=True, quality=qual)
    assert v.verdict == "NO_CHANGE", v.to_dict()
    assert v.verdict != "IMPROVED"
    assert not v.proposable
    # delta is real but below the effect size — recorded, not rewarded.
    assert 0.0 < v.quality_delta < EvalThresholds().min_quality_effect


def test_consistent_quality_regression_rejects():
    qual = QualityResult(
        baseline_scores=(0.7, 0.7, 0.7, 0.7, 0.7),
        candidate_scores=(0.6, 0.61, 0.59, 0.6, 0.62),  # consistently worse, ≥ effect
    )
    v = compute_verdict(invariants_ok=True, quality=qual)
    assert v.verdict == "REJECT"


def test_too_few_quality_samples_are_ignored():
    qual = QualityResult(baseline_scores=(0.5, 0.5), candidate_scores=(0.9, 0.9))
    v = compute_verdict(invariants_ok=True, quality=qual)
    # Big delta but only 2 samples (< min_quality_samples) → not trusted; nothing
    # else measured → INVARIANTS_ONLY (operator decides), NOT IMPROVED.
    assert v.verdict == "INVARIANTS_ONLY"


def test_no_signal_is_invariants_only():
    v = compute_verdict(invariants_ok=True)
    assert v.verdict == "INVARIANTS_ONLY"
    assert v.proposable  # correctness proven; operator decides on value


def test_green_refactor_with_no_opportunity_is_invariants_only():
    corr = CorrectnessResult(
        baseline_failed=frozenset(), candidate_failed=frozenset(), ran=True
    )
    v = compute_verdict(invariants_ok=True, correctness=corr)
    assert v.verdict == "INVARIANTS_ONLY"  # nothing to fix, nothing to measure


def test_measured_opportunity_but_no_benefit_is_no_change():
    corr = CorrectnessResult(
        baseline_failed=frozenset({"tests/test_x.py::test_a"}),
        candidate_failed=frozenset({"tests/test_x.py::test_a"}),  # didn't fix it
        ran=True,
    )
    v = compute_verdict(invariants_ok=True, correctness=corr)
    assert v.verdict == "NO_CHANGE"
    assert not v.proposable


# ── Benchmark loader ─────────────────────────────────────────────────────────


def test_load_benchmark_matches_target_prefix(tmp_path):
    (tmp_path / "research.json").write_text(
        json.dumps(
            {
                "target_prefixes": ["app/crews/research_crew.py"],
                "tasks": [{"id": "t1", "input": "Q?", "rubric": "correct"}],
            }
        )
    )
    hit = load_benchmark("app/crews/research_crew.py", benchmarks_dir=tmp_path)
    assert len(hit) == 1 and hit[0]["id"] == "t1"

    miss = load_benchmark("app/crews/writer_crew.py", benchmarks_dir=tmp_path)
    assert miss == []


def test_load_benchmark_ignores_bad_json(tmp_path):
    (tmp_path / "broken.json").write_text("{not valid json")
    assert load_benchmark("app/anything.py", benchmarks_dir=tmp_path) == []


# ── Judge seam ───────────────────────────────────────────────────────────────


def test_judge_outputs_parses_scores_via_injected_call():
    tasks = [{"id": "t1", "input": "Q", "rubric": "r"}, {"id": "t2", "input": "Q2", "rubric": "r"}]
    scores = judge_outputs(tasks, ["ans1", "ans2"], _llm_call=lambda p: "0.83")
    assert scores == (0.83, 0.83)


def test_judge_outputs_length_mismatch_returns_empty():
    assert judge_outputs([{"id": "t1", "input": "Q"}], [], _llm_call=lambda p: "1.0") == ()
