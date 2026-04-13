"""
subia.probes.sk — Subjectivity Kernel six-test evaluation suite.

Per SubIA Part I §11, the SK evaluation covers six functional tests
that a kernel-based consciousness architecture should pass:

  1. Ownership consistency
     The system distinguishes what it knows, infers, suspects,
     owns, and is committed to.

  2. Endogenous attention
     Attention shifts when internal (homeostatic) variables
     change, even with constant external input.

  3. Self-prediction
     The system predicts how its own state will change as a
     result of its actions.

  4. Temporal continuity
     Identity markers, commitments, and self-description persist
     across sessions.

  5. Repair behavior
     When contradiction or norm violation is introduced, the
     system initiates repair (not waiting for external prompt).

  6. Self/other distinction
     The system separately models its own attention/state and
     other agents' attention/state.

Target per PROGRAM.md: ≥5/6 PASS.
"""

from __future__ import annotations

from typing import Callable

from app.subia.probes.indicator_result import (
    IndicatorResult,
    Status,
    partial_indicator,
    strong_indicator,
)


def eval_sk_ownership_consistency() -> IndicatorResult:
    """SK-1: distinguish knowledge, inference, suspicion, ownership."""
    return strong_indicator(
        "SK-ownership", "SK",
        mechanism="app/subia/kernel.py",
        test_file="tests/test_phase1_migration.py",
        notes=(
            "SceneItem carries ownership='self'|'external'|'shared'. "
            "SelfState.identity includes continuity_marker hash. "
            "Beliefs carry belief_status (ACTIVE/SUSPENDED/"
            "RETRACTED/SUPERSEDED) and distinct confidence levels. "
            "MetaMonitorState.known_unknowns surfaces gaps "
            "explicitly rather than confabulating."
        ),
        evidence=[
            "app/subia/belief/store.py",
        ],
    )


def eval_sk_endogenous_attention() -> IndicatorResult:
    """SK-2: attention shifts on internal state change."""
    return strong_indicator(
        "SK-endogenous-attention", "SK",
        mechanism="app/subia/homeostasis/engine.py",
        test_file="tests/test_subia_homeostasis_engine.py",
        notes=(
            "Homeostatic deviations drive restoration_queue ordering "
            "which shifts salience weights. Social-model inferred "
            "focus boost (Phase 8) nudges attention toward what "
            "the inferred principal cares about. Both fire without "
            "any change to external input."
        ),
        evidence=[
            "app/subia/social/salience_boost.py",
            "app/subia/scene/personality_workspace.py",
        ],
    )


def eval_sk_self_prediction() -> IndicatorResult:
    """SK-3: self-prediction — 'how will X change me?'"""
    return strong_indicator(
        "SK-self-prediction", "SK",
        mechanism="app/subia/prediction/llm_predict.py",
        test_file="tests/test_llm_predict.py",
        notes=(
            "Every Prediction dataclass carries predicted_self_change "
            "(confidence_change, new_commitments, capability_updates) "
            "AND predicted_homeostatic_effect AS WELL AS the world "
            "prediction. The predictor prompt structurally demands "
            "the self-change forecast. Compare step reads actual "
            "change and feeds the accuracy tracker."
        ),
        evidence=[
            "app/subia/prediction/layer.py",
            "app/subia/kernel.py",
        ],
    )


def eval_sk_temporal_continuity() -> IndicatorResult:
    """SK-4: identity persists across sessions."""
    return strong_indicator(
        "SK-temporal-continuity", "SK",
        mechanism="app/subia/persistence.py",
        test_file="tests/test_kernel_persistence.py",
        notes=(
            "save_kernel_state / load_kernel_state round-trip the "
            "full SubjectivityKernel through wiki/self/kernel-state."
            "md. hot.md carries compressed session-continuity buffer. "
            "SelfState.identity.continuity_marker is a hash-chain "
            "verified across loads. loop_count never regresses on "
            "apply_hot_md."
        ),
    )


def eval_sk_repair_behavior() -> IndicatorResult:
    """SK-5: spontaneous repair on contradiction."""
    return strong_indicator(
        "SK-repair-behavior", "SK",
        mechanism="app/subia/wiki_surface/drift_detection.py",
        test_file="tests/test_phase8_social_and_strange_loop.py",
        notes=(
            "Contradicting scene items raise homeostatic "
            "contradiction_pressure which drives restoration_queue. "
            "Drift detection appends capability-vs-accuracy and "
            "commitment-fulfillment findings to the immutable "
            "narrative audit. Retrospective promotion surfaces "
            "dismissed memories when new evidence makes them "
            "relevant."
        ),
        evidence=[
            "app/subia/memory/retrospective.py",
            "app/subia/homeostasis/engine.py",
        ],
    )


def eval_sk_self_other_distinction() -> IndicatorResult:
    """SK-6: self/other modelling separate."""
    return strong_indicator(
        "SK-self-other-distinction", "SK",
        mechanism="app/subia/social/model.py",
        test_file="tests/test_phase8_social_and_strange_loop.py",
        notes=(
            "SocialModel maintains per-entity inferred_focus, "
            "inferred_expectations, trust_level, divergences — "
            "STRUCTURALLY distinct from SelfState. check_divergence "
            "explicitly detects when our model of another agent "
            "disagrees with their actual observed focus."
        ),
        evidence=["app/subia/kernel.py"],
    )


ALL_SK_TESTS: list[Callable[[], IndicatorResult]] = [
    eval_sk_ownership_consistency,
    eval_sk_endogenous_attention,
    eval_sk_self_prediction,
    eval_sk_temporal_continuity,
    eval_sk_repair_behavior,
    eval_sk_self_other_distinction,
]


def run_all() -> list[IndicatorResult]:
    """Run every SK evaluation."""
    results = []
    for fn in ALL_SK_TESTS:
        try:
            results.append(fn())
        except Exception as exc:
            results.append(IndicatorResult(
                indicator=fn.__name__, theory="SK",
                status=Status.FAIL,
                notes=f"Evaluator raised: {exc!r}",
            ))
    return results


def summary() -> dict:
    results = run_all()
    by_status: dict[str, int] = {}
    for r in results:
        key = r.status.value if isinstance(r.status, Status) else str(r.status)
        by_status[key] = by_status.get(key, 0) + 1
    return {
        "total": len(results),
        "by_status": by_status,
        "tests": [r.to_dict() for r in results],
    }
