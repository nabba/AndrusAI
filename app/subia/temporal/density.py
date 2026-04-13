"""Processing density — the felt quality of recent time (Proposal §3.4).

High density: many scene transitions, prediction errors, wonder events,
homeostatic shifts per unit clock time. Subjectively rich.
Low density: routine processing, few changes. Subjectively sparse.

Maps to the human experience of "time flying" vs. "time dragging".
Closed-loop consequence: feeds the wonder-threshold modulator (dense
periods make wonder easier to enter) and the cascade preference (dense
periods favour higher-tier reasoning if available).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DensitySample:
    window_minutes: float
    scene_transitions: int = 0
    prediction_errors: int = 0
    wonder_events: int = 0
    homeostatic_shifts: int = 0       # number of variables that crossed |delta| > 0.05

    def total_events(self) -> int:
        return (self.scene_transitions + self.prediction_errors
                + self.wonder_events + self.homeostatic_shifts)


# Calibration: 12 events per 60 minutes ≈ density 1.0 (very dense session).
_DENSITY_NORMALIZER = 12.0 / 60.0   # events per minute that maps to 1.0


def compute_processing_density(sample: DensitySample) -> float:
    """Return felt density in [0.0, 1.0]."""
    minutes = max(0.5, float(sample.window_minutes))
    rate = sample.total_events() / minutes
    return round(min(1.0, rate / _DENSITY_NORMALIZER), 4)


def density_to_wonder_threshold_delta(density: float) -> float:
    """Higher density → lower wonder threshold (easier to enter wonder).

    Range: density 0.0 → +0.05 (slightly harder), density 1.0 → -0.10.
    """
    return round(0.05 - 0.15 * float(density), 4)


def density_describes_subjective_time(density: float) -> str:
    """Render density as a one-line subjective description for context."""
    if density >= 0.7:
        return "subjectively dense (time feels fast)"
    if density >= 0.4:
        return "subjectively engaged"
    if density >= 0.15:
        return "subjectively routine"
    return "subjectively sparse (time feels slow)"
