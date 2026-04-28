"""
app.affect — Affective layer for AndrusAI.

Thin façade over the existing self-awareness substrate (app.subia.*) that adds:

    1. Viability variables (H_t) — 10 internal state dimensions whose deficits
       create allostatic error and bias action selection.
    2. Affect core (V_t, A_t, C_t) — valence/arousal/controllability triple
       computed from existing somatic + certainty + hyper-model signals.
    3. Welfare envelope — INFRASTRUCTURE-level bounds (max negative-valence
       duration, variance floor, drift detection). Not modifiable by the
       Self-Improver. Audit log + override-reset.
    4. Reference panel — fixed-compass scenarios for drift detection during
       the daily reflection cycle.
    5. Reflection cycle scaffold — runs at 04:30 Helsinki, replays trace
       against panel, proposes (Phase 1: doesn't yet apply) calibration
       adjustments under hard envelope + healthy-dynamics + ratchet.

Reads from `app.subia.belief.internal_state.InternalState.somatic` for V_t,
`hyper_model_state.free_energy_proxy` for A_t, and a combination of
`certainty.adjusted_certainty` + `reality_model_summary` for C_t.

Writes affect snapshots, viability frames, and welfare breaches to
`/app/workspace/affect/`.

Phase 1 scope (current): viability + core + welfare + reference panel +
calibration scaffold + lifecycle hooks + sampling modulation + API/dashboard.
Phase 2 will activate calibration delta application; Phases 3+ add
attachment/ecological/consciousness probes.
"""

from app.affect.schemas import (
    AffectState,
    ViabilityFrame,
    ViabilityVariable,
    WelfareBreach,
    ReferenceScenarioResult,
)
from app.affect.salience import SalienceEvent

__all__ = [
    "AffectState",
    "ViabilityFrame",
    "ViabilityVariable",
    "WelfareBreach",
    "ReferenceScenarioResult",
    "SalienceEvent",
]
