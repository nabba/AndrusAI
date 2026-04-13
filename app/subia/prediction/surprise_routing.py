"""
subia.prediction.surprise_routing — PP-1 closure: route prediction
error into the GWT-2 scene as a competing WorkspaceItem.

Before Phase 2 this was a half-circuit: predictive_layer marked
`error.routed_to_workspace = True` but nothing ever consumed the
flag. This module closes the loop:

    PredictionError (NOTABLE/MAJOR/PARADIGM surprise)
        |
        v
    WorkspaceItem (high surprise_signal, high agent_urgency)
        |
        v
    CompetitiveGate.evaluate()  -> admitted / displaced / peripheral

This is the canonical Clark/Friston predictive processing flow:
prediction error drives the attentional bottleneck. Butlin et al. 2023
indicator PP-1 requires predictive coding to serve as input to
downstream modules; a flag-set-and-ignored does not qualify.

The module is pure plumbing — no LLM calls, bounded by deterministic
thresholds, and respects the existing per-cycle surprise budget set
by PredictiveLayer.

Infrastructure-level. Not agent-modifiable. See PROGRAM.md Phase 2.
"""

from __future__ import annotations

import logging
import uuid

from app.subia.prediction.layer import PredictionError
from app.subia.scene.buffer import CompetitiveGate, GateResult, WorkspaceItem

logger = logging.getLogger(__name__)


# Minimum effective_surprise below which we do not route even if the
# PredictiveLayer marked the error as routable. Prevents a pathological
# stream of marginally-notable errors from flooding the gate.
_MIN_ROUTABLE_SURPRISE = 0.25

# Urgency floor given to routed surprise items. The SalienceScorer
# weights surprise_signal at 0.25 already, so we also lift
# agent_urgency slightly so that truly major prediction errors
# genuinely compete with ordinary focal content.
_URGENCY_FOR_NOTABLE = 0.60
_URGENCY_FOR_MAJOR = 0.80
_URGENCY_FOR_PARADIGM = 0.95

# Novelty floor for routed surprise items — PP-1 errors ARE novelty
# by construction (prediction failed), so they participate in the
# scene's novelty budget.
_NOVELTY_FOR_SURPRISE = 0.80


def route_surprise_to_gate(
    error: PredictionError,
    gate: CompetitiveGate,
    context: str = "",
    content_embedding: list[float] | None = None,
) -> GateResult | None:
    """Convert a routable prediction error into a WorkspaceItem and
    submit it to the gate. Returns the GateResult, or None if the
    error was below the routable threshold (no-op).

    The caller (PredictiveLayer.predict_and_compare, or a wrapper)
    should invoke this only when `error.routed_to_workspace is True`;
    we defend anyway with an explicit check so that wiring mistakes
    fail closed.
    """
    if not error.routed_to_workspace:
        return None
    if error.effective_surprise < _MIN_ROUTABLE_SURPRISE:
        # PredictiveLayer set the flag but the effective surprise
        # is below our router-level floor. Fail safe: do not route.
        logger.debug(
            "surprise_routing: skipping %s (effective=%.3f < floor=%.3f)",
            error.surprise_level, error.effective_surprise,
            _MIN_ROUTABLE_SURPRISE,
        )
        return None

    urgency = _urgency_for_level(error.surprise_level)

    item = WorkspaceItem(
        item_id=f"pp1-{uuid.uuid4()}",
        content=context or f"[PP-1 surprise on {error.channel}] {error.surprise_level}",
        content_embedding=list(content_embedding) if content_embedding else [],
        source_agent="predictive_layer",
        source_channel=f"pp1:{error.channel}",
        surprise_signal=error.effective_surprise,
        agent_urgency=urgency,
        novelty_score=_NOVELTY_FOR_SURPRISE,
        metadata={
            "pp1_error_id": error.error_id,
            "pp1_surprise_level": error.surprise_level,
            "pp1_error_magnitude": round(error.error_magnitude, 3),
            "pp1_channel": error.channel,
        },
    )

    # Populate salience_score so evaluate() does not treat this as 0.
    # The SalienceScorer is normally called with goal embeddings; for
    # PP-1 routing we compute a direct composite from urgency + novelty
    # + surprise so that high-surprise items compete meaningfully even
    # when no task-goal comparison is available.
    item.salience_score = (
        0.35 * item.goal_relevance       # 0.0 without goals (unbiased)
        + 0.25 * item.novelty_score
        + 0.15 * item.agent_urgency
        + 0.25 * item.surprise_signal
    )

    result = gate.evaluate(item)

    logger.info(
        "surprise_routing: level=%s effective=%.2f -> %s",
        error.surprise_level,
        error.effective_surprise,
        result.transition_type,
    )
    return result


def _urgency_for_level(surprise_level: str) -> float:
    """Map surprise_level string to an urgency float in [0.6, 0.95]."""
    if surprise_level == "PARADIGM_VIOLATION":
        return _URGENCY_FOR_PARADIGM
    if surprise_level == "MAJOR_SURPRISE":
        return _URGENCY_FOR_MAJOR
    # NOTABLE_SURPRISE and anything else that got through the
    # PredictiveLayer's level filter falls back to NOTABLE.
    return _URGENCY_FOR_NOTABLE
