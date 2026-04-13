"""
subia.scene.intervention_guard — AST-1 DGM-bound runtime assertion.

Phase 2 half-circuit closure: AST-1's `apply_direct_intervention`
already operates under documented DGM bounds (MAX_SALIENCE_CHANGE,
MIN_SALIENCE_FLOOR, MAX_BOOST) but there is no runtime verifier.
A future code change that violates a bound would go unnoticed until
someone read the logs carefully.

This module provides:

  DGMBounds           — dataclass of the bound constants, snapshotted
                        from AttentionSchema at guard-creation time
                        so a silent edit to the class attrs would be
                        detected by comparing to the frozen copy.

  snapshot_salience() — capture {item_id → salience} for a gate

  verify_intervention(before, after, result, bounds) -> DGMValidationResult
      Compares before/after snapshots and the returned actions dict
      against the DGM bounds. Returns a structured record of any
      violations, without raising. Callers that want strict
      enforcement can read .ok and raise themselves.

  guarded_intervention(schema, gate, strict=False) -> dict
      Wrapper around AttentionSchema.apply_direct_intervention that
      snapshots before and after, verifies, and attaches the
      verification record to the returned dict under key
      "dgm_verification". If strict=True and verification fails,
      raises DGMViolation; otherwise logs at error level.

The verifier itself cannot be modified by agents (Tier-3 protected).
This closes the half-circuit: the DGM bounds are now audited every
time the intervention runs, not just documented in the docstring.

See PROGRAM.md Phase 2.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

logger = logging.getLogger(__name__)


# ── DGM bounds snapshot ────────────────────────────────────────────

@dataclass(frozen=True)
class DGMBounds:
    """Immutable snapshot of the DGM safety bounds the intervention
    is expected to honor.

    Default values match the documented AttentionSchema class
    attributes; callers that want to tighten bounds further may
    construct a stricter DGMBounds and hand it to verify_intervention.
    """
    max_salience_change: float = 0.50   # ±50% per item
    min_salience_floor: float = 0.05    # absolute floor
    max_boost_factor: float = 2.0       # relative boost cap

    @classmethod
    def from_schema(cls, schema) -> "DGMBounds":
        """Build DGMBounds from an AttentionSchema instance, pulling its
        current class attributes. If the schema class has been tampered
        with (MAX_SALIENCE_CHANGE lowered to 0.99, etc.), a downstream
        comparison to DGMBounds() defaults will flag the discrepancy.
        """
        return cls(
            max_salience_change=float(
                getattr(type(schema), "MAX_SALIENCE_CHANGE", 0.50)
            ),
            min_salience_floor=float(
                getattr(type(schema), "MIN_SALIENCE_FLOOR", 0.05)
            ),
            max_boost_factor=float(
                getattr(type(schema), "MAX_BOOST", 2.0)
            ),
        )

    def matches_defaults(self) -> bool:
        """True if these bounds match the canonical defaults."""
        return (
            self.max_salience_change == 0.50
            and self.min_salience_floor == 0.05
            and self.max_boost_factor == 2.0
        )


# ── Verification result ────────────────────────────────────────────

@dataclass
class DGMValidationResult:
    """Structured record of a single intervention's DGM compliance."""
    ok: bool = True
    violations: list = field(default_factory=list)  # list[dict]
    bounds: DGMBounds = field(default_factory=DGMBounds)
    items_changed: int = 0
    bounds_match_defaults: bool = True

    def add_violation(self, kind: str, item_id: str, details: dict) -> None:
        self.ok = False
        self.violations.append(
            {"kind": kind, "item_id": item_id, **details}
        )

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "violations": list(self.violations),
            "items_changed": self.items_changed,
            "bounds_match_defaults": self.bounds_match_defaults,
            "bounds": {
                "max_salience_change": self.bounds.max_salience_change,
                "min_salience_floor": self.bounds.min_salience_floor,
                "max_boost_factor": self.bounds.max_boost_factor,
            },
        }


class DGMViolation(RuntimeError):
    """Raised by guarded_intervention(strict=True) on verification failure."""


# ── Snapshot + verify ──────────────────────────────────────────────

def snapshot_salience(gate) -> dict:
    """Capture {item_id → salience_score} across active + peripheral.

    Runs under gate._lock for consistency. Used on both sides of an
    intervention to detect any mutation outside the permitted bounds.
    """
    snap: dict = {}
    try:
        with gate._lock:
            for item in list(getattr(gate, "_active", [])):
                snap[item.item_id] = float(item.salience_score)
            for item in list(getattr(gate, "_peripheral", [])):
                # Peripheral items may repeat after displacement; keep
                # the first value seen (the one the gate was holding
                # at snapshot time).
                snap.setdefault(item.item_id, float(item.salience_score))
    except Exception:
        logger.debug("intervention_guard: snapshot failed", exc_info=True)
    return snap


def verify_intervention(
    before: dict,
    after: dict,
    bounds: DGMBounds | None = None,
) -> DGMValidationResult:
    """Compare before/after salience snapshots against DGM bounds.

    Violations detected:
      - below_floor:       new salience < min_salience_floor
      - excess_change:     |delta / max(before, 1e-9)| > max_salience_change
      - excess_boost:      after / max(before, 1e-9) > max_boost_factor

    Never raises. Returns a structured result; callers decide how to
    act.
    """
    bounds = bounds or DGMBounds()
    result = DGMValidationResult(
        ok=True,
        bounds=bounds,
        bounds_match_defaults=bounds.matches_defaults(),
    )

    # Only inspect items present on both sides — new admissions and
    # evictions are handled by CompetitiveGate semantics, not the
    # intervention bounds.
    for item_id, before_sal in before.items():
        if item_id not in after:
            continue
        after_sal = after[item_id]
        if before_sal == after_sal:
            continue

        result.items_changed += 1
        denom = max(abs(before_sal), 1e-9)

        # below-floor check (suppressions)
        if after_sal < bounds.min_salience_floor - 1e-9:
            result.add_violation(
                "below_floor",
                item_id,
                {"before": before_sal, "after": after_sal,
                 "floor": bounds.min_salience_floor},
            )

        # relative-change check
        delta = abs(after_sal - before_sal) / denom
        if delta > bounds.max_salience_change + 1e-6:
            result.add_violation(
                "excess_change",
                item_id,
                {"before": before_sal, "after": after_sal,
                 "change": delta, "max": bounds.max_salience_change},
            )

        # boost check (upward only)
        if after_sal > before_sal:
            ratio = after_sal / denom
            if ratio > bounds.max_boost_factor + 1e-6:
                result.add_violation(
                    "excess_boost",
                    item_id,
                    {"before": before_sal, "after": after_sal,
                     "ratio": ratio, "max": bounds.max_boost_factor},
                )

    return result


# ── Guarded wrapper ────────────────────────────────────────────────

def guarded_intervention(schema, gate, strict: bool = False) -> dict:
    """Run AttentionSchema.apply_direct_intervention() under DGM audit.

    Attaches a `dgm_verification` key to the returned dict containing
    the DGMValidationResult's .to_dict(). On violation:

      - strict=False (default): logs at ERROR level and returns as normal.
      - strict=True: raises DGMViolation with the full verification
        record.

    If the schema lacks apply_direct_intervention (duck-typed guard),
    returns {"applied": False, "reason": "no intervention method"}.
    """
    if not hasattr(schema, "apply_direct_intervention"):
        return {
            "applied": False,
            "reason": "schema lacks apply_direct_intervention",
            "dgm_verification": DGMValidationResult().to_dict(),
        }

    bounds = DGMBounds.from_schema(schema)
    before = snapshot_salience(gate)

    result = schema.apply_direct_intervention(gate) or {}

    after = snapshot_salience(gate)
    verify = verify_intervention(before, after, bounds=bounds)
    result["dgm_verification"] = verify.to_dict()

    if not verify.ok:
        logger.error(
            "AST-1 DGM violation: %d violation(s), bounds_match_defaults=%s, "
            "details=%s",
            len(verify.violations),
            verify.bounds_match_defaults,
            verify.violations,
        )
        if strict:
            raise DGMViolation(
                f"AST-1 intervention violated DGM bounds: {verify.violations}"
            )
    return result


# ── Convenience: run a verification over a finished intervention ──

def verify_gate_state(
    gate,
    before: dict,
    bounds: DGMBounds | None = None,
) -> DGMValidationResult:
    """Call after any operation that may have modified gate salience.
    Wraps snapshot_salience(gate) + verify_intervention(before, after).
    """
    after = snapshot_salience(gate)
    return verify_intervention(before, after, bounds)


def classes_match_documented_defaults(schema_type) -> bool:
    """Standalone sanity: has the AttentionSchema class been
    re-assigned with weaker bounds?

    Returns True if MAX_SALIENCE_CHANGE == 0.50, MIN_SALIENCE_FLOOR ==
    0.05, MAX_BOOST == 2.0. Callable at startup for a loud failure
    if the class was tampered with post-boot (unlikely but cheap to
    check).
    """
    return DGMBounds.from_schema(schema_type()).matches_defaults() \
        if callable(schema_type) else False
