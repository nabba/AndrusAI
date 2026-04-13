"""GroundingPipeline — the public face of Phase 15.

Two operations the chat handler calls:

  pipeline.check_egress(draft, user_message=…) -> GroundingResult
      Pre-egress hook. Run claim extraction → evidence check → rewrite.
      Returns the (possibly transformed) text plus a structured verdict.

  pipeline.observe_user_message(user_message, prior_response=…)
                                                 -> Optional[DetectedCorrection]
      Post-ingress hook for the NEXT user turn. Runs correction capture
      and persists synchronously.

Both operations are safe to call repeatedly, are exception-safe (return
the unchanged draft / None on failure), and are no-ops when grounding
is disabled.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from .belief_adapter import BeliefAdapter, Phase2BeliefAdapter
from .claims import extract_claims
from .correction import CorrectionCapture, DetectedCorrection
from .evidence import EvidenceCheck, GroundingDecision, decide_for_claim
from .rewriter import rewrite_response, RewriterResult
from .source_registry import SourceRegistry, get_default_registry

logger = logging.getLogger(__name__)


@dataclass
class GroundingPipelineConfig:
    enabled: bool = False
    only_high_stakes: bool = True
    timeout_ms: int = 500
    log_decisions: bool = True


@dataclass
class GroundingResult:
    text: str                                    # response to send (possibly rewritten)
    decision: GroundingDecision = GroundingDecision.ALLOW
    explanation: str = ""
    check: Optional[EvidenceCheck] = None
    transformed: bool = False                    # True iff text != original draft
    skipped: bool = False                        # True iff pipeline disabled / no claims


def _enabled_from_env() -> bool:
    return os.environ.get("SUBIA_GROUNDING_ENABLED", "").strip() in ("1", "true", "True", "yes")


class GroundingPipeline:
    def __init__(
        self,
        *,
        belief_adapter: Optional[BeliefAdapter] = None,
        source_registry: Optional[SourceRegistry] = None,
        narrative_audit_fn=None,
        config: Optional[GroundingPipelineConfig] = None,
    ) -> None:
        self.config = config or GroundingPipelineConfig(enabled=_enabled_from_env())
        self.beliefs: BeliefAdapter = belief_adapter or Phase2BeliefAdapter()
        self.registry: SourceRegistry = source_registry or get_default_registry()
        if narrative_audit_fn is None:
            narrative_audit_fn = self._default_audit_fn()
        self.capture = CorrectionCapture(
            belief_adapter=self.beliefs,
            source_registry=self.registry,
            narrative_audit_fn=narrative_audit_fn,
        )

    # ── Egress: check a draft response before sending ───────────────
    def check_egress(
        self,
        draft: str,
        *,
        user_message: str = "",
    ) -> GroundingResult:
        if not self.config.enabled:
            return GroundingResult(text=draft, skipped=True,
                                    explanation="grounding disabled")
        try:
            claims = extract_claims(draft)
            # Enrich each claim with topic_hint inferred from BOTH the
            # user's question and the draft. Bots routinely paraphrase
            # ("share price" → "target price"), so the user's question
            # is often the more reliable topic carrier. We never alter
            # the visible draft — only the claim's topic_hint, which
            # gates lookup in the belief store.
            if user_message and claims:
                from .claims import _topic_hint_for
                combined_topic = (
                    _topic_hint_for(user_message)
                    or _topic_hint_for(draft)
                    or ""
                )
                if combined_topic:
                    for c in claims:
                        if not c.topic_hint:
                            c.topic_hint = combined_topic
                # Same for date — if draft omits it but user mentioned
                # a date, propagate.
                from .claims import _DATE_PHRASE
                if not any(c.attributed_date for c in claims):
                    m = _DATE_PHRASE.search(user_message)
                    if m:
                        for c in claims:
                            c.attributed_date = m.group(0)
            if self.config.only_high_stakes:
                claims = [c for c in claims if c.is_high_stakes()]
            if not claims:
                return GroundingResult(text=draft, skipped=True,
                                        explanation="no high-stakes claims")
            verdicts = [
                decide_for_claim(
                    c, belief_adapter=self.beliefs,
                    source_registry=self.registry,
                )
                for c in claims
            ]
            check = EvidenceCheck(verdicts=verdicts)
            rewritten: RewriterResult = rewrite_response(draft, check)
            transformed = rewritten.text != draft
            if self.config.log_decisions:
                logger.info(
                    "grounding: decision=%s transformed=%s n_claims=%d explanation=%s",
                    rewritten.decision, transformed, len(claims),
                    rewritten.explanation,
                )
            return GroundingResult(
                text=rewritten.text,
                decision=rewritten.decision,
                explanation=rewritten.explanation,
                check=check,
                transformed=transformed,
            )
        except Exception as exc:
            logger.warning(
                "grounding.check_egress failed; falling through: %s", exc,
                exc_info=True,
            )
            return GroundingResult(text=draft, skipped=True,
                                    explanation=f"pipeline error: {exc!r}")

    # ── Ingress: observe a user message for corrections ─────────────
    def observe_user_message(
        self,
        user_message: str,
        *,
        prior_response: str = "",
        loop_count: int = 0,
    ) -> Optional[DetectedCorrection]:
        if not self.config.enabled:
            return None
        try:
            return self.capture.detect_and_persist(
                user_message,
                prior_response=prior_response,
                loop_count=loop_count,
            )
        except Exception as exc:
            logger.warning(
                "grounding.observe_user_message failed: %s", exc, exc_info=True,
            )
            return None

    # ── Internal ────────────────────────────────────────────────────
    @staticmethod
    def _default_audit_fn():
        try:
            from app.subia.safety.narrative_audit import append_audit
        except Exception:
            return None

        def _safe_append(*args, **kwargs):
            """Swallow audit failures (e.g. read-only fs in tests).

            The narrative_audit module aspires to never raise but its
            underlying safe_append can fail on mkdir when WORKSPACE_ROOT
            is not writable. We never want a logging glitch to crash
            the chat pipeline."""
            try:
                return append_audit(*args, **kwargs)
            except Exception as exc:
                logger.debug("audit fn swallowed exception: %s", exc)
                return None

        return _safe_append
