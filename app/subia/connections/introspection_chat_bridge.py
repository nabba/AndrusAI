"""Introspection ↔ Chat-handler bridge (Phase 17).

One thin wrapper the chat handler calls. Defensive: any failure
returns the original message unchanged so the chat path keeps working.

Wire-in (single-line addition in main.py handle_task, BEFORE the
commander.handle call):

    from app.subia.connections.introspection_chat_bridge import inject_introspection
    text = inject_introspection(text)

That's it. Activation: SUBIA_INTROSPECTION_ENABLED=1.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def inject_introspection(user_message: str) -> str:
    """Pre-LLM hook. Returns either the original message OR the
    augmented one (with self-state prefix) when introspection is
    detected. Never raises.

    Use in handle_task() as a transparent transformer:
        text = inject_introspection(text)
    """
    if user_message is None:
        return user_message
    try:
        from app.subia.introspection.pipeline import get_pipeline
        return get_pipeline().inject(user_message) or user_message
    except Exception as exc:
        logger.warning(
            "introspection bridge: inject failed; passing message "
            "through unchanged: %s", exc,
        )
        return user_message


def is_introspection_enabled() -> bool:
    """Surface the feature-flag state for diagnostic endpoints."""
    try:
        from app.subia.introspection.pipeline import get_pipeline
        return bool(get_pipeline().config.enabled)
    except Exception:
        return False


def inspect_message(user_message: str):
    """Diagnostic — returns the full IntrospectionResult so callers can
    see WHY a message was/wasn't classified as introspection without
    triggering injection. Never raises."""
    try:
        from app.subia.introspection.pipeline import get_pipeline
        return get_pipeline().inspect(user_message)
    except Exception as exc:
        logger.debug("introspection bridge: inspect failed: %s", exc)
        return None
