"""IntrospectionPipeline — public face of Phase 17.

Two operations the chat handler calls:

  pipeline.inspect(user_message)
      Returns Optional[str]: a system-prompt prefix (the formatted
      self-state note) when the message is an introspection question,
      None otherwise. Cheap; safe to call on every message.

  pipeline.inject(user_message)
      Returns the augmented user_message (prefix + original). When
      the message is NOT introspection, returns the original
      unchanged. Never raises.

Both are no-ops when SUBIA_INTROSPECTION_ENABLED=0.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from .context import IntrospectionContext, gather_context
from .detector import (
    IntrospectionMatch,
    classify_introspection,
    is_introspection_question,
)
from .formatter import format_introspection_note

logger = logging.getLogger(__name__)


@dataclass
class IntrospectionPipelineConfig:
    enabled: bool = False
    min_confidence: float = 0.5
    log_decisions: bool = True


@dataclass
class IntrospectionResult:
    detected: bool = False
    match: Optional[IntrospectionMatch] = None
    context: Optional[IntrospectionContext] = None
    note: str = ""
    augmented_message: str = ""
    skipped: bool = False
    explanation: str = ""


def _enabled_from_env() -> bool:
    return os.environ.get(
        "SUBIA_INTROSPECTION_ENABLED", ""
    ).strip() in ("1", "true", "True", "yes")


class IntrospectionPipeline:
    def __init__(
        self,
        *,
        config: Optional[IntrospectionPipelineConfig] = None,
        gather_fn=None,
    ) -> None:
        self.config = config or IntrospectionPipelineConfig(
            enabled=_enabled_from_env(),
        )
        self._gather = gather_fn or gather_context

    # ── Public API ──────────────────────────────────────────────────
    def inspect(self, user_message: str) -> IntrospectionResult:
        """Detect + gather + format. Returns a structured result."""
        if not self.config.enabled:
            return IntrospectionResult(
                skipped=True, explanation="introspection disabled",
                augmented_message=user_message,
            )
        if not user_message:
            return IntrospectionResult(
                skipped=True, explanation="empty message",
                augmented_message=user_message,
            )

        try:
            match = classify_introspection(user_message)
        except Exception as exc:
            logger.debug("introspection: classify failed: %s", exc, exc_info=True)
            return IntrospectionResult(
                skipped=True, explanation=f"classify failed: {exc!r}",
                augmented_message=user_message,
            )

        if not match.is_introspection or match.confidence < self.config.min_confidence:
            return IntrospectionResult(
                detected=False, match=match,
                augmented_message=user_message,
                explanation="not introspection (or below confidence threshold)",
            )

        try:
            ctx = self._gather()
            note = format_introspection_note(
                ctx, user_message=user_message, match=match,
            )
        except Exception as exc:
            logger.warning(
                "introspection: gather/format failed: %s", exc, exc_info=True,
            )
            return IntrospectionResult(
                detected=True, match=match, skipped=True,
                explanation=f"gather/format failed: {exc!r}",
                augmented_message=user_message,
            )

        # ── Phase 18: per-topic sections ────────────────────────────
        # Run the topic-specific handlers for any detected topics
        # OUTSIDE the Phase 17 base set (AFFECT/ENERGY/SELF_STATE/etc.).
        # Each handler is wrapped: a failure logs and skips that
        # section without blocking the rest.
        try:
            from .topics import _import_topic_handlers
            handlers = _import_topic_handlers()
            for topic in (match.topics or []):
                handler = handlers.get(topic)
                if handler is None:
                    continue
                gather_fn, format_fn = handler
                try:
                    section_data = gather_fn() or {}
                    section_text = format_fn(section_data) or ""
                    if section_text.strip():
                        note += "\n\n" + section_text
                except Exception as topic_exc:
                    logger.debug(
                        "introspection: topic %s handler failed: %s",
                        getattr(topic, "value", topic), topic_exc,
                    )
        except Exception as exc:
            logger.debug(
                "introspection: per-topic dispatch failed (non-fatal): %s",
                exc,
            )

        augmented = (
            f"{note}\n\n"
            f"User question: {user_message}"
        )
        if self.config.log_decisions:
            logger.info(
                "introspection: detected (topics=%s confidence=%.2f); "
                "injecting %d-line self-state note",
                [t.value if hasattr(t, "value") else str(t) for t in match.topics],
                match.confidence,
                note.count("\n") + 1,
            )
        return IntrospectionResult(
            detected=True,
            match=match,
            context=ctx,
            note=note,
            augmented_message=augmented,
            explanation="introspection detected",
        )

    def inject(self, user_message: str) -> str:
        """Convenience: return augmented_message directly. The chat
        bridge uses this — it always returns a string and never None."""
        return self.inspect(user_message).augmented_message


# Process-local singleton — built lazily on first use.
_pipeline: Optional[IntrospectionPipeline] = None


def get_pipeline() -> IntrospectionPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = IntrospectionPipeline()
    return _pipeline


def reset_pipeline_for_tests(pipeline: Optional[IntrospectionPipeline] = None) -> None:
    """Test helper — never call from production code."""
    global _pipeline
    _pipeline = pipeline
