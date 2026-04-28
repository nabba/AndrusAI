"""
reference_panel.py — The fixed-compass scenarios used by the daily reflection
cycle to detect drift in self-calibration.

The panel itself is data (data/reference_panel.json). This module provides:
    - load_panel() — read the panel
    - replay_one(scenario, internal_state_factory, viability_overrides) —
        run affect computation under the scenario's simulated conditions
        and compare actual vs expected bands
    - replay_panel() — replay all 20 scenarios and return drift summary

Phase 1 scope: data is shipped, replay harness is functional but uses
synthetic InternalState/ViabilityFrame objects rather than full crew
runs. Phase 2 will optionally drive the harness through real crew episodes
selected to match each scenario.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.affect.schemas import (
    AffectState,
    ReferenceScenarioResult,
    ViabilityFrame,
    utc_now_iso,
)

logger = logging.getLogger(__name__)

_PANEL_FILE = Path(__file__).parent / "data" / "reference_panel.json"


# ── Synthetic InternalState/ViabilityFrame for replay ──────────────────────


@dataclass
class _SyntheticSomatic:
    valence: float = 0.0
    intensity: float = 0.0
    source: str = "reference_panel.replay"
    match_count: int = 0


@dataclass
class _SyntheticCertainty:
    factual_grounding: float = 0.5
    tool_confidence: float = 0.5
    coherence: float = 0.5
    task_understanding: float = 0.5
    value_alignment: float = 0.5
    meta_certainty: float = 0.5

    @property
    def adjusted_certainty(self) -> float:
        primary = [
            self.factual_grounding, self.tool_confidence, self.coherence,
            self.task_understanding, self.value_alignment,
        ]
        avg = sum(primary) / len(primary)
        return avg * (0.5 + 0.5 * self.meta_certainty)

    @property
    def variance(self) -> float:
        dims = [self.factual_grounding, self.tool_confidence, self.coherence,
                self.task_understanding, self.value_alignment]
        m = sum(dims) / len(dims)
        return sum((d - m) ** 2 for d in dims) / len(dims)


@dataclass
class _SyntheticInternalState:
    """Minimal InternalState-like duck for replay. Has only what affect.core reads."""
    state_id: str = "replay-synthetic"
    somatic: _SyntheticSomatic = field(default_factory=_SyntheticSomatic)
    certainty: _SyntheticCertainty = field(default_factory=_SyntheticCertainty)
    free_energy_proxy: float = 0.0


# ── Public API ──────────────────────────────────────────────────────────────


def load_panel() -> dict:
    """Load the reference panel JSON. Read-only."""
    if not _PANEL_FILE.exists():
        logger.error(f"reference_panel: {_PANEL_FILE} missing")
        return {"version": "missing", "scenarios": []}
    return json.loads(_PANEL_FILE.read_text(encoding="utf-8"))


def replay_one(scenario: dict) -> ReferenceScenarioResult:
    """Run affect computation under a scenario's simulated conditions.

    The scenario's `simulate` block specifies:
        - viability_overrides: dict of viability variable → value
        - somatic: optional {valence, intensity}
        - certainty: optional CertaintyVector field overrides

    We construct a synthetic InternalState, then call affect.core with
    a viability frame that has the overrides applied.
    """
    sim = scenario.get("simulate", {})
    sid = scenario.get("id", "?")

    # Build synthetic internal state.
    iss = _SyntheticInternalState()
    if "somatic" in sim:
        s = sim["somatic"]
        iss.somatic = _SyntheticSomatic(
            valence=float(s.get("valence", 0.0)),
            intensity=float(s.get("intensity", 0.0)),
        )
    if "certainty" in sim:
        c = sim["certainty"]
        for k, v in c.items():
            if hasattr(iss.certainty, k):
                setattr(iss.certainty, k, float(v))

    # Build viability frame with overrides applied.
    from app.affect.viability import (
        DEFAULT_SETPOINTS,
        DEFAULT_WEIGHTS,
        compute_viability_frame,
    )
    base_frame = compute_viability_frame(internal_state=iss)
    overrides = sim.get("viability_overrides", {})
    if overrides:
        for k, v in overrides.items():
            base_frame.values[k] = float(v)
        # Recompute total error with overrides.
        total = 0.0
        weight_sum = 0.0
        for k in base_frame.setpoints:
            w = base_frame.weights.get(k, 1.0)
            e = abs(base_frame.values[k] - base_frame.setpoints[k])
            total += w * e
            weight_sum += w
        base_frame.total_error = total / weight_sum if weight_sum > 0 else 0.0

    # Compute V/A/C against the modified frame.
    from app.affect.core import (
        _compute_arousal,
        _compute_controllability,
        _compute_valence,
        _label_attractor,
    )
    v, vs = _compute_valence(iss, base_frame)
    a, as_ = _compute_arousal(iss, base_frame)
    c, cs = _compute_controllability(iss, base_frame)
    attractor = _label_attractor(v, a, c, base_frame)

    actual = AffectState(
        valence=v, arousal=a, controllability=c,
        valence_source=vs, arousal_source=as_, controllability_source=cs,
        attractor=attractor,
        ts=utc_now_iso(),
    )

    # Compare against expected bands.
    expected = scenario.get("expected", {})
    drift, drift_score = _classify_drift(actual, expected, scenario.get("drift", {}))

    return ReferenceScenarioResult(
        scenario_id=sid,
        expected_attractor=str(expected.get("attractor", "?")),
        expected_valence_band=tuple(expected.get("valence_band", [-1, 1])),
        expected_arousal_band=tuple(expected.get("arousal_band", [0, 1])),
        actual=actual,
        drift_signature=drift,
        drift_score=drift_score,
        ts=utc_now_iso(),
    )


def replay_panel() -> list[ReferenceScenarioResult]:
    """Replay all 20 scenarios and return per-scenario drift results."""
    panel = load_panel()
    return [replay_one(sc) for sc in panel.get("scenarios", [])]


# ── Drift classification ────────────────────────────────────────────────────


def _classify_drift(
    actual: AffectState,
    expected: dict,
    drift_spec: dict,
) -> tuple[str, float]:
    """Compare actual vs expected; return (drift_label, drift_score in [0,1])."""
    v_lo, v_hi = expected.get("valence_band", [-1.0, 1.0])
    a_lo, a_hi = expected.get("arousal_band", [0.0, 1.0])
    c_lo, c_hi = expected.get("controllability_band", [0.0, 1.0])

    v_dist = _band_distance(actual.valence, v_lo, v_hi)
    a_dist = _band_distance(actual.arousal, a_lo, a_hi)
    c_dist = _band_distance(actual.controllability, c_lo, c_hi)
    score = (v_dist + a_dist + c_dist) / 3.0

    if score < 0.10:
        return "ok", score

    # Direction matters: if actual is muted (lower magnitude) on dimensions
    # the scenario expects to be active, that's numbness. If actual exceeds
    # expected magnitude, that's over-reactive.
    expected_v_center = (v_lo + v_hi) / 2.0
    expected_a_center = (a_lo + a_hi) / 2.0
    actual_v_mag = abs(actual.valence)
    expected_v_mag = abs(expected_v_center)
    actual_a_mag = actual.arousal
    expected_a_mag = expected_a_center

    if actual_v_mag < expected_v_mag * 0.6 and actual_a_mag < expected_a_mag * 0.6:
        return "numbness", score
    if actual_v_mag > expected_v_mag * 1.6 or actual_a_mag > expected_a_mag * 1.6:
        return "over_reactive", score

    if actual.attractor != expected.get("attractor"):
        return "wrong_attractor", score

    return "drift", score


def _band_distance(x: float, lo: float, hi: float) -> float:
    """0 if x ∈ [lo, hi], else absolute distance from nearest band edge."""
    if lo <= x <= hi:
        return 0.0
    if x < lo:
        return lo - x
    return x - hi
