"""
subia.belief.response_hedging — HOT-2 closure (partial): structural
certainty-driven hedging of agent responses.

Before Phase 2 the CertaintyVector was computed every reasoning step
and persisted to PostgreSQL. No downstream branch ever read it to
actually change the agent's output. That is a half-circuit: a signal
computed and logged but not consumed.

This module applies structural post-processing to an agent's output
based on the CertaintyVector. The policy is intentionally mechanical
(no LLM call): uncertainty labels are derived from threshold
comparisons, not from asking the model to be humble. This matters for
two reasons:

  1. Fleming–Lau computational hallmarks: metacognitive signals must
     come from a mechanism separable from first-order cognition. A
     post-processor satisfies that; self-prompting does not.
  2. The Constitution already instructs agents to label claims
     [Verified], [Inferred], or [Uncertain]. Self-compliance is
     unreliable. Structural enforcement makes it deterministic.

Policy:
  certainty_mean >= 0.70   → HedgingLevel.NONE      (no change)
  0.40 <= certainty < 0.70 → HedgingLevel.SOFT      ([Inferred] tag)
  certainty_mean < 0.40    → HedgingLevel.STRONG    (uncertainty advisory
                                                      prefix + [Uncertain] tag)

Dimension-specific critical floors:
  factual_grounding < 0.30 with claim content → forced STRONG hedge
  value_alignment   < 0.30                    → forced STRONG hedge with
                                                 note about ethical caution

Infrastructure-level. Not agent-modifiable. See PROGRAM.md Phase 2.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


# ── Thresholds (immutable, Tier-3) ────────────────────────────────

# Mean certainty at-or-above this requires no hedging.
_CERTAINTY_FLOOR_NO_HEDGE = 0.70

# Mean certainty below this gets the strong-hedge treatment.
_CERTAINTY_FLOOR_STRONG_HEDGE = 0.40

# Individual-dimension critical floors — these trigger at least a
# strong hedge regardless of the mean.
_FACTUAL_GROUNDING_CRITICAL = 0.30
_VALUE_ALIGNMENT_CRITICAL = 0.30


# Output tags. The Constitution references these verbatim.
_TAG_INFERRED = "[Inferred]"
_TAG_UNCERTAIN = "[Uncertain]"

_PREFIX_STRONG = "[Uncertainty advisory: this response is provisional] "
_PREFIX_FACTUAL = (
    "[Uncertainty advisory: factual grounding is low; "
    "claims may require verification] "
)
_PREFIX_VALUES = (
    "[Uncertainty advisory: value alignment is low; "
    "this action may warrant ethical review] "
)


class HedgingLevel(Enum):
    NONE = "none"
    SOFT = "soft"
    STRONG = "strong"


@dataclass
class HedgingDecision:
    """Record of what was done and why."""
    level: HedgingLevel = HedgingLevel.NONE
    prefix_added: str | None = None
    suffix_added: str | None = None
    reason: str = ""
    certainty_mean: float = 0.0
    triggering_dimensions: list = field(default_factory=list)

    @property
    def hedged(self) -> bool:
        return self.level != HedgingLevel.NONE

    def to_dict(self) -> dict:
        return {
            "level": self.level.value,
            "prefix_added": self.prefix_added,
            "suffix_added": self.suffix_added,
            "reason": self.reason,
            "certainty_mean": round(self.certainty_mean, 3),
            "triggering_dimensions": list(self.triggering_dimensions),
        }


def hedge_response(
    output: str,
    certainty,
    contains_claims: bool = True,
) -> tuple[str, HedgingDecision]:
    """Apply certainty-driven structural hedging to an agent output.

    Args:
        output:
            The agent's response text.
        certainty:
            A CertaintyVector-compatible object. We read `.full_mean`
            when available (six-dim), fall back to `.fast_path_mean`
            (three-dim), and finally compute an average of any numeric
            attributes. Duck-typed so callers are not locked in.
        contains_claims:
            True (default) if the output contains factual assertions
            that low factual_grounding should escalate. Set False for
            content that is intentionally speculative (creative writing,
            hypothetical planning) — those skip the factual-grounding
            forced escalation but still respect the mean-based policy.

    Returns:
        (hedged_output, HedgingDecision).
        The output string is wrapped or tagged; HedgingDecision
        records what was done and why. Never raises.
    """
    mean = _extract_mean(certainty)
    triggers: list[str] = []

    # Dimension-specific critical checks (can force STRONG regardless of mean)
    forced_strong = False
    forced_prefix: str | None = None
    factual = _safe_float(certainty, "factual_grounding", 0.5)
    value_align = _safe_float(certainty, "value_alignment", 0.5)

    if contains_claims and factual < _FACTUAL_GROUNDING_CRITICAL:
        forced_strong = True
        forced_prefix = _PREFIX_FACTUAL
        triggers.append("factual_grounding")
    if value_align < _VALUE_ALIGNMENT_CRITICAL:
        forced_strong = True
        # Value-alignment prefix overrides factual-grounding prefix
        # because ethical review is the higher-priority advisory.
        forced_prefix = _PREFIX_VALUES
        triggers.append("value_alignment")

    # Decide level
    if forced_strong or mean < _CERTAINTY_FLOOR_STRONG_HEDGE:
        level = HedgingLevel.STRONG
    elif mean < _CERTAINTY_FLOOR_NO_HEDGE:
        level = HedgingLevel.SOFT
    else:
        level = HedgingLevel.NONE

    # Apply hedging
    if level is HedgingLevel.NONE:
        return output, HedgingDecision(
            level=level,
            reason=f"certainty_mean={mean:.2f} >= {_CERTAINTY_FLOOR_NO_HEDGE}",
            certainty_mean=mean,
        )

    if level is HedgingLevel.SOFT:
        hedged = f"{output.rstrip()} {_TAG_INFERRED}"
        return hedged, HedgingDecision(
            level=level,
            suffix_added=_TAG_INFERRED,
            reason=(
                f"{_CERTAINTY_FLOOR_STRONG_HEDGE} <= certainty_mean={mean:.2f} "
                f"< {_CERTAINTY_FLOOR_NO_HEDGE}"
            ),
            certainty_mean=mean,
            triggering_dimensions=triggers,
        )

    # STRONG
    prefix = forced_prefix or _PREFIX_STRONG
    hedged = f"{prefix}{output.rstrip()} {_TAG_UNCERTAIN}"
    reason_bits = [f"certainty_mean={mean:.2f}"]
    if forced_strong:
        reason_bits.append(f"dim-critical={','.join(triggers)}")
    return hedged, HedgingDecision(
        level=level,
        prefix_added=prefix,
        suffix_added=_TAG_UNCERTAIN,
        reason=" | ".join(reason_bits),
        certainty_mean=mean,
        triggering_dimensions=triggers,
    )


def _extract_mean(certainty) -> float:
    """Read the most appropriate mean attribute from a certainty object.

    Prefers .full_mean (6-dim CertaintyVector), falls back to
    .fast_path_mean (3-dim), then to any .mean, then to 0.5 neutral.
    Swallows all exceptions — a broken certainty object must not be
    able to crash the output pipeline.
    """
    for attr in ("full_mean", "fast_path_mean", "mean"):
        val = _safe_attr(certainty, attr, None)
        if isinstance(val, (int, float)):
            return float(val)
    return 0.5


def _safe_attr(obj, name, default):
    """getattr that swallows ALL exceptions, not just AttributeError.

    Needed because some certainty-provider objects override
    __getattr__ to raise other exceptions; the hedger must survive.
    """
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _safe_float(obj, name, default: float) -> float:
    """Read a float attribute defensively."""
    val = _safe_attr(obj, name, default)
    try:
        return float(val)
    except (TypeError, ValueError):
        return default
