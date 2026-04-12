"""
loop_closure.py — Beautiful Loop self-referential closure.

The system predicts its ENTIRE processing path (strategy selection,
certainty outcome, reality model consultation, somatic valence, AND
its own loop coherence) BEFORE processing, then compares after.

The self-referential property: `loop_coherence` depends on prediction
accuracy, but `predicted_loop_coherence` was itself a prediction. The
accuracy of THAT prediction affects the coherence, creating a fixed-point
that the system converges toward over multiple iterations.

This IS "catching its own tail" — the model contains a prediction of
its own modeling process, and the accuracy of that prediction determines
the next prediction.

References:
  Laukkonen, Friston & Chandaria (2025) — "A Beautiful Loop"
  The epistemic field "catches its own tail" when the system's
  predictions turn back on themselves, creating recursive self-reference.

DGM Safety: Loop closure is read-only observation. Cannot modify
safety constraints or processing order. Pure arithmetic, <0.5ms.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_MAX_HISTORY = 30
_FIXED_POINT_ITERATIONS = 3  # Iterations to approximate self-referential fixed point
_CONVERGENCE_ALPHA = 0.2     # EMA smoothing for running coherence


@dataclass
class ProcessingPathPrediction:
    """What the system predicts about its own processing path."""
    predicted_plan_type: str = "default"           # Which strategy type will win
    predicted_certainty: float = 0.5               # What certainty will be achieved
    predicted_somatic_valence: float = 0.0         # What emotional state will emerge
    predicted_fe_trend: str = "stable"             # Free energy trajectory
    predicted_loop_coherence: float = 0.5          # ← THE TAIL: prediction of own coherence


@dataclass
class LoopClosureState:
    """Complete loop state: prediction + actual + error + coherence."""
    prediction: ProcessingPathPrediction = field(default_factory=ProcessingPathPrediction)
    actual_plan_type: str = ""
    actual_certainty: float = 0.5
    actual_somatic_valence: float = 0.0
    actual_fe_trend: str = "stable"
    # Errors
    plan_error: float = 0.0
    certainty_error: float = 0.0
    valence_error: float = 0.0
    fe_trend_match: float = 0.0
    coherence_prediction_error: float = 0.0
    composite_error: float = 0.0
    # The self-referential measure
    loop_coherence: float = 0.5  # = 1 - composite_error (including coherence prediction error)

    def to_dict(self) -> dict:
        return {
            "plan_error": round(self.plan_error, 3),
            "certainty_error": round(self.certainty_error, 3),
            "valence_error": round(self.valence_error, 3),
            "coherence_pred_error": round(self.coherence_prediction_error, 3),
            "composite_error": round(self.composite_error, 3),
            "loop_coherence": round(self.loop_coherence, 3),
            "predicted_coherence": round(self.prediction.predicted_loop_coherence, 3),
        }


class LoopClosure:
    """Beautiful Loop self-referential closure mechanism.

    Predicts the entire processing path, then compares against actual
    outcome. The self-referential fixed point: the system predicts its
    own coherence, and the accuracy of that prediction affects the
    coherence it predicted.

    Singleton per agent.
    """

    _instances: dict[str, LoopClosure] = {}

    def __init__(self, agent_id: str = ""):
        self.agent_id = agent_id
        self._history: deque[LoopClosureState] = deque(maxlen=_MAX_HISTORY)
        self._running_coherence: float = 0.5  # Running average — used for self-reference
        self._pending_prediction: ProcessingPathPrediction | None = None
        # Track plan type frequency for prediction
        self._plan_history: deque[str] = deque(maxlen=20)
        self._certainty_history: deque[float] = deque(maxlen=20)
        self._valence_history: deque[float] = deque(maxlen=20)

    @classmethod
    def get_instance(cls, agent_id: str = "default") -> LoopClosure:
        if agent_id not in cls._instances:
            cls._instances[agent_id] = cls(agent_id)
        return cls._instances[agent_id]

    def predict_path(self, task_description: str = "",
                     hyper_model=None) -> ProcessingPathPrediction:
        """Predict entire processing path BEFORE execution.

        Pure arithmetic from history — no LLM calls, <0.1ms.
        """
        # Plan type: most frequent in recent history
        if self._plan_history:
            from collections import Counter
            plan_counts = Counter(self._plan_history)
            predicted_plan = plan_counts.most_common(1)[0][0]
        else:
            predicted_plan = "default"

        # Certainty: from HyperModel if available, else history average
        if hyper_model:
            predicted_certainty = hyper_model.predict_next_step()
        elif self._certainty_history:
            predicted_certainty = sum(self._certainty_history) / len(self._certainty_history)
        else:
            predicted_certainty = 0.5

        # Somatic valence: running average
        if self._valence_history:
            predicted_valence = sum(self._valence_history) / len(self._valence_history)
        else:
            predicted_valence = 0.0

        # FE trend: from HyperModel if available
        predicted_fe = "stable"
        if hyper_model and hyper_model.history:
            predicted_fe = hyper_model.history[-1].free_energy_trend

        # THE TAIL: predict our own loop coherence
        # This is self-referential — we predict how coherent this very
        # prediction will be, based on our running average of past coherences
        predicted_coherence = self._running_coherence

        prediction = ProcessingPathPrediction(
            predicted_plan_type=predicted_plan,
            predicted_certainty=round(predicted_certainty, 3),
            predicted_somatic_valence=round(predicted_valence, 3),
            predicted_fe_trend=predicted_fe,
            predicted_loop_coherence=round(predicted_coherence, 3),
        )
        self._pending_prediction = prediction
        return prediction

    def close_loop(
        self,
        actual_plan_type: str = "",
        actual_certainty: float = 0.5,
        actual_somatic_valence: float = 0.0,
        actual_fe_trend: str = "stable",
    ) -> LoopClosureState:
        """Close the loop: compare predictions against actuals.

        Includes the self-referential fixed-point computation:
        loop_coherence depends on coherence_prediction_error, which
        depends on loop_coherence. Resolved by iterating 3 times.
        """
        prediction = self._pending_prediction or ProcessingPathPrediction()

        # Record actuals in history for future predictions
        if actual_plan_type:
            self._plan_history.append(actual_plan_type)
        self._certainty_history.append(actual_certainty)
        self._valence_history.append(actual_somatic_valence)

        # Compute per-dimension errors
        plan_error = 0.0 if prediction.predicted_plan_type == actual_plan_type else 1.0
        certainty_error = abs(prediction.predicted_certainty - actual_certainty)
        valence_error = abs(prediction.predicted_somatic_valence - actual_somatic_valence)
        fe_match = 1.0 if prediction.predicted_fe_trend == actual_fe_trend else 0.0

        # Base errors (without self-referential coherence error)
        base_errors = [
            plan_error * 0.20,
            certainty_error * 0.30,
            valence_error * 0.15,
            (1.0 - fe_match) * 0.10,
        ]
        base_error_sum = sum(base_errors)

        # ── Self-referential fixed-point iteration ────────────────────
        # loop_coherence = 1 - mean(all_errors including coherence_pred_error)
        # coherence_pred_error = |predicted_coherence - loop_coherence|
        # These are mutually dependent → iterate to approximate fixed point
        coherence = 1.0 - base_error_sum / 0.75  # Initial estimate (scale to [0,1])
        coherence = max(0.0, min(1.0, coherence))

        for _ in range(_FIXED_POINT_ITERATIONS):
            coh_pred_error = abs(prediction.predicted_loop_coherence - coherence)
            # Coherence includes its own prediction error (weight 0.25)
            total_error = base_error_sum + coh_pred_error * 0.25
            coherence = max(0.0, min(1.0, 1.0 - total_error))

        # Final coherence prediction error (after convergence)
        coherence_prediction_error = abs(prediction.predicted_loop_coherence - coherence)
        composite_error = base_error_sum + coherence_prediction_error * 0.25

        # Update running coherence for NEXT prediction (the self-referential link)
        self._running_coherence = (
            (1.0 - _CONVERGENCE_ALPHA) * self._running_coherence
            + _CONVERGENCE_ALPHA * coherence
        )

        state = LoopClosureState(
            prediction=prediction,
            actual_plan_type=actual_plan_type,
            actual_certainty=actual_certainty,
            actual_somatic_valence=actual_somatic_valence,
            actual_fe_trend=actual_fe_trend,
            plan_error=plan_error,
            certainty_error=certainty_error,
            valence_error=valence_error,
            fe_trend_match=fe_match,
            coherence_prediction_error=coherence_prediction_error,
            composite_error=round(composite_error, 4),
            loop_coherence=round(coherence, 4),
        )
        self._history.append(state)
        self._pending_prediction = None

        logger.debug(
            f"loop_closure [{self.agent_id}]: "
            f"coherence={coherence:.3f} (predicted={prediction.predicted_loop_coherence:.3f}) "
            f"composite_error={composite_error:.3f}"
        )
        return state

    def get_convergence(self) -> float:
        """How close to fixed-point (0=divergent, 1=perfectly self-predicting)."""
        return self._running_coherence

    def get_summary(self) -> dict:
        """Dashboard/introspection summary."""
        return {
            "agent_id": self.agent_id,
            "running_coherence": round(self._running_coherence, 3),
            "history_length": len(self._history),
            "plan_history": list(self._plan_history)[-5:],
            "last_state": self._history[-1].to_dict() if self._history else None,
        }


# ── Module-level convenience ────────────────────────────────────────────────

def get_loop_closure(agent_id: str = "default") -> LoopClosure:
    return LoopClosure.get_instance(agent_id)
