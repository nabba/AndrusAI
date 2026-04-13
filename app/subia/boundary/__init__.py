"""subia.boundary — Phase 12 Proposal 5: Boundary Sense.

Deterministic phenomenological-origin classifier. Maps every SceneItem's
`source` (and ownership) into one of five processing modes:

    INTROSPECTIVE | MEMORIAL | PERCEPTUAL | IMAGINATIVE | SOCIAL

This is the felt boundary between self and world — implemented not as
qualia (impossible) but as differential routing rules consulted by the
consolidator and the homeostasis engine.

The classifier is INFRASTRUCTURE: the source→mode table lives in
`SUBIA_CONFIG["BOUNDARY_MODE_MAP"]` and this file is Tier-3 protected so
the Self-Improver cannot retag introspective sources as perceptual to
inflate its own consciousness scorecard.
"""
from .classifier import (
    INTROSPECTIVE,
    MEMORIAL,
    PERCEPTUAL,
    IMAGINATIVE,
    SOCIAL,
    PROCESSING_MODES,
    classify_scene_item,
    classify_source,
)
from .differential import (
    homeostatic_modulator_for,
    consolidator_route_for,
)

__all__ = [
    "INTROSPECTIVE", "MEMORIAL", "PERCEPTUAL", "IMAGINATIVE", "SOCIAL",
    "PROCESSING_MODES",
    "classify_scene_item", "classify_source",
    "homeostatic_modulator_for", "consolidator_route_for",
]
