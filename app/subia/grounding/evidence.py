"""Per-claim evidence checking → ALLOW / ESCALATE / BLOCK decision.

This is the deterministic core of the grounding gate. Pure functions
over (claim, belief, source registry) so the policy is auditable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .belief_adapter import BeliefAdapter, GroundedBelief
from .claims import FactualClaim
from .source_registry import SourceRegistry


class GroundingDecision(str, Enum):
    ALLOW    = "allow"     # backed by a verified belief
    ESCALATE = "escalate"  # would-be claim — fetch first, then answer
    BLOCK    = "block"     # claim contradicts a verified belief — refuse


@dataclass
class GroundingVerdict:
    claim: FactualClaim
    decision: GroundingDecision
    reason: str
    backing_belief: Optional[GroundedBelief] = None
    suggested_source: str = ""           # URL from registry, if any
    contradicted_belief: Optional[GroundedBelief] = None


def topic_key_for(claim: FactualClaim) -> str:
    """Build a canonical lookup key for the belief store.

    Format: ``{topic_hint}::{normalized_date}`` (date may be empty).
    Falls back to ``general_fact::<hash-of-text>`` if no topic hint.

    Date normalisation strips leading prepositions ("on ", "as of "),
    punctuation, and case so that "on April 14, 2022" and
    "April 14, 2022" produce the same key.
    """
    if claim.topic_hint:
        date = _normalize_date_for_key(claim.attributed_date)
        return f"{claim.topic_hint}::{date}" if date else claim.topic_hint
    import hashlib
    h = hashlib.sha256(claim.text.encode("utf-8")).hexdigest()[:12]
    return f"general_fact::{h}"


def _normalize_date_for_key(s: str) -> str:
    """Canonicalise a date phrase for use in a key.

    "on April 14, 2022" → "april_14_2022"
    "as of 2024-12-31"  → "2024-12-31"
    "April 14, 2022"    → "april_14_2022"
    "2024-12-31"        → "2024-12-31"
    ""                  → ""
    """
    import re
    if not s:
        return ""
    out = s.strip().lower()
    # Strip leading prepositions
    out = re.sub(r"^(?:on|as\s+of|in)\s+", "", out)
    # ISO date: keep as-is
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", out):
        return out
    out = out.replace(",", "")
    out = re.sub(r"\s+", "_", out)
    return out


def _values_match(claim_value: str, belief_value: str) -> bool:
    """Lenient comparison — strip currency symbols + whitespace + casing."""
    import re
    def _norm(s: str) -> str:
        return re.sub(r"[€$£¥\s,]", "", s).lower()
    cv, bv = _norm(claim_value), _norm(belief_value)
    if cv == bv:
        return True
    # Numeric tolerance: 1% (handles 0.595 vs 0.5950)
    try:
        ncv = float(re.sub(r"[^\d.]", "", cv))
        nbv = float(re.sub(r"[^\d.]", "", bv))
        if max(ncv, nbv) > 0:
            return abs(ncv - nbv) / max(ncv, nbv) < 0.01
    except (ValueError, ZeroDivisionError):
        pass
    return False


def decide_for_claim(
    claim: FactualClaim,
    *,
    belief_adapter: BeliefAdapter,
    source_registry: SourceRegistry,
) -> GroundingVerdict:
    """Single-claim decision. Pure-ish (reads adapters, no writes)."""
    key = topic_key_for(claim)
    belief = belief_adapter.find(key)
    # Prefix fallback: drafts often omit dates that the verified belief
    # was stored with. Try the topic_hint as a prefix to surface any
    # ACTIVE belief on the same topic (date-agnostic).
    if belief is None and claim.topic_hint:
        try:
            belief = belief_adapter.find_by_prefix(claim.topic_hint)
        except (AttributeError, NotImplementedError):
            pass

    # Case 1: verified belief AGREES with the claim → ALLOW
    if belief and belief.is_verified():
        if _values_match(claim.normalized_value, belief.value):
            return GroundingVerdict(
                claim=claim,
                decision=GroundingDecision.ALLOW,
                reason="claim matches a verified belief",
                backing_belief=belief,
            )
        # Case 2: verified belief CONTRADICTS the claim → BLOCK
        return GroundingVerdict(
            claim=claim,
            decision=GroundingDecision.BLOCK,
            reason=(
                f"claim ({claim.normalized_value}) contradicts a verified "
                f"belief ({belief.value})"
            ),
            contradicted_belief=belief,
        )

    # Case 3: belief exists but unverified → ESCALATE
    if belief is not None:
        return GroundingVerdict(
            claim=claim,
            decision=GroundingDecision.ESCALATE,
            reason="belief exists but lacks evidence sources",
            backing_belief=belief,
            suggested_source=_suggest_source(claim, source_registry),
        )

    # Case 4: no belief at all → ESCALATE (must fetch)
    return GroundingVerdict(
        claim=claim,
        decision=GroundingDecision.ESCALATE,
        reason="no verified belief backs this claim",
        suggested_source=_suggest_source(claim, source_registry),
    )


def _suggest_source(claim: FactualClaim, registry: SourceRegistry) -> str:
    if not claim.topic_hint:
        return ""
    rs = registry.get(claim.topic_hint)
    return rs.url if rs else ""


@dataclass
class EvidenceCheck:
    """Result of running decide_for_claim over every claim in a draft."""
    verdicts: list = field(default_factory=list)

    @property
    def any_blocked(self) -> bool:
        return any(v.decision == GroundingDecision.BLOCK for v in self.verdicts)

    @property
    def any_escalated(self) -> bool:
        return any(v.decision == GroundingDecision.ESCALATE for v in self.verdicts)

    @property
    def all_allowed(self) -> bool:
        return bool(self.verdicts) and all(
            v.decision == GroundingDecision.ALLOW for v in self.verdicts
        )

    @property
    def aggregate_decision(self) -> GroundingDecision:
        if self.any_blocked:
            return GroundingDecision.BLOCK
        if self.any_escalated:
            return GroundingDecision.ESCALATE
        return GroundingDecision.ALLOW
