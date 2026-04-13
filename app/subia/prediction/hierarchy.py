"""
prediction_hierarchy.py — 4-level hierarchical prediction error propagation.

Approximates Friston's hierarchical predictive processing within the
constraint of feedforward LLM calls. Errors flow BETWEEN levels
bidirectionally after each LLM round:

  Level 0 (Representation): prompt→response embedding distance
  Level 1 (Semantic): predicted vs actual response embedding
  Level 2 (Behavioral): response length/tools/hedging (LLMOutputPredictor)
  Level 3 (Meta): certainty prediction (HyperModel online buffer)

  Bottom-up: lower-level surprise reduces upper-level confidence
  Top-down: meta-uncertainty discounts lower-level surprise

This creates a closed loop across 4 levels — not within a single
inference, but across the sequence of LLM calls within a crew execution.

DGM Safety: prediction hierarchy is read-only with respect to safety.
Prediction errors cannot modify DGM evaluation functions.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Propagation thresholds (DGM immutable)
_BOTTOM_UP_THRESHOLD = 0.25   # Error must exceed this to propagate upward
_TOP_DOWN_FLOOR = 0.10        # Precision never drops below this (prevent full suppression)
_CONFIDENCE_FLOOR = 0.10      # Level confidence never drops below this
_MAX_HISTORY = 20


def _cosine_distance(a: list[float], b: list[float]) -> float:
    """1 - cosine_similarity. Returns [0, 1]. 0 = identical, 1 = orthogonal."""
    if not a or not b or len(a) != len(b):
        return 0.5  # Unknown → moderate distance
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.5
    sim = dot / (na * nb)
    return max(0.0, min(1.0, 1.0 - sim))


@dataclass
class HierarchyState:
    """Snapshot of all 4 prediction levels after one LLM round."""
    round_number: int = 0
    level0_error: float = 0.0      # Representation match error
    level1_error: float = 0.0      # Semantic prediction error
    level2_error: float = 0.0      # Behavioral prediction error
    level3_error: float = 0.0      # Meta-prediction error
    composite_surprise: float = 0.0  # Precision-weighted across all levels
    level_confidence: list[float] = field(default_factory=lambda: [0.5] * 4)
    precision_weights: list[float] = field(default_factory=lambda: [0.25] * 4)
    propagation_applied: bool = False

    def to_dict(self) -> dict:
        return {
            "round": self.round_number,
            "errors": [round(self.level0_error, 3), round(self.level1_error, 3),
                       round(self.level2_error, 3), round(self.level3_error, 3)],
            "composite_surprise": round(self.composite_surprise, 3),
            "confidence": [round(c, 3) for c in self.level_confidence],
            "precision": [round(p, 3) for p in self.precision_weights],
        }


class PredictionHierarchy:
    """4-level hierarchical prediction with inter-level error propagation.

    Per-agent singleton. Maintains running confidence per level and
    learns the prompt→response transform for Level 1 prediction.
    """

    _instances: dict[str, PredictionHierarchy] = {}

    def __init__(self, agent_id: str = ""):
        self.agent_id = agent_id
        self._history: deque[HierarchyState] = deque(maxlen=_MAX_HISTORY)
        self._round: int = 0

        # Per-level confidence: how reliable is this level's prediction?
        # Adapts via error propagation. Floor at _CONFIDENCE_FLOOR.
        self._level_confidence = [0.5, 0.5, 0.5, 0.5]

        # Top-down precision: how much to TRUST each level's error signal.
        # Modulated by meta-level (Level 3) confidence.
        self._precision = [0.25, 0.25, 0.25, 0.25]

        # Level 1 learning: running average of prompt→response embeddings
        # Used to PREDICT the response embedding from the prompt
        self._prompt_response_pairs: deque[tuple[list[float], list[float]]] = deque(maxlen=30)
        self._pending_prompt_embedding: list[float] = []
        self._pending_predicted_response: list[float] = []

    @classmethod
    def get_instance(cls, agent_id: str = "default") -> PredictionHierarchy:
        if agent_id not in cls._instances:
            cls._instances[agent_id] = cls(agent_id)
        return cls._instances[agent_id]

    # ── PRE_LLM_CALL: Generate predictions ────────────────────────────────

    def generate_predictions(self, prompt_text: str) -> dict:
        """Called before each LLM call. Generate predictions at Level 0+1.

        Level 0: embed the prompt (for comparison with response later)
        Level 1: predict what the response embedding will be
                 (using learned prompt→response transform)

        Returns dict with prediction metadata for storage in hook context.
        """
        self._round += 1

        try:
            from app.memory.chromadb_manager import embed
            prompt_emb = embed(prompt_text[:300])
        except Exception:
            prompt_emb = []

        self._pending_prompt_embedding = prompt_emb

        # Level 1: predict response embedding from prompt
        # Uses weighted average of recent prompt→response mappings
        predicted_response = self._predict_response_embedding(prompt_emb)
        self._pending_predicted_response = predicted_response

        return {
            "round": self._round,
            "prompt_embedded": bool(prompt_emb),
            "response_predicted": bool(predicted_response),
        }

    def _predict_response_embedding(self, prompt_emb: list[float]) -> list[float]:
        """Level 1: predict the response embedding from prompt context.

        Uses a simple associative model: weighted average of recent
        response embeddings, weighted by similarity to current prompt.
        This learns the prompt→response TRANSFORM, not just raw similarity.
        """
        if not prompt_emb or len(self._prompt_response_pairs) < 3:
            return []

        # Weight recent response embeddings by prompt similarity
        weighted_response = [0.0] * len(prompt_emb)
        total_weight = 0.0

        for past_prompt, past_response in self._prompt_response_pairs:
            if not past_response or len(past_response) != len(prompt_emb):
                continue
            sim = 1.0 - _cosine_distance(prompt_emb, past_prompt)
            weight = max(0.01, sim)
            for i in range(len(weighted_response)):
                weighted_response[i] += past_response[i] * weight
            total_weight += weight

        if total_weight > 0:
            return [v / total_weight for v in weighted_response]
        return []

    # ── POST_LLM_CALL: Compare + propagate ────────────────────────────────

    def compare_and_propagate(
        self, response_text: str,
        level2_error: float = 0.0,
        level3_error: float = 0.0,
    ) -> HierarchyState:
        """Called after each LLM call. Compute errors at all 4 levels
        and propagate between them.

        Args:
            response_text: the LLM's response
            level2_error: from LLMOutputPredictor (behavioral)
            level3_error: from HyperModel online buffer (meta)

        Returns:
            HierarchyState with all errors + composite surprise
        """
        # Embed response
        response_emb: list[float] = []
        try:
            from app.memory.chromadb_manager import embed
            response_emb = embed(response_text[:300])
        except Exception:
            pass

        # Level 0: representation match (prompt vs response distance)
        level0_error = _cosine_distance(self._pending_prompt_embedding, response_emb)

        # Level 1: semantic prediction (predicted response vs actual response)
        if self._pending_predicted_response and response_emb:
            level1_error = _cosine_distance(self._pending_predicted_response, response_emb)
        else:
            level1_error = 0.3  # Default moderate error when no prediction available

        # Learn: store this prompt→response pair for future Level 1 predictions
        if self._pending_prompt_embedding and response_emb:
            self._prompt_response_pairs.append(
                (self._pending_prompt_embedding, response_emb)
            )

        # Build state
        state = HierarchyState(
            round_number=self._round,
            level0_error=level0_error,
            level1_error=level1_error,
            level2_error=level2_error,
            level3_error=level3_error,
            level_confidence=list(self._level_confidence),
            precision_weights=list(self._precision),
        )

        # ── Inter-level error propagation ─────────────────────────────
        self._propagate_errors(state)

        self._history.append(state)

        logger.debug(
            f"hierarchy [{self.agent_id}] round={self._round}: "
            f"errors=[{level0_error:.2f},{level1_error:.2f},{level2_error:.2f},{level3_error:.2f}] "
            f"composite={state.composite_surprise:.2f}"
        )
        return state

    def _propagate_errors(self, state: HierarchyState) -> None:
        """Bidirectional error propagation between levels.

        Bottom-up: lower-level surprise reduces upper-level confidence
        Top-down: meta-uncertainty discounts lower-level surprise (precision)
        """
        # ── Bottom-up: surprise flows upward ──────────────────────────
        # Level 0 surprise → reduces Level 1 confidence
        if state.level0_error > _BOTTOM_UP_THRESHOLD:
            reduction = state.level0_error * 0.3
            self._level_confidence[1] = max(
                _CONFIDENCE_FLOOR,
                self._level_confidence[1] * (1.0 - reduction),
            )

        # Level 1 surprise → reduces Level 2 confidence
        if state.level1_error > _BOTTOM_UP_THRESHOLD:
            reduction = state.level1_error * 0.25
            self._level_confidence[2] = max(
                _CONFIDENCE_FLOOR,
                self._level_confidence[2] * (1.0 - reduction),
            )

        # Level 2 surprise → reduces Level 3 confidence
        if state.level2_error > _BOTTOM_UP_THRESHOLD:
            reduction = state.level2_error * 0.2
            self._level_confidence[3] = max(
                _CONFIDENCE_FLOOR,
                self._level_confidence[3] * (1.0 - reduction),
            )

        # ── Top-down: meta-uncertainty discounts lower levels ─────────
        # If Level 3 (meta) has low confidence, lower levels' surprises
        # are less meaningful → reduce their precision weights
        meta_confidence = self._level_confidence[3]
        self._precision[0] = max(_TOP_DOWN_FLOOR, 0.25 * meta_confidence)
        self._precision[1] = max(_TOP_DOWN_FLOOR, 0.25 * meta_confidence)
        # Level 2+3 precision stays at base (not modulated by themselves)
        self._precision[2] = 0.25
        self._precision[3] = 0.25

        # ── Slow confidence recovery ──────────────────────────────────
        # Levels slowly recover confidence when errors are low
        for i in range(4):
            errors = [state.level0_error, state.level1_error,
                      state.level2_error, state.level3_error]
            if errors[i] < 0.15:  # Low error → confidence recovery
                self._level_confidence[i] = min(
                    0.95,
                    self._level_confidence[i] + 0.02,
                )

        # ── Composite surprise: precision-weighted sum ────────────────
        errors = [state.level0_error, state.level1_error,
                  state.level2_error, state.level3_error]
        state.composite_surprise = sum(
            err * prec for err, prec in zip(errors, self._precision)
        )
        state.level_confidence = list(self._level_confidence)
        state.precision_weights = list(self._precision)
        state.propagation_applied = True

    # ── Context injection for recurrence ──────────────────────────────

    def get_hierarchy_injection(self) -> str:
        """Compact string for injection into next LLM prompt (~30 tokens)."""
        if not self._history:
            return ""
        last = self._history[-1]
        return (
            f"[Hierarchy L0={last.level0_error:.2f} L1={last.level1_error:.2f} "
            f"composite={last.composite_surprise:.2f} "
            f"conf={','.join(f'{c:.1f}' for c in last.level_confidence[:2])}]"
        )

    def reset(self) -> None:
        """Reset for new crew execution."""
        self._round = 0
        self._pending_prompt_embedding = []
        self._pending_predicted_response = []
        # Don't clear history or confidence — they carry across crew executions

    def get_summary(self) -> dict:
        """Dashboard/introspection summary."""
        return {
            "agent_id": self.agent_id,
            "rounds": self._round,
            "level_confidence": [round(c, 3) for c in self._level_confidence],
            "precision_weights": [round(p, 3) for p in self._precision],
            "history_length": len(self._history),
            "prompt_response_pairs": len(self._prompt_response_pairs),
            "last_state": self._history[-1].to_dict() if self._history else None,
        }


# ── Module-level singleton ──────────────────────────────────────────────────

def get_prediction_hierarchy(agent_id: str = "default") -> PredictionHierarchy:
    return PredictionHierarchy.get_instance(agent_id)
