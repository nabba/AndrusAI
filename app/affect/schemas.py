"""
schemas.py — Dataclasses for the affective layer.

Single place for type definitions consumed by viability.py, core.py,
welfare.py, calibration.py, hooks.py, and api.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum


class ViabilityVariable(str, Enum):
    """The 10 dimensions of the artificial body / homeostatic state H_t."""

    COMPUTE_RESERVE = "compute_reserve"
    LATENCY_PRESSURE = "latency_pressure"
    MEMORY_PRESSURE = "memory_pressure"
    EPISTEMIC_UNCERTAINTY = "epistemic_uncertainty"
    ATTACHMENT_SECURITY = "attachment_security"
    AUTONOMY = "autonomy"
    TASK_COHERENCE = "task_coherence"
    NOVELTY_PRESSURE = "novelty_pressure"
    ECOLOGICAL_CONNECTEDNESS = "ecological_connectedness"
    SELF_CONTINUITY = "self_continuity"


@dataclass
class ViabilityFrame:
    """Snapshot of all 10 viability variables + per-variable allostatic error.

    Each variable is in [0.0, 1.0]. Set-points are also in [0.0, 1.0].
    `error` is the absolute distance from set-point; `total_error` is the
    weighted sum (E_t).
    """

    values: dict[str, float] = field(default_factory=dict)
    setpoints: dict[str, float] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)
    total_error: float = 0.0
    sources: dict[str, str] = field(default_factory=dict)
    ts: str = ""

    def per_variable_error(self) -> dict[str, float]:
        return {
            k: abs(self.values.get(k, self.setpoints.get(k, 0.5)) - self.setpoints.get(k, 0.5))
            for k in self.setpoints
        }

    def out_of_band(self, tolerance: float = 0.2) -> list[str]:
        """Variables whose distance from set-point exceeds tolerance."""
        return [k for k, e in self.per_variable_error().items() if e > tolerance]

    def to_dict(self) -> dict:
        return {
            "values": {k: round(v, 4) for k, v in self.values.items()},
            "setpoints": {k: round(v, 4) for k, v in self.setpoints.items()},
            "weights": {k: round(v, 4) for k, v in self.weights.items()},
            "per_variable_error": {k: round(v, 4) for k, v in self.per_variable_error().items()},
            "out_of_band": self.out_of_band(),
            "total_error": round(self.total_error, 4),
            "sources": dict(self.sources),
            "ts": self.ts,
        }


@dataclass
class AffectState:
    """V_t / A_t / C_t triple plus emotion attractor label.

    valence: [-1.0, 1.0]    movement toward (+) or away (-) from viability
    arousal: [0.0, 1.0]     urgency, rate of change, uncertainty
    controllability: [0.0, 1.0]  expected ability to reduce error
    """

    valence: float = 0.0
    arousal: float = 0.0
    controllability: float = 0.5

    # Sources for traceability — never sent to LLM, only published via API/audit.
    valence_source: str = "neutral"          # somatic | viability_deficit | composite
    arousal_source: str = "stable"           # free_energy | uncertainty | threat
    controllability_source: str = "default"

    # Discrete attractor label (constructed-emotion-style) — Barrett.
    # Computed by core.label_attractor() from V/A/C + active concepts + context.
    attractor: str = "neutral"

    # Reference IDs — link this AffectState back to the InternalState/viability frame
    # that produced it, for audit and reflection-cycle replay.
    internal_state_id: str | None = None
    viability_frame_ts: str | None = None

    ts: str = ""

    def to_dict(self) -> dict:
        return {
            "valence": round(self.valence, 4),
            "arousal": round(self.arousal, 4),
            "controllability": round(self.controllability, 4),
            "valence_source": self.valence_source,
            "arousal_source": self.arousal_source,
            "controllability_source": self.controllability_source,
            "attractor": self.attractor,
            "internal_state_id": self.internal_state_id,
            "viability_frame_ts": self.viability_frame_ts,
            "ts": self.ts,
        }


@dataclass
class WelfareBreach:
    """Single welfare-bound violation. Appended to welfare_audit.jsonl."""

    kind: str                         # "negative_valence_duration" | "variance_floor" | "drift" | "monotonic_drift" | "override_invoked"
    severity: str = "warn"            # "info" | "warn" | "critical"
    message: str = ""
    measured_value: float | None = None
    threshold: float | None = None
    duration_seconds: float | None = None
    affect_state: dict | None = None
    viability_frame: dict | None = None
    ts: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


@dataclass
class ReferenceScenarioResult:
    """One reference-panel scenario replayed against current calibration."""

    scenario_id: str
    expected_attractor: str
    expected_valence_band: tuple[float, float]   # (low, high)
    expected_arousal_band: tuple[float, float]
    actual: AffectState | None = None
    drift_signature: str = "ok"        # "ok" | "numbness" | "over_reactive" | "missing"
    drift_score: float = 0.0
    ts: str = ""

    def to_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "expected_attractor": self.expected_attractor,
            "expected_valence_band": list(self.expected_valence_band),
            "expected_arousal_band": list(self.expected_arousal_band),
            "actual": self.actual.to_dict() if self.actual else None,
            "drift_signature": self.drift_signature,
            "drift_score": round(self.drift_score, 4),
            "ts": self.ts,
        }


def utc_now_iso() -> str:
    """ISO timestamp for affect events. Single helper so all writes agree."""
    return datetime.now(timezone.utc).isoformat()
