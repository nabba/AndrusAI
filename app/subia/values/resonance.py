"""Deterministic value-resonance matcher (Proposal 6 §6.2).

Hot-path. No LLM. Keyword/concept sets derived from
HUMANIST_CONSTITUTION and Phronesis frameworks; Tier-3 protected so the
Self-Improver cannot inflate its own value-alignment score by editing
the keyword list.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from app.subia.config import SUBIA_CONFIG
from app.subia.kernel import SceneItem

# Value channels. Each maps to a homeostatic variable per Proposal 6.
DIGNITY = "dignity_fulfillment"
TRUTH = "truth_alignment"
CARE = "care_activation"
EXCELLENCE = "excellence_satisfaction"

# Keyword sets — minimal seed; expandable via wiki/philosophy/.
_KEYWORDS = {
    DIGNITY: (
        "dignity", "autonomy", "rights", "consent", "respect",
        "person", "human", "agency", "freedom",
    ),
    TRUTH: (
        "truth", "evidence", "verify", "provenance", "transparent",
        "accurate", "honest", "fact", "clarify", "resolve", "uncertainty",
    ),
    CARE: (
        "care", "wellbeing", "help", "support", "andrus", "user",
        "stakeholder", "benefit",
    ),
    EXCELLENCE: (
        "quality", "craft", "elegant", "rigorous", "thorough",
        "excellence", "well-done", "exemplary",
    ),
}

# Per-channel modulation onto existing homeostatic variables.
_CHANNEL_TO_HOMEOSTAT = {
    DIGNITY:    "social_alignment",
    TRUTH:      "coherence",
    CARE:       "progress",
    EXCELLENCE: "trustworthiness",
}


@dataclass
class ValueResonance:
    """Per-item resonance result."""
    overall: float = 0.0           # 0.0–1.0 max-channel intensity
    channels: dict = field(default_factory=dict)  # channel → intensity
    homeostatic_deltas: dict = field(default_factory=dict)  # var → delta

    def is_significant(self, threshold: float = 0.3) -> bool:
        return self.overall >= threshold


def _text_of(item: SceneItem) -> str:
    pieces = [getattr(item, "summary", "") or "",
              getattr(item, "content_ref", "") or ""]
    return " ".join(pieces).lower()


def score_item(item: SceneItem) -> ValueResonance:
    """Score a single scene item against the four value channels."""
    text = _text_of(item)
    if not text.strip():
        return ValueResonance()

    channels: dict[str, float] = {}
    deltas: dict[str, float] = {}
    for channel, words in _KEYWORDS.items():
        hits = sum(1 for w in words if w in text)
        if not hits:
            continue
        # Saturating: 1 hit → 0.4, 2 → 0.7, 3+ → 1.0
        intensity = min(1.0, 0.25 + 0.25 * hits)
        channels[channel] = round(intensity, 3)
        var = _CHANNEL_TO_HOMEOSTAT[channel]
        deltas[var] = round(deltas.get(var, 0.0) + intensity * 0.05, 4)

    overall = round(max(channels.values()) if channels else 0.0, 3)
    return ValueResonance(
        overall=overall,
        channels=channels,
        homeostatic_deltas=deltas,
    )


def apply_resonance_to_scene(kernel) -> int:
    """For every focal scene item, score and apply salience boost.

    Closed-loop wired: salience modulation is the behavioural
    consequence; without it, value resonance would be computed-but-unused.
    Returns number of items modulated.
    """
    boost = float(SUBIA_CONFIG.get("VALUE_RESONANCE_SALIENCE_BOOST", 0.15))
    n = 0
    for item in kernel.focal_scene():
        vr = score_item(item)
        if vr.overall <= 0.0:
            continue
        # Salience boost
        item.salience = round(min(1.0, item.salience + vr.overall * boost), 4)
        # Homeostatic deltas applied
        h = kernel.homeostasis
        for var, delta in vr.homeostatic_deltas.items():
            h.variables[var] = round(
                max(0.0, min(1.0, h.variables.get(var, 0.5) + delta)), 4
            )
        n += 1
    return n
