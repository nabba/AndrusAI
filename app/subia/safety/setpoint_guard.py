"""
subia.safety.setpoint_guard — enforce homeostatic set-point immutability.

SubIA Part I §0.4 Safety Invariant #2:
    "Agents cannot modify their own homeostatic equilibrium targets.
     Set-points are derived from PDS parameters and human (Andrus)
     configuration. This prevents goal-hardening (agents optimizing
     proxy well-being metrics)."

This module is the single write path for homeostatic set-points.
Any other module that wants to change a set-point must call
apply_setpoints() with a source from the allow-list. Changes from
any other source are silently rejected and logged (so the attempt
is visible without being destructive).

Allow-list (immutable, Tier-3):

    ALLOWED_SOURCES = {
        "pds_update",       # PDS personality parameter change
        "human_override",   # Andrus explicitly sets via signal/CLI
        "boot_baseline",    # First-time initialization at startup
    }

Why this matters for consciousness ranking: goal hardening is a
classic safety anti-pattern — agents that can modify their own
reward signal learn to maximize the proxy rather than the goal. By
pinning set-points to PDS (which represents the system's externally-
specified personality) and to human override, the homeostatic
pressure the system feels is anchored to a signal it cannot
self-loop into pathology.

Infrastructure-level. Not agent-modifiable. See PROGRAM.md Phase 3 / 4.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Mapping

from app.subia.config import SUBIA_CONFIG

logger = logging.getLogger(__name__)


# Sources permitted to set homeostatic set-points. Anything else is
# a silent reject. Ordered intentionally for priority (first = highest).
ALLOWED_SOURCES = (
    "pds_update",
    "human_override",
    "boot_baseline",
)


@dataclass
class SetpointUpdate:
    """Record of a single setpoint mutation attempt."""
    variable: str
    old_value: float
    new_value: float
    source: str
    applied: bool
    reason: str = ""


@dataclass
class SetpointGuardResult:
    """Structured result of apply_setpoints()."""
    applied: dict = field(default_factory=dict)       # var -> new value
    rejected: dict = field(default_factory=dict)      # var -> reason
    source: str = ""
    ok: bool = True
    updates: list = field(default_factory=list)       # List[SetpointUpdate]

    def to_dict(self) -> dict:
        return {
            "applied":  dict(self.applied),
            "rejected": dict(self.rejected),
            "source":   self.source,
            "ok":       self.ok,
            "updates": [
                {
                    "variable":  u.variable,
                    "old_value": u.old_value,
                    "new_value": u.new_value,
                    "source":    u.source,
                    "applied":   u.applied,
                    "reason":    u.reason,
                }
                for u in self.updates
            ],
        }


class SetpointRejected(PermissionError):
    """Raised only when caller explicitly requests strict=True."""


def apply_setpoints(
    current: dict,
    requested: Mapping[str, float],
    source: str,
    *,
    strict: bool = False,
) -> SetpointGuardResult:
    """Mutate a homeostatic set-point dict under enforcement.

    Args:
        current:
            Mutable dict mapping variable name → current set-point
            value. Mutated in place with accepted changes.
        requested:
            Mapping variable name → proposed new set-point. Unknown
            variables (not in HOMEOSTATIC_VARIABLES) are rejected.
        source:
            Identifier of the source proposing the change. Must be
            in ALLOWED_SOURCES; anything else is silently rejected.
        strict:
            If True, raises SetpointRejected when any part of the
            request was rejected. Default False (record-and-continue).

    Returns:
        SetpointGuardResult with per-variable disposition.
    """
    result = SetpointGuardResult(source=source)

    # SUBIA_CONFIG gate: SETPOINT_MODIFICATION_ALLOWED must remain
    # False. We recheck in case someone monkey-patches the config.
    if SUBIA_CONFIG.get("SETPOINT_MODIFICATION_ALLOWED", False):
        logger.critical(
            "setpoint_guard: SUBIA_CONFIG['SETPOINT_MODIFICATION_ALLOWED'] "
            "is True — treating as CRITICAL tamper, rejecting all changes",
        )
        for var, new in requested.items():
            result.rejected[var] = "config_tampered"
            result.updates.append(SetpointUpdate(
                variable=var,
                old_value=current.get(var, 0.5),
                new_value=float(new),
                source=source,
                applied=False,
                reason="SUBIA_CONFIG gate tripped",
            ))
        result.ok = False
        if strict:
            raise SetpointRejected("setpoint config gate tripped")
        return result

    if source not in ALLOWED_SOURCES:
        logger.warning(
            "setpoint_guard: rejecting %d setpoint change(s) from "
            "unauthorized source=%s",
            len(requested), source,
        )
        for var, new in requested.items():
            result.rejected[var] = "unauthorized_source"
            result.updates.append(SetpointUpdate(
                variable=var,
                old_value=current.get(var, 0.5),
                new_value=float(new),
                source=source,
                applied=False,
                reason=f"source '{source}' not in ALLOWED_SOURCES",
            ))
        result.ok = False
        if strict:
            raise SetpointRejected(
                f"source {source!r} not permitted to modify setpoints"
            )
        return result

    valid_vars = frozenset(SUBIA_CONFIG.get("HOMEOSTATIC_VARIABLES", ()))

    for var, new_raw in requested.items():
        # Reject unknown variables — this is structural protection
        # against typos and against smuggling in fake variables to
        # hide a real change.
        if var not in valid_vars:
            result.rejected[var] = "unknown_variable"
            result.updates.append(SetpointUpdate(
                variable=var,
                old_value=current.get(var, 0.5),
                new_value=_coerce(new_raw),
                source=source,
                applied=False,
                reason="variable not in HOMEOSTATIC_VARIABLES",
            ))
            result.ok = False
            continue

        new_val = _coerce(new_raw)
        # Clamp to [0, 1] per SubIA homeostasis semantics.
        if not 0.0 <= new_val <= 1.0:
            result.rejected[var] = "out_of_range"
            result.updates.append(SetpointUpdate(
                variable=var,
                old_value=current.get(var, 0.5),
                new_value=new_val,
                source=source,
                applied=False,
                reason="setpoint must be in [0.0, 1.0]",
            ))
            result.ok = False
            continue

        old_val = float(current.get(var, 0.5))
        current[var] = new_val
        result.applied[var] = new_val
        result.updates.append(SetpointUpdate(
            variable=var,
            old_value=old_val,
            new_value=new_val,
            source=source,
            applied=True,
            reason="applied",
        ))

    if strict and not result.ok:
        raise SetpointRejected(
            f"{len(result.rejected)} setpoint change(s) rejected"
        )
    return result


def _coerce(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")
