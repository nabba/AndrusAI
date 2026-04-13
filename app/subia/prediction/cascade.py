"""
subia.prediction.cascade — cascade-tier modulation policy.

Phase 6 extension: the original cascade modulation (inline in
SubIALoop._step_cascade) looked at single-prediction confidence
only. That caught per-task uncertainty but missed the "model keeps
being wrong on this domain" case — where every individual prediction
looks confident but the domain has a sustained error trend.

This module formalizes the policy as a pure function so it is
(a) testable in isolation, (b) composable with the accuracy tracker,
and (c) Tier-3-protected independently of the loop.

Inputs:
  - single-prediction confidence
  - per-domain sustained-error flag from accuracy_tracker
  - homeostatic coherence deviation (existing signal)

Outputs: one of "maintain", "escalate", "escalate_premium"
         plus a structured decision record for logging/audit.

Safety posture: escalation is cautious — it moves UP, never down.
Choosing a cheaper tier from this module is deliberately impossible.
That matches the cascade-tier contract: we only override the default
selection to ask for MORE capability, never less.

Infrastructure-level. Not agent-modifiable. See PROGRAM.md Phase 6.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.subia.config import SUBIA_CONFIG

logger = logging.getLogger(__name__)


# Ordered high → low. Escalation moves toward index 0.
_TIER_LEVELS = ("escalate_premium", "escalate", "maintain")


@dataclass
class CascadeDecision:
    """Structured cascade-modulation decision."""
    recommendation: str = "maintain"
    reasons: list = field(default_factory=list)
    prediction_confidence: float = 0.5
    homeostatic_coherence_deviation: float = 0.0
    domain: str = ""
    sustained_error: bool = False

    def to_dict(self) -> dict:
        return {
            "recommendation": self.recommendation,
            "reasons": list(self.reasons),
            "prediction_confidence": round(self.prediction_confidence, 4),
            "homeostatic_coherence_deviation": round(
                self.homeostatic_coherence_deviation, 4,
            ),
            "domain": self.domain,
            "sustained_error": self.sustained_error,
        }

    @property
    def escalated(self) -> bool:
        return self.recommendation != "maintain"


def decide_cascade(
    *,
    prediction_confidence: float = 0.5,
    homeostatic_coherence_deviation: float = 0.0,
    domain: str = "",
    sustained_error: bool = False,
    config: dict | None = None,
) -> CascadeDecision:
    """Pure function: combine three signals into a tier recommendation.

    Signals:
      1. prediction_confidence: single-call confidence from Step 5.
         Low confidence → escalate (original Phase 4 behavior).
      2. homeostatic_coherence_deviation: |deviation| from set-point.
         High magnitude → escalate; represents "the system's
         internal coherence signal is off".
      3. sustained_error: boolean from accuracy_tracker indicating
         the system's prediction accuracy has been bad in this
         domain recently. A single low-error call does not escalate,
         but the pattern does.

    Any of the three signals can trigger "escalate". Two signals
    simultaneously or a very low single-confidence (< premium floor)
    triggers "escalate_premium".

    Config-respecting: if CASCADE_UNCERTAINTY_ESCALATION is False
    we return "maintain" regardless of inputs (the feature flag
    for turning modulation off).
    """
    cfg = config or SUBIA_CONFIG
    decision = CascadeDecision(
        prediction_confidence=float(prediction_confidence),
        homeostatic_coherence_deviation=float(homeostatic_coherence_deviation),
        domain=str(domain),
        sustained_error=bool(sustained_error),
    )

    if not cfg.get("CASCADE_UNCERTAINTY_ESCALATION", True):
        decision.reasons.append("escalation disabled in config")
        return decision

    threshold = float(cfg.get("CASCADE_CONFIDENCE_THRESHOLD", 0.4))
    premium_floor = float(cfg.get("CASCADE_PREMIUM_CONFIDENCE_FLOOR", 0.2))

    escalate = False
    premium = False

    # Signal 1: prediction confidence
    if decision.prediction_confidence < premium_floor:
        decision.reasons.append(
            f"confidence {decision.prediction_confidence:.2f} "
            f"< premium floor {premium_floor}"
        )
        premium = True
    elif decision.prediction_confidence < threshold:
        decision.reasons.append(
            f"confidence {decision.prediction_confidence:.2f} "
            f"< threshold {threshold}"
        )
        escalate = True

    # Signal 2: homeostatic uncertainty
    if abs(decision.homeostatic_coherence_deviation) > 0.4:
        decision.reasons.append(
            f"homeostatic coherence deviation "
            f"{decision.homeostatic_coherence_deviation:+.2f} "
            f"exceeds 0.40"
        )
        escalate = True

    # Signal 3: sustained error in this domain
    if decision.sustained_error:
        decision.reasons.append(
            f"sustained error in domain '{domain}'"
        )
        escalate = True

    # Compose: two-or-more escalate signals → premium
    escalate_signals = sum([
        decision.prediction_confidence < threshold
        and decision.prediction_confidence >= premium_floor,
        abs(decision.homeostatic_coherence_deviation) > 0.4,
        decision.sustained_error,
    ])

    if premium or (escalate and escalate_signals >= 2):
        decision.recommendation = "escalate_premium"
    elif escalate:
        decision.recommendation = "escalate"
    else:
        decision.recommendation = "maintain"

    return decision


def highest_recommendation(*recs: str) -> str:
    """Combine multiple tier recommendations; return the highest.

    Useful when the loop receives recommendations from multiple
    sources (e.g. an early risk-flag + Step 5b).
    """
    order = {level: i for i, level in enumerate(_TIER_LEVELS)}
    current = "maintain"
    current_idx = order["maintain"]
    for r in recs:
        idx = order.get(r, order["maintain"])
        if idx < current_idx:
            current_idx = idx
            current = r
    return current
