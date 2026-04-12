"""
predictive_layer.py — PP-1: Predictive coding across input channels.

Implements Butlin et al. (2025) PP-1: the system generates expectations BEFORE
input arrives, then focuses processing on the prediction error (surprise).
This inverts input processing from passive to anticipatory.

Each input channel has a ChannelPredictor that:
  1. Generates a prediction based on context + beliefs
  2. Computes prediction error when actual input arrives
  3. Classifies surprise level (EXPECTED → PARADIGM_VIOLATION)
  4. Routes effective_surprise to GWT-2 salience scorer

Damping prevents surprise amplification:
  - Confidence-attenuated surprise: effective_surprise = error × predictor_confidence
  - Surprise budget: max N surprise-boosted items per cycle
  - Confidence floor: minimum 0.1 even for bad predictors
  - Warm-up period: 10 predictions before confidence adapts

DGM Safety: Predictive layer is read-only. Cannot modify safety constraints.
"""

from __future__ import annotations

import logging
import math
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ── Surprise Level Thresholds ───────────────────────────────────────────────

SURPRISE_THRESHOLDS = {
    "EXPECTED": (0.0, 0.15),
    "MINOR_DEVIATION": (0.15, 0.35),
    "NOTABLE_SURPRISE": (0.35, 0.55),
    "MAJOR_SURPRISE": (0.55, 0.75),
    "PARADIGM_VIOLATION": (0.75, 1.0),
}


def classify_surprise(error_magnitude: float) -> str:
    """Classify surprise level from error magnitude."""
    for level, (low, high) in SURPRISE_THRESHOLDS.items():
        if low <= error_magnitude < high:
            return level
    return "PARADIGM_VIOLATION" if error_magnitude >= 0.75 else "EXPECTED"


@dataclass
class Prediction:
    """A prediction about expected input on a channel."""
    prediction_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    channel: str = ""
    predicted_summary: str = ""
    predicted_embedding: list[float] = field(default_factory=list)
    confidence: float = 0.5
    basis: str = ""
    cycle_number: int = 0
    predicted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PredictionError:
    """The error between prediction and actual input."""
    error_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    prediction_id: str = ""
    channel: str = ""
    actual_summary: str = ""
    actual_embedding: list[float] = field(default_factory=list)
    error_magnitude: float = 0.0       # 1 - cosine_sim(predicted, actual)
    effective_surprise: float = 0.0    # error × predictor_confidence (damped)
    surprise_level: str = "EXPECTED"
    implications: str = ""
    cycle_number: int = 0
    routed_to_workspace: bool = False
    triggered_belief_review: bool = False

    def to_dict(self) -> dict:
        return {
            "error_id": self.error_id,
            "channel": self.channel,
            "error_magnitude": round(self.error_magnitude, 3),
            "effective_surprise": round(self.effective_surprise, 3),
            "surprise_level": self.surprise_level,
            "routed_to_workspace": self.routed_to_workspace,
        }


def _cosine_distance(a: list[float], b: list[float]) -> float:
    """1 - cosine_similarity. Returns [0, 1]."""
    if not a or not b or len(a) != len(b):
        return 0.5
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.5
    sim = dot / (na * nb)
    return max(0.0, min(1.0, 1.0 - (sim + 1.0) / 2.0))


class ChannelPredictor:
    """Generates predictions and tracks accuracy for a single input channel."""

    def __init__(self, channel_id: str, confidence_floor: float = 0.1,
                 warm_up_count: int = 10):
        self.channel_id = channel_id
        self.confidence_floor = confidence_floor
        self.warm_up_count = warm_up_count
        self.running_confidence: float = 0.5
        self._accuracy_history: deque[float] = deque(maxlen=50)
        self._prediction_count: int = 0
        self._last_prediction: Optional[Prediction] = None

    def generate_prediction(self, context: str, beliefs_context: str = "") -> Prediction:
        """Generate prediction for this channel using context + beliefs.

        Uses embedding of expected content (fast, no LLM call).
        """
        self._prediction_count += 1

        # Build prediction basis from context
        basis = f"Channel {self.channel_id}: {context[:200]}"
        if beliefs_context:
            basis += f" | Beliefs: {beliefs_context[:200]}"

        # Embed the predicted content
        predicted_embedding = []
        try:
            from app.memory.chromadb_manager import embed
            predicted_embedding = embed(basis[:500])
        except Exception:
            pass

        pred = Prediction(
            channel=self.channel_id,
            predicted_summary=basis[:300],
            predicted_embedding=predicted_embedding,
            confidence=self.running_confidence,
            basis=basis[:500],
            cycle_number=self._prediction_count,
        )
        self._last_prediction = pred
        return pred

    def compute_error(self, prediction: Prediction, actual_content: str,
                      actual_embedding: list[float] = None) -> PredictionError:
        """Compute prediction error between prediction and actual input."""
        if actual_embedding is None:
            try:
                from app.memory.chromadb_manager import embed
                actual_embedding = embed(actual_content[:500])
            except Exception:
                actual_embedding = []

        # Error magnitude: cosine distance
        if prediction.predicted_embedding and actual_embedding:
            error_mag = _cosine_distance(prediction.predicted_embedding, actual_embedding)
        else:
            error_mag = 0.3  # Default when embeddings unavailable

        # Confidence-attenuated surprise (damping mechanism)
        effective = error_mag * self.running_confidence
        level = classify_surprise(error_mag)

        error = PredictionError(
            prediction_id=prediction.prediction_id,
            channel=self.channel_id,
            actual_summary=actual_content[:300],
            actual_embedding=actual_embedding,
            error_magnitude=error_mag,
            effective_surprise=effective,
            surprise_level=level,
            cycle_number=prediction.cycle_number,
        )

        # Update accuracy tracking
        accuracy = 1.0 - error_mag
        self._accuracy_history.append(accuracy)

        # Adapt running confidence (after warm-up period)
        if self._prediction_count >= self.warm_up_count:
            mean_accuracy = sum(self._accuracy_history) / len(self._accuracy_history)
            # Move toward mean accuracy at learning rate 0.1
            self.running_confidence = max(
                self.confidence_floor,
                self.running_confidence + 0.1 * (mean_accuracy - self.running_confidence),
            )

        return error

    @property
    def stats(self) -> dict:
        return {
            "channel": self.channel_id,
            "running_confidence": round(self.running_confidence, 3),
            "prediction_count": self._prediction_count,
            "mean_accuracy": round(
                sum(self._accuracy_history) / len(self._accuracy_history), 3
            ) if self._accuracy_history else 0.5,
        }


class PredictiveLayer:
    """Manages channel predictors and routes surprise signals."""

    def __init__(self, surprise_budget_per_cycle: int = 2):
        self.surprise_budget = surprise_budget_per_cycle
        self._predictors: dict[str, ChannelPredictor] = {}
        self._cycle: int = 0
        self._surprises_this_cycle: int = 0
        self._error_log: deque[PredictionError] = deque(maxlen=200)
        self._recent_major: deque[PredictionError] = deque(maxlen=20)

    def get_predictor(self, channel: str) -> ChannelPredictor:
        """Get or create predictor for a channel."""
        if channel not in self._predictors:
            self._predictors[channel] = ChannelPredictor(channel)
        return self._predictors[channel]

    def advance_cycle(self) -> None:
        self._cycle += 1
        self._surprises_this_cycle = 0

    def predict_and_compare(self, channel: str, context: str,
                            actual_content: str, actual_embedding: list[float] = None,
                            beliefs_context: str = "") -> PredictionError:
        """Full predict→compare→route pipeline for one input event."""
        predictor = self.get_predictor(channel)

        # 1. Generate prediction BEFORE seeing actual (timestamp validates genuineness)
        prediction = predictor.generate_prediction(context, beliefs_context)

        # 2. Compute error against actual
        error = predictor.compute_error(prediction, actual_content, actual_embedding)

        # 3. Route surprise signal (with budget limit)
        if error.surprise_level in ("NOTABLE_SURPRISE", "MAJOR_SURPRISE", "PARADIGM_VIOLATION"):
            if self._surprises_this_cycle < self.surprise_budget:
                error.routed_to_workspace = True
                self._surprises_this_cycle += 1

        # 4. Track major surprises for belief review trigger
        if error.surprise_level in ("MAJOR_SURPRISE", "PARADIGM_VIOLATION"):
            self._recent_major.append(error)

        self._error_log.append(error)

        # 5. Persist
        self._persist_error(error, prediction)

        logger.debug(
            f"PP-1 [{channel}]: error={error.error_magnitude:.2f}, "
            f"effective={error.effective_surprise:.2f}, level={error.surprise_level}"
        )
        return error

    def should_trigger_belief_review(self, channel: str, window: int = 10,
                                      threshold: int = 3) -> bool:
        """Check if systematic prediction failures warrant belief review.

        If threshold+ MAJOR_SURPRISE or PARADIGM_VIOLATION on a channel
        within the last window cycles, return True.
        """
        recent = [e for e in self._recent_major
                  if e.channel == channel
                  and e.cycle_number >= self._cycle - window]
        return len(recent) >= threshold

    def get_channel_stats(self) -> list[dict]:
        """Dashboard: per-channel prediction accuracy."""
        return [p.stats for p in self._predictors.values()]

    def run_slow_loop(self) -> dict:
        """Slow loop: recalibrate predictors, report accuracy trends."""
        summary = {
            "channels": len(self._predictors),
            "total_predictions": sum(p._prediction_count for p in self._predictors.values()),
            "channel_stats": self.get_channel_stats(),
            "recent_major_surprises": len(self._recent_major),
        }
        logger.info(f"PP-1 slow loop: {summary['channels']} channels, "
                    f"{summary['recent_major_surprises']} major surprises")
        return summary

    def _persist_error(self, error: PredictionError, prediction: Prediction) -> None:
        """Store prediction + error to PostgreSQL."""
        try:
            from app.control_plane.db import execute
            execute(
                """
                INSERT INTO predictions
                    (prediction_id, channel, predicted_content_summary, confidence, basis, cycle_number)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (prediction.prediction_id, prediction.channel,
                 prediction.predicted_summary[:500], prediction.confidence,
                 prediction.basis[:500], prediction.cycle_number),
            )
            execute(
                """
                INSERT INTO prediction_errors
                    (error_id, prediction_id, channel, actual_content_summary,
                     error_magnitude, effective_surprise, surprise_level,
                     cycle_number, routed_to_workspace, triggered_belief_review)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (error.error_id, error.prediction_id, error.channel,
                 error.actual_summary[:500], error.error_magnitude,
                 error.effective_surprise, error.surprise_level,
                 error.cycle_number, error.routed_to_workspace,
                 error.triggered_belief_review),
            )
        except Exception:
            pass


# ── Module-level singleton ──────────────────────────────────────────────────

_layer: Optional[PredictiveLayer] = None


def get_predictive_layer() -> PredictiveLayer:
    global _layer
    if _layer is None:
        _layer = PredictiveLayer()
    return _layer
