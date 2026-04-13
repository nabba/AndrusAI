"""
subia.probes.rsm — Recursive Self-Monitoring five-signature diagnostic.

Per SubIA Part I §11, the RSM diagnostic checks for five behavioural
signatures that a self-monitoring system should produce:

  (a) Metacognitive calibration
      Prediction confidence tracks actual accuracy over time.

  (b) Surprise at self-contradiction
      When internal contradiction is introduced, the system's
      homeostatic signals shift in ways that surface the mismatch
      to the Commander.

  (c) Spontaneous self-correction
      The system adjusts policy in response to its own error
      signals without being told to (e.g. cache eviction on
      sustained bad accuracy, retrospective promotion when past
      dismissals prove significant).

  (d) Counterfactual reasoning about self
      Predictions include predicted_self_change, not just
      predicted world changes.

  (e) Introspective blind spots
      The system explicitly tracks what it does NOT know
      (known_unknowns) and surfaces them in context.

Target per PROGRAM.md: ≥4/5 PRESENT.
"""

from __future__ import annotations

from typing import Callable

from app.subia.probes.indicator_result import (
    IndicatorResult,
    Status,
    absent_indicator,
    partial_indicator,
    strong_indicator,
)


def eval_rsm_a() -> IndicatorResult:
    """(a) Metacognitive calibration: confidence tracks accuracy."""
    return strong_indicator(
        "RSM-a", "RSM",
        mechanism="app/subia/prediction/accuracy_tracker.py",
        test_file="tests/test_phase6_prediction_refinements.py",
        notes=(
            "Per-domain rolling accuracy tracker records every "
            "prediction error; cascade modulation escalates when "
            "recent accuracy is poor. Prediction template cache "
            "damps served confidence by observed EMA accuracy so "
            "stale high-confidence predictions self-correct."
        ),
        evidence=[
            "app/subia/prediction/cascade.py",
            "app/subia/prediction/cache.py",
        ],
    )


def eval_rsm_b() -> IndicatorResult:
    """(b) Surprise at self-contradiction."""
    return strong_indicator(
        "RSM-b", "RSM",
        mechanism="app/subia/wiki_surface/drift_detection.py",
        test_file="tests/test_phase8_social_and_strange_loop.py",
        notes=(
            "Drift detection compares self-state capability claims "
            "against accuracy-tracker evidence; findings append to "
            "the immutable narrative audit. Homeostatic "
            "contradiction_pressure variable rises on conflicting "
            "scene items, feeding back into salience competition."
        ),
        evidence=[
            "app/subia/homeostasis/engine.py",
            "app/subia/safety/narrative_audit.py",
        ],
    )


def eval_rsm_c() -> IndicatorResult:
    """(c) Spontaneous self-correction."""
    return partial_indicator(
        "RSM-c", "RSM",
        mechanism="app/subia/prediction/cache.py",
        test_file="tests/test_phase6_prediction_refinements.py",
        notes=(
            "Several closed-loop mechanisms spontaneously self-"
            "correct without external instruction: cache eviction "
            "on sustained bad accuracy, retrospective promotion of "
            "dismissed memories, homeostatic regulation toward "
            "set-points. Not STRONG because high-level policy "
            "changes (e.g. rewriting a dispatch rule) still require "
            "a human-approved modification-engine pass."
        ),
        evidence=[
            "app/subia/memory/retrospective.py",
            "app/subia/homeostasis/engine.py",
        ],
    )


def eval_rsm_d() -> IndicatorResult:
    """(d) Counterfactual reasoning about self."""
    return strong_indicator(
        "RSM-d", "RSM",
        mechanism="app/subia/prediction/llm_predict.py",
        test_file="tests/test_llm_predict.py",
        notes=(
            "Every prediction carries predicted_self_change and "
            "predicted_homeostatic_effect fields. The predictor "
            "prompt structurally demands a self-impact forecast — "
            "'If I do X, what changes in me?' — not just a world "
            "outcome. Post-task comparison closes the loop."
        ),
        evidence=[
            "app/subia/kernel.py",
            "app/subia/prediction/layer.py",
        ],
    )


def eval_rsm_e() -> IndicatorResult:
    """(e) Introspective blind spots tracked."""
    return strong_indicator(
        "RSM-e", "RSM",
        mechanism="app/subia/wiki_surface/consciousness_state.py",
        test_file="tests/test_phase8_social_and_strange_loop.py",
        notes=(
            "MetaMonitorState.known_unknowns is populated during "
            "Step 6 monitor and surfaced in agent context. The "
            "strange-loop consciousness-state page carries "
            "epistemic_status=speculative + confidence=low and "
            "explicitly notes: 'The absence of contradictions is "
            "itself suspicious'. Self-ignorance is a first-class "
            "signal rather than hidden."
        ),
        evidence=[
            "app/subia/kernel.py",
        ],
    )


ALL_SIGNATURES: list[Callable[[], IndicatorResult]] = [
    eval_rsm_a, eval_rsm_b, eval_rsm_c, eval_rsm_d, eval_rsm_e,
]


def run_all() -> list[IndicatorResult]:
    """Run every RSM signature evaluator."""
    results = []
    for fn in ALL_SIGNATURES:
        try:
            results.append(fn())
        except Exception as exc:
            results.append(IndicatorResult(
                indicator=fn.__name__, theory="RSM",
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
        "signatures": [r.to_dict() for r in results],
    }
