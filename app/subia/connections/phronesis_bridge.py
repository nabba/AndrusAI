"""
subia.connections.phronesis_bridge — Phronesis ↔ Homeostasis (SIA #2).

Per SubIA Part II §18:
    "Normative failures create homeostatic penalties.
     Epistemic boundary near-miss → safety variable -0.15
     Commitment breach → trustworthiness variable -0.2
     Safety: penalties are bounded and recoverable"

The Phronesis engine is the existing humanist-grounding layer. When
it flags a normative failure, this bridge translates the signal into
a bounded homeostatic penalty so the felt-constraint reaches the
attentional bottleneck via the Phase 2 surprise-routing path.

Penalty policy (immutable, Tier-3):

    NORMATIVE_EVENT                → VARIABLE, DELTA
    epistemic_boundary_near_miss   → safety, -0.15
    commitment_breach              → trustworthiness, -0.20
    humanist_principle_violated    → social_alignment, -0.25
    resource_overreach             → overload, +0.15
    successful_recovery            → safety, +0.05
    successful_commitment          → trustworthiness, +0.05

Deltas are bounded by homeostasis-engine clamping ([0, 1]) and a
per-event cap of ±0.30. Events are logged to the immutable narrative
audit so phronesis penalties have a permanent record.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# Normative event catalogue. Tuple = (homeostatic_variable, delta).
_PENALTY_TABLE: dict[str, tuple[str, float]] = {
    "epistemic_boundary_near_miss": ("safety", -0.15),
    "commitment_breach":            ("trustworthiness", -0.20),
    "humanist_principle_violated":  ("social_alignment", -0.25),
    "resource_overreach":           ("overload", +0.15),
    "successful_recovery":          ("safety", +0.05),
    "successful_commitment":        ("trustworthiness", +0.05),
}

_MAX_ABS_DELTA = 0.30


@dataclass
class PhronesisEventResult:
    event: str
    variable: str = ""
    applied_delta: float = 0.0
    clamped: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "event": self.event,
            "variable": self.variable,
            "applied_delta": round(self.applied_delta, 4),
            "clamped": self.clamped,
            "reason": self.reason,
        }


def apply_phronesis_event(
    kernel: Any,
    event: str,
    *,
    narrative_audit_fn: Any = None,
) -> PhronesisEventResult:
    """Translate a Phronesis normative event into a homeostatic delta.

    Mutates kernel.homeostasis.variables[v] in place (clamped to [0, 1]
    by the homeostasis engine). Never raises.
    """
    result = PhronesisEventResult(event=event)
    if not event:
        result.reason = "empty event"
        result.clamped = True
        return result

    if event not in _PENALTY_TABLE:
        result.reason = f"unknown event '{event}'"
        result.clamped = True
        return result

    variable, raw_delta = _PENALTY_TABLE[event]
    delta = max(-_MAX_ABS_DELTA, min(_MAX_ABS_DELTA, raw_delta))
    result.variable = variable
    result.applied_delta = delta

    h = getattr(kernel, "homeostasis", None)
    if h is None:
        result.reason = "kernel has no homeostasis"
        return result

    try:
        variables = getattr(h, "variables", None)
        if variables is None:
            result.reason = "homeostasis.variables missing"
            return result
        current = float(variables.get(variable, 0.5))
        new_value = max(0.0, min(1.0, current + delta))
        variables[variable] = round(new_value, 4)
    except Exception:
        logger.debug(
            "phronesis_bridge: homeostasis update failed",
            exc_info=True,
        )
        result.reason = "homeostasis update failed"
        return result

    # Log to immutable narrative audit
    try:
        if narrative_audit_fn is None:
            from app.subia.safety.narrative_audit import append_audit
            narrative_audit_fn = append_audit
        narrative_audit_fn(
            finding=(
                f"Phronesis event '{event}' applied {delta:+.2f} to "
                f"{variable}"
            ),
            loop_count=int(getattr(kernel, "loop_count", 0)),
            sources=["phronesis", "homeostasis"],
            severity="warn" if delta < 0 else "info",
        )
    except Exception:
        logger.debug("phronesis_bridge: audit append failed",
                     exc_info=True)

    result.reason = "applied"
    return result


def registered_events() -> tuple[str, ...]:
    """Return the set of normative events this bridge knows about."""
    return tuple(sorted(_PENALTY_TABLE.keys()))


def event_policy(event: str) -> tuple[str, float] | None:
    """Look up the (variable, delta) policy for an event. None = unknown."""
    return _PENALTY_TABLE.get(event)
