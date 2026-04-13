"""Phronesis frameworks as always-on perceptual lenses (Proposal 6 §6.2).

Five lenses, each a pure modulator over (item_text, kernel) → dict of
sensitivity adjustments. Applied alongside `score_item()` during
Step 3 (Attend). Closed-loop: each lens output feeds either salience or
known_unknowns.
"""
from __future__ import annotations

from typing import Callable

# Lens signature: (lower-cased text, kernel) → {sensitivity_key: weight}
_Sentitivity = dict


def _socratic(text: str, kernel) -> _Sentitivity:
    # Heightened attention to unexamined assumptions.
    markers = ("assume", "obviously", "clearly", "self-evident", "of course")
    if any(m in text for m in markers):
        return {"unexamined_assumption_alert": 1.0}
    return {}


def _dialectical(text: str, kernel) -> _Sentitivity:
    # Heightened attention to productive contradictions.
    if "contradict" in text or "tension" in text or "but" in text:
        return {"productive_tension_attention": 1.0}
    return {}


def _stoic(text: str, kernel) -> _Sentitivity:
    # Awareness of control boundaries.
    markers = ("not in our control", "external", "outside", "depend on")
    if any(m in text for m in markers):
        return {"control_boundary_clarity": 1.0}
    return {}


def _aristotelian(text: str, kernel) -> _Sentitivity:
    # Evaluation in terms of character/excellence.
    markers = ("habit", "character", "virtue", "excellence", "flourish")
    if any(m in text for m in markers):
        return {"character_evaluation_active": 1.0}
    return {}


def _rhetorical(text: str, kernel) -> _Sentitivity:
    # Awareness of audience reception.
    markers = ("audience", "reader", "listener", "user", "andrus", "communicat")
    if any(m in text for m in markers):
        return {"audience_awareness_active": 1.0}
    return {}


PHRONESIS_LENSES: dict[str, Callable] = {
    "socratic":     _socratic,
    "dialectical":  _dialectical,
    "stoic":        _stoic,
    "aristotelian": _aristotelian,
    "rhetorical":   _rhetorical,
}


def apply_lenses_to_homeostasis(kernel) -> dict:
    """Run every lens over every focal item; return aggregate report.

    Closed-loop: any lens that fires raises 'self_coherence' slightly
    (Aristotelian + Stoic) or contributes to known_unknowns (Socratic).
    """
    aggregate: dict[str, int] = {}
    socratic_hits = 0
    for item in kernel.focal_scene():
        text = (getattr(item, "summary", "") or "").lower()
        for name, lens in PHRONESIS_LENSES.items():
            for sens_key, weight in lens(text, kernel).items():
                aggregate[sens_key] = aggregate.get(sens_key, 0) + 1
                if sens_key == "unexamined_assumption_alert":
                    socratic_hits += 1

    # Closed-loop wiring
    h = kernel.homeostasis
    if "self_coherence" in h.variables:
        bump = 0.01 * (
            aggregate.get("character_evaluation_active", 0)
            + aggregate.get("control_boundary_clarity", 0)
        )
        h.variables["self_coherence"] = round(
            min(1.0, h.variables["self_coherence"] + bump), 4
        )
    if socratic_hits:
        kernel.meta_monitor.known_unknowns.append(
            f"socratic-lens flagged {socratic_hits} unexamined assumption(s)"
        )
    return aggregate
