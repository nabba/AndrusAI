"""
attention_schema.py — AST-1: Modeling own attention state.

Implements Butlin et al. (2025) AST-1: the system maintains an internal model
of WHAT it's attending to, WHY, whether it SHOULD shift, and how accurate
its attentional predictions are.

Dual-timescale operation:
  Fast loop: real-time monitoring during workspace competition. Can intervene
             (suppress capturing item, boost neglected item) DURING gating.
  Slow loop: evaluate attention patterns over time. "Am I over-attending to X
             and neglecting Y? Are my attention predictions improving?"

DGM Safety: Schema recommendations are advisory. Cannot force workspace
modifications that violate DGM constraints.
"""

from __future__ import annotations

import logging
import math
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

@dataclass
class AttentionState:
    """Snapshot of current attentional allocation."""
    state_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    workspace_item_ids: list[str] = field(default_factory=list)
    salience_distribution: dict[str, float] = field(default_factory=dict)
    attending_because: str = ""
    attention_duration_s: float = 0.0
    cycle_number: int = 0
    source_trigger: str = "GOAL_DRIVEN"  # GOAL_DRIVEN | STIMULUS_DRIVEN | SCHEMA_DIRECTED
    is_stuck: bool = False
    is_captured: bool = False
    capturing_item_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "state_id": self.state_id,
            "workspace_items": len(self.workspace_item_ids),
            "is_stuck": self.is_stuck,
            "is_captured": self.is_captured,
            "source_trigger": self.source_trigger,
            "cycle": self.cycle_number,
        }

@dataclass
class AttentionPrediction:
    """Prediction of next workspace focus."""
    prediction_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    predicted_focus_ids: list[str] = field(default_factory=list)
    predicted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    actual_focus_ids: list[str] | None = None
    accuracy: float | None = None
    cycle_number: int = 0

@dataclass
class AttentionShift:
    """Record of an attention shift (schema-directed or otherwise)."""
    shift_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    trigger: str = ""        # stuck_detection | capture_detection | schema_recommendation | surprise_redirect
    shift_cost: float = 0.0
    utility_delta: float | None = None
    cooldown_until_cycle: int = 0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

class AttentionController:
    """Detects stuck/capture states and recommends shifts."""

    def __init__(self, stuck_threshold_cycles: int = 5,
                 capture_dominance_threshold: float = 0.70,
                 shift_cooldown_cycles: int = 3,
                 max_shifts_per_period: int = 2):
        self.stuck_threshold = stuck_threshold_cycles
        self.capture_threshold = capture_dominance_threshold
        self.shift_cooldown = shift_cooldown_cycles
        self.max_shifts = max_shifts_per_period
        self._shifts_this_period: int = 0
        self._cooldown_until: int = 0

    def detect_stuck(self, history: list[AttentionState]) -> bool:
        """True if workspace has same items for > threshold cycles without new actions."""
        if len(history) < self.stuck_threshold:
            return False
        recent = history[-self.stuck_threshold:]
        if not recent[0].workspace_item_ids:
            return False
        # Check if item IDs are substantially the same across recent cycles
        first_set = set(recent[0].workspace_item_ids)
        for state in recent[1:]:
            overlap = len(first_set & set(state.workspace_item_ids))
            if overlap < len(first_set) * 0.8:  # >20% changed = not stuck
                return False
        return True

    def detect_capture(self, state: AttentionState) -> tuple[bool, str | None]:
        """True if one item dominates salience distribution."""
        if not state.salience_distribution:
            return False, None
        total = sum(state.salience_distribution.values())
        if total == 0:
            return False, None
        for item_id, salience in state.salience_distribution.items():
            if salience / total > self.capture_threshold:
                return True, item_id
        return False, None

    def can_recommend_shift(self, current_cycle: int) -> bool:
        """Check cooldown and frequency limits."""
        if current_cycle < self._cooldown_until:
            return False
        if self._shifts_this_period >= self.max_shifts:
            return False
        return True

    def record_shift(self, current_cycle: int) -> AttentionShift:
        """Record a shift and set cooldown."""
        self._shifts_this_period += 1
        self._cooldown_until = current_cycle + self.shift_cooldown
        return AttentionShift(
            trigger="schema_recommendation",
            cooldown_until_cycle=self._cooldown_until,
        )

    def reset_period(self) -> None:
        """Reset shift counter for new slow-loop period."""
        self._shifts_this_period = 0

class AttentionPredictor:
    """Predicts next workspace focus and tracks accuracy."""

    def __init__(self, history_window: int = 20):
        self._predictions: deque[AttentionPrediction] = deque(maxlen=history_window)
        self._accuracy_history: deque[float] = deque(maxlen=50)

    def predict_next_focus(self, current_state: AttentionState) -> AttentionPrediction:
        """Predict which items will be in workspace next cycle.

        Heuristic: items with highest current salience likely persist.
        """
        if not current_state.salience_distribution:
            return AttentionPrediction(cycle_number=current_state.cycle_number + 1)

        # Top items by salience = predicted to persist
        sorted_items = sorted(
            current_state.salience_distribution.items(),
            key=lambda x: x[1], reverse=True,
        )
        predicted_ids = [item_id for item_id, _ in sorted_items[:5]]

        pred = AttentionPrediction(
            predicted_focus_ids=predicted_ids,
            cycle_number=current_state.cycle_number + 1,
        )
        self._predictions.append(pred)
        return pred

    def evaluate_prediction(self, prediction: AttentionPrediction,
                            actual_state: AttentionState) -> float:
        """Compare prediction to reality. Returns accuracy [0, 1]."""
        if not prediction.predicted_focus_ids or not actual_state.workspace_item_ids:
            return 0.5

        predicted = set(prediction.predicted_focus_ids)
        actual = set(actual_state.workspace_item_ids)
        if not predicted:
            return 0.5

        overlap = len(predicted & actual)
        accuracy = overlap / max(len(predicted), len(actual))
        prediction.actual_focus_ids = actual_state.workspace_item_ids
        prediction.accuracy = accuracy
        self._accuracy_history.append(accuracy)
        return accuracy

    @property
    def running_accuracy(self) -> float:
        if not self._accuracy_history:
            return 0.5
        return sum(self._accuracy_history) / len(self._accuracy_history)

class AttentionSchema:
    """Full attention schema: state tracking, prediction, control."""

    def __init__(self):
        self.controller = AttentionController()
        self.predictor = AttentionPredictor()
        self._history: deque[AttentionState] = deque(maxlen=50)
        self._current: AttentionState | None = None
        self._cycle: int = 0
        self._shifts: list[AttentionShift] = []

    def update(self, workspace_items: list, cycle: int = None) -> AttentionState:
        """Called on every workspace state change. Creates new AttentionState."""
        self._cycle = cycle or self._cycle + 1

        # Build salience distribution
        salience_dist = {}
        item_ids = []
        for item in workspace_items:
            salience_dist[item.item_id] = item.salience_score
            item_ids.append(item.item_id)

        # Determine attending_because
        if item_ids:
            top_item = max(workspace_items, key=lambda x: x.salience_score)
            reason = f"Highest salience: {top_item.content[:60]} ({top_item.salience_score:.2f})"
        else:
            reason = "Empty workspace"

        state = AttentionState(
            workspace_item_ids=item_ids,
            salience_distribution=salience_dist,
            attending_because=reason,
            cycle_number=self._cycle,
        )

        # Evaluate previous prediction
        if self._history:
            prev_pred = None
            for p in reversed(list(self.predictor._predictions)):
                if p.cycle_number == self._cycle:
                    prev_pred = p
                    break
            if prev_pred:
                self.predictor.evaluate_prediction(prev_pred, state)

        # Detect stuck
        history_list = list(self._history) + [state]
        state.is_stuck = self.controller.detect_stuck(history_list)

        # Detect capture
        captured, capturing_id = self.controller.detect_capture(state)
        state.is_captured = captured
        state.capturing_item_id = capturing_id

        # Generate next prediction
        self.predictor.predict_next_focus(state)

        self._history.append(state)
        self._current = state

        # Persist
        self._persist_state(state)

        return state

    def recommend_intervention(self) -> dict | None:
        """If stuck or captured, recommend intervention for workspace gate.

        Returns dict with suppression/boost directives, or None.
        """
        if not self._current:
            return None

        if not self.controller.can_recommend_shift(self._cycle):
            return None

        if self._current.is_captured and self._current.capturing_item_id:
            shift = self.controller.record_shift(self._cycle)
            self._shifts.append(shift)
            return {
                "action": "suppress",
                "target_item_id": self._current.capturing_item_id,
                "salience_reduction": 0.3,
                "reason": f"Capture detected: item dominates >{self.controller.capture_threshold*100:.0f}% of salience",
            }

        if self._current.is_stuck:
            shift = self.controller.record_shift(self._cycle)
            self._shifts.append(shift)
            return {
                "action": "boost_novelty",
                "reason": f"Stuck: same items for {self.controller.stuck_threshold}+ cycles",
            }

        return None

    def get_state_summary(self) -> dict:
        """Dashboard/introspection summary."""
        return {
            "cycle": self._cycle,
            "is_stuck": self._current.is_stuck if self._current else False,
            "is_captured": self._current.is_captured if self._current else False,
            "prediction_accuracy": round(self.predictor.running_accuracy, 3),
            "shifts_this_period": self.controller._shifts_this_period,
            "history_length": len(self._history),
            "workspace_size": len(self._current.workspace_item_ids) if self._current else 0,
        }

    def run_slow_loop(self) -> dict:
        """Slow loop: evaluate attention patterns, reset shift counter."""
        self.controller.reset_period()
        summary = self.get_state_summary()

        # Check for over-attention patterns
        if len(self._history) >= 10:
            # Count how often each source_channel appears
            channel_counts: dict[str, int] = {}
            for state in self._history:
                for item_id in state.workspace_item_ids:
                    channel_counts[item_id] = channel_counts.get(item_id, 0) + 1
            summary["dominant_items"] = sorted(
                channel_counts.items(), key=lambda x: x[1], reverse=True,
            )[:3]

        logger.info(f"AST-1 slow loop: accuracy={summary['prediction_accuracy']:.2f}, "
                    f"stuck={summary['is_stuck']}, captured={summary['is_captured']}")
        return summary

    def _persist_state(self, state: AttentionState) -> None:
        """Store attention state to PostgreSQL."""
        try:
            from app.control_plane.db import execute
            execute(
                """
                INSERT INTO attention_states
                    (state_id, workspace_item_ids, salience_distribution,
                     attending_because, cycle_number, source_trigger,
                     is_stuck, is_captured, capturing_item_id)
                VALUES (%s, %s::uuid[], %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    state.state_id,
                    state.workspace_item_ids,
                    __import__("json").dumps(state.salience_distribution),
                    state.attending_because[:500],
                    state.cycle_number,
                    state.source_trigger,
                    state.is_stuck,
                    state.is_captured,
                    state.capturing_item_id,
                ),
            )
        except Exception:
            pass

# ── Module-level singleton ──────────────────────────────────────────────────

_schema: AttentionSchema | None = None

def get_attention_schema() -> AttentionSchema:
    global _schema
    if _schema is None:
        _schema = AttentionSchema()
    return _schema
