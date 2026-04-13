"""subia.wonder — Phase 12 Proposal 4: Wonder Register.

Depth-sensitive epistemic affect. Deterministic detector (no LLM, no
judgment call) using the formula in Proposal 4 §4.2: weighted sum of
multi-level resonance, generative contradiction, recursive structure,
cross-epistemic resonance, and structural-analogy span.

Wonder modulates behavior in three closed-loop ways:
  1. Salience-decay freeze on the triggering scene item (kernel-level).
  2. Homeostatic variable `wonder` rises (engine-level).
  3. Reverie-priority topic recorded for next idle cycle (bridge-level).

The detector is Tier-3 protected so the Self-Improver cannot lower the
depth thresholds to manufacture wonder events.
"""
from .detector import (
    detect_wonder,
    UnderstandingDepth,
    WonderSignal,
)
from .register import (
    apply_wonder_to_kernel,
    should_inhibit_completion,
    is_wonder_event,
)

__all__ = [
    "detect_wonder", "UnderstandingDepth", "WonderSignal",
    "apply_wonder_to_kernel", "should_inhibit_completion", "is_wonder_event",
]
