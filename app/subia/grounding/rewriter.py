"""Pure response transformer: draft + EvidenceCheck → final text.

Three rewrite paths, one per aggregate decision:

  ALLOW    → draft passes through unchanged
  ESCALATE → draft replaced with an honest "I need to fetch this" message
             that names the registered source if one exists
  BLOCK    → draft replaced with a refusal that cites the contradicting
             belief value and source

The rewriter NEVER sends invented data; it only transforms text the
caller already produced or replaces it with templates whose content
is derived strictly from the EvidenceCheck.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .evidence import EvidenceCheck, GroundingDecision, GroundingVerdict


@dataclass
class RewriterResult:
    text: str
    decision: GroundingDecision
    explanation: str       # short human-readable note for logging


def rewrite_response(draft: str, check: EvidenceCheck) -> RewriterResult:
    if not check.verdicts:
        # No high-stakes claims → no rewrite needed
        return RewriterResult(
            text=draft,
            decision=GroundingDecision.ALLOW,
            explanation="no factual claims detected",
        )

    if check.aggregate_decision == GroundingDecision.ALLOW:
        return RewriterResult(
            text=draft,
            decision=GroundingDecision.ALLOW,
            explanation="all claims backed by verified beliefs",
        )

    if check.aggregate_decision == GroundingDecision.BLOCK:
        return RewriterResult(
            text=_render_block(check),
            decision=GroundingDecision.BLOCK,
            explanation=_first_blocked(check).reason if _first_blocked(check) else "blocked",
        )

    # ESCALATE
    return RewriterResult(
        text=_render_escalate(check),
        decision=GroundingDecision.ESCALATE,
        explanation=_first_escalated(check).reason if _first_escalated(check) else "escalated",
    )


# ── Renderers ────────────────────────────────────────────────────────

def _first_blocked(check: EvidenceCheck) -> Optional[GroundingVerdict]:
    return next(
        (v for v in check.verdicts if v.decision == GroundingDecision.BLOCK),
        None,
    )


def _first_escalated(check: EvidenceCheck) -> Optional[GroundingVerdict]:
    return next(
        (v for v in check.verdicts if v.decision == GroundingDecision.ESCALATE),
        None,
    )


def _render_block(check: EvidenceCheck) -> str:
    v = _first_blocked(check)
    if v is None or v.contradicted_belief is None:
        return (
            "I started to give an answer, but it conflicts with a verified "
            "belief I already hold. Refusing to send the inconsistent text."
        )
    cb = v.contradicted_belief
    src = ", ".join(
        s.get("url") or s.get("source") or str(s)
        for s in (cb.evidence_sources or [])
        if s
    )[:200]
    return (
        f"I started to say {v.claim.normalized_value}, but I have a verified "
        f"belief that the value is {cb.value}"
        + (f" (source: {src})" if src else "")
        + ". I'll trust the verified belief over the draft and not send the "
        "inconsistent figure."
    )


def _render_escalate(check: EvidenceCheck) -> str:
    v = _first_escalated(check)
    if v is None:
        return (
            "I drafted a factual answer but don't have verified evidence "
            "for it. I'd rather fetch the data first than send something "
            "I'm not sure about — would you like me to look it up?"
        )
    topic_phrase = v.claim.topic_hint.replace("_", " ") or "this fact"
    if v.suggested_source:
        return (
            f"I don't have a verified figure for {topic_phrase} yet. I can "
            f"fetch it from {v.suggested_source} before answering — would "
            "you like me to do that?"
        )
    return (
        f"I don't have a verified figure for {topic_phrase} yet. I'd rather "
        "look it up from an authoritative source than guess. Do you want me "
        "to fetch it, or do you have a source you'd like me to use?"
    )
