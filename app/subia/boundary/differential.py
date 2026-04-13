"""Mode-aware downstream rules (Proposal 5 §5.2).

Two pure functions consulted by the homeostasis engine and the
consolidator. No state, no side effects — just the routing rules that
make processing mode behaviorally consequential.
"""
from __future__ import annotations

from .classifier import (
    INTROSPECTIVE, MEMORIAL, PERCEPTUAL, IMAGINATIVE, SOCIAL,
)

# Per-mode multiplicative impact on each homeostatic variable.
# Variables not listed receive 1.0 (unchanged). Closed-loop discipline:
# every entry has an observable consequence in update_homeostasis.
_MODULATORS = {
    INTROSPECTIVE: {"coherence": 1.5, "self_coherence": 1.5},
    MEMORIAL:      {"progress": 1.3, "novelty_balance": 0.7},
    PERCEPTUAL:    {"novelty_balance": 1.5, "contradiction_pressure": 1.2},
    IMAGINATIVE:   {"wonder": 1.2, "novelty_balance": 1.2,
                    "trustworthiness": 0.8},  # imaginative items don't strengthen trust
    SOCIAL:        {"social_alignment": 1.4, "trustworthiness": 1.1},
}


def homeostatic_modulator_for(mode: str | None, variable: str) -> float:
    """Return multiplicative modulator for (mode, variable)."""
    if not mode:
        return 1.0
    return float(_MODULATORS.get(mode, {}).get(variable, 1.0))


# Routing preferences for the consolidator.
_ROUTES = {
    INTROSPECTIVE: {"prefer": "wiki/self/", "mem0_tier": "curated"},
    MEMORIAL:      {"prefer": "mem0",        "mem0_tier": "full"},
    PERCEPTUAL:    {"prefer": "wiki/domain", "mem0_tier": "full"},
    IMAGINATIVE:   {"prefer": "wiki/meta/reverie/", "mem0_tier": "full",
                    "epistemic_tag": "speculative"},
    SOCIAL:        {"prefer": "social_model", "mem0_tier": "curated"},
}


def consolidator_route_for(mode: str | None) -> dict:
    """Return routing preference dict for the consolidator."""
    if not mode:
        return {}
    return dict(_ROUTES.get(mode, {}))
