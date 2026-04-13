"""Deterministic depth detection (Proposal 4 §4.2).

Pure functions over an UnderstandingDepth descriptor. No LLM. The
weights are constants (Tier-3) so the system cannot retune them in its
own favour.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class UnderstandingDepth:
    """Output of the Understanding Layer pass for a single page/topic."""
    causal_levels: int = 0
    cross_references: int = 0
    implications_generated: int = 0
    structural_analogies: int = 0
    deep_questions: int = 0
    cross_domain_contradictions: int = 0
    recursive_structure_detected: bool = False
    epistemic_statuses: list = field(default_factory=list)


@dataclass
class WonderSignal:
    """Output of the wonder detector — closed-loop consumed."""
    intensity: float                # 0.0–1.0
    contributing_factors: dict      # factor → score increment
    inhibits_completion: bool
    is_event: bool
    triggering_topic: str = ""


# ── Weights (Tier-3, do not retune) ──────────────────────────────────
_W_MULTI_LEVEL_RESONANCE      = 0.30   # causal_depth ≥ 3 AND ref_breadth ≥ 4
_W_GENERATIVE_CONTRADICTION   = 0.20   # cross-domain contradictions, capped at 3
_W_RECURSIVE_STRUCTURE        = 0.20
_W_CROSS_EPISTEMIC            = 0.15   # creative + factual both present
_W_STRUCTURAL_ANALOGY_SPAN    = 0.15   # ≥2 cross-domain analogies


def detect_wonder(
    depth: UnderstandingDepth,
    *,
    inhibit_threshold: float = 0.3,
    event_threshold: float = 0.7,
    triggering_topic: str = "",
) -> WonderSignal:
    """Return a WonderSignal for the given UnderstandingDepth.

    Thresholds are passed in (resolved from SUBIA_CONFIG by the caller)
    rather than imported here so the detector remains a pure function
    testable in isolation.
    """
    factors: dict[str, float] = {}
    score = 0.0

    # Multi-level resonance
    if depth.causal_levels >= 3 and depth.cross_references >= 4:
        factors["multi_level_resonance"] = _W_MULTI_LEVEL_RESONANCE
        score += _W_MULTI_LEVEL_RESONANCE

    # Generative contradiction
    if depth.cross_domain_contradictions > 0:
        ratio = min(1.0, depth.cross_domain_contradictions / 3.0)
        delta = _W_GENERATIVE_CONTRADICTION * ratio
        factors["generative_contradiction"] = delta
        score += delta

    # Recursive structure
    if depth.recursive_structure_detected:
        factors["recursive_structure"] = _W_RECURSIVE_STRUCTURE
        score += _W_RECURSIVE_STRUCTURE

    # Cross-epistemic resonance
    creative = any(e == "creative" for e in depth.epistemic_statuses)
    factual = any(e in ("factual", "inferred") for e in depth.epistemic_statuses)
    if creative and factual:
        factors["cross_epistemic"] = _W_CROSS_EPISTEMIC
        score += _W_CROSS_EPISTEMIC

    # Structural-analogy span
    if depth.structural_analogies >= 2:
        factors["structural_analogy_span"] = _W_STRUCTURAL_ANALOGY_SPAN
        score += _W_STRUCTURAL_ANALOGY_SPAN

    intensity = round(min(1.0, score), 4)
    return WonderSignal(
        intensity=intensity,
        contributing_factors=factors,
        inhibits_completion=intensity > inhibit_threshold,
        is_event=intensity > event_threshold,
        triggering_topic=triggering_topic,
    )
