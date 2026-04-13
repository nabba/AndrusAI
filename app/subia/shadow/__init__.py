"""subia.shadow — Phase 12 Proposal 3: Shadow Self.

Behavioral pattern mining over Mem0 full + scene history + accuracy
tracker + PDS, surfacing implicit biases the declared self_state does
not carry. Output is appended (immutably) to wiki/self/shadow-analysis.md
and to self_state.discovered_limitations.

Runs on a slow cadence (monthly by default) via the idle scheduler.
"""
from .miner import (
    ShadowMiner,
    ShadowAdapters,
    ShadowReport,
)
from .biases import (
    detect_attentional_bias,
    detect_prediction_bias,
    detect_avoidance,
    detect_affect_action_divergence,
)

__all__ = [
    "ShadowMiner", "ShadowAdapters", "ShadowReport",
    "detect_attentional_bias", "detect_prediction_bias",
    "detect_avoidance", "detect_affect_action_divergence",
]
