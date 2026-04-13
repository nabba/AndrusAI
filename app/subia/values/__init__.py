"""subia.values — Phase 12 Proposal 6: Value Resonance.

Values are not constraints that punish violations — they are perceptual
modulations that shape what the system notices. Implemented in two
layers:

  1. `resonance.py` — deterministic keyword/concept matcher producing a
     ValueResonance score per scene item. Hot-path safe (zero LLM).
  2. `perceptual_lens.py` — five always-on Phronesis lens modulators
     that adjust salience and homeostatic side-effects. Pure functions.

Optional Tier-1 LLM deepening lives behind `enrich_resonance()` and is
only called when keyword score > 0.3 (~20% of focal items per
Proposal 6 §6.3 token budget).
"""
from .resonance import (
    ValueResonance,
    score_item,
    apply_resonance_to_scene,
    DIGNITY, TRUTH, CARE, EXCELLENCE,
)
from .perceptual_lens import (
    PHRONESIS_LENSES,
    apply_lenses_to_homeostasis,
)

__all__ = [
    "ValueResonance", "score_item", "apply_resonance_to_scene",
    "DIGNITY", "TRUTH", "CARE", "EXCELLENCE",
    "PHRONESIS_LENSES", "apply_lenses_to_homeostasis",
]
