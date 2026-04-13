"""Grounding ↔ Chat-handler bridge (Phase 15).

Two thin wrappers the existing chat handler can call. Both are
defensive — if grounding is disabled or fails, they return the input
unchanged so the chat path keeps working.

Wire-in (single-line addition at each touch-point):

    # In the chat handler, just before sending the response:
    from app.subia.connections.grounding_chat_bridge import ground_response
    response = ground_response(response, user_message=user_text)

    # In the chat handler, when receiving a new user message:
    from app.subia.connections.grounding_chat_bridge import observe_user_correction
    observe_user_correction(user_text, prior_response=last_bot_response)

That's it — no other changes required. Activation: set
SUBIA_GROUNDING_ENABLED=1 in the environment.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.subia.grounding import GroundingPipeline, GroundingResult, DetectedCorrection

logger = logging.getLogger(__name__)


# Process-local singleton — built lazily on first use.
_pipeline: Optional[GroundingPipeline] = None


def get_pipeline() -> GroundingPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = GroundingPipeline()
    return _pipeline


def reset_pipeline_for_tests(pipeline: Optional[GroundingPipeline] = None) -> None:
    """Test helper — never call from production code."""
    global _pipeline
    _pipeline = pipeline


def ground_response(
    draft: str,
    *,
    user_message: str = "",
) -> str:
    """Pre-egress hook. Returns the rewritten response (or draft if
    grounding is disabled / errors).

    Use in the chat handler as a transparent transformer:
        response = ground_response(response, user_message=user_text)
    """
    if draft is None:
        return draft
    try:
        result: GroundingResult = get_pipeline().check_egress(
            draft, user_message=user_message,
        )
        return result.text
    except Exception as exc:
        logger.warning(
            "grounding bridge: ground_response failed; passing draft "
            "through unchanged: %s", exc,
        )
        return draft


def observe_user_correction(
    user_message: str,
    *,
    prior_response: str = "",
    loop_count: int = 0,
) -> Optional[DetectedCorrection]:
    """Post-ingress hook. Detects + synchronously persists corrections.

    Returns the DetectedCorrection if one was captured, else None.
    Never raises.
    """
    if not user_message:
        return None
    try:
        return get_pipeline().observe_user_message(
            user_message,
            prior_response=prior_response,
            loop_count=loop_count,
        )
    except Exception as exc:
        logger.warning(
            "grounding bridge: observe_user_correction failed: %s", exc,
        )
        return None


def is_grounding_enabled() -> bool:
    """Surface the feature-flag state for diagnostic endpoints."""
    return bool(get_pipeline().config.enabled)
