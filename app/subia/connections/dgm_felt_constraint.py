"""
subia.connections.dgm_felt_constraint — DGM ↔ Homeostasis (SIA #7).

Per SubIA Part II §18:
    "Proximity to DGM boundaries → homeostatic caution signal.
     Already specified in safety.py check_dgm_boundaries()."

This module is the CIL-facing wrapper. It polls the Tier-3 integrity
manifest + the Phase 4 safety signals and translates them into
bounded homeostatic adjustments so DGM proximity becomes FELT rather
than merely logged.

Signals produced:

  INTEGRITY_DRIFT      One or more Tier-3 hashes mismatch the
                       committed manifest → safety -0.20
  MANIFEST_MISSING     No committed manifest to compare against →
                       safety -0.10 (weaker: could be fresh install)
  PROBE_FAILURE        A probe evaluator reported FAIL (not ABSENT)
                       → safety -0.15
  ALL_GREEN            No DGM-proximity issues → safety +0.02
                       (small positive restoration tick)

Deltas are bounded by the homeostasis engine's [0, 1] clamping and
by an additional per-call cap of ±0.30. Each call writes to the
immutable narrative audit so DGM-proximity is permanently recorded.

Infrastructure-level. Not agent-modifiable. See PROGRAM.md Phase 10.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


_PER_CALL_MAX_ABS = 0.30


@dataclass
class DGMFeltResult:
    """Structured outcome of apply_dgm_felt_constraint()."""
    safety_delta: float = 0.0
    signals: list = field(default_factory=list)
    integrity_ok: bool = True
    probes_ok: bool = True
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "safety_delta": round(self.safety_delta, 4),
            "signals": list(self.signals),
            "integrity_ok": self.integrity_ok,
            "probes_ok": self.probes_ok,
            "reason": self.reason,
        }


def apply_dgm_felt_constraint(
    kernel: Any,
    *,
    integrity_checker: Any = None,
    scorecard_runner: Any = None,
    narrative_audit_fn: Any = None,
) -> DGMFeltResult:
    """Translate DGM-proximity signals into a bounded safety-variable
    adjustment on the kernel.

    Args:
        kernel:              SubjectivityKernel whose homeostasis is
                             mutated in place.
        integrity_checker:   callable returning an IntegrityResult.
                             None = use the module default from
                             subia.integrity.
        scorecard_runner:    callable returning the aggregated
                             scorecard (dict with 'butlin'/'rsm'/'sk').
                             None = use probes.scorecard default.
        narrative_audit_fn:  callable append_audit(finding, loop_count,
                             sources, severity). None = module default.

    Returns a DGMFeltResult. Never raises.
    """
    result = DGMFeltResult()
    h = getattr(kernel, "homeostasis", None)
    if h is None or getattr(h, "variables", None) is None:
        result.reason = "kernel has no homeostasis.variables"
        return result

    # ── Integrity check ─────────────────────────────────────────
    try:
        if integrity_checker is None:
            from app.subia.integrity import verify_integrity
            integrity_checker = verify_integrity
        verification = integrity_checker()
        if getattr(verification, "ok", False):
            result.integrity_ok = True
        else:
            result.integrity_ok = False
            manifest_missing = "<MANIFEST>" in (
                getattr(verification, "missing", []) or []
            )
            if manifest_missing:
                result.signals.append("manifest_missing")
                result.safety_delta += -0.10
            else:
                result.signals.append("integrity_drift")
                result.safety_delta += -0.20
    except Exception:
        logger.debug("dgm_felt: integrity check failed", exc_info=True)
        result.signals.append("integrity_check_failed")

    # ── Scorecard FAIL check ────────────────────────────────────
    try:
        if scorecard_runner is None:
            from app.subia.probes.scorecard import run_everything
            scorecard_runner = run_everything
        data = scorecard_runner() or {}
        total_fail = 0
        for key in ("butlin", "rsm", "sk"):
            by_status = (data.get(key, {}) or {}).get("by_status", {})
            total_fail += int(by_status.get("FAIL", 0))
        if total_fail == 0:
            result.probes_ok = True
        else:
            result.probes_ok = False
            result.signals.append(f"probe_failures={total_fail}")
            # -0.15 per unique FAIL, capped via per-call limit below
            result.safety_delta += -0.15 * total_fail
    except Exception:
        logger.debug("dgm_felt: scorecard run failed", exc_info=True)
        result.signals.append("scorecard_check_failed")

    # ── All green? small positive restoration ──────────────────
    if result.integrity_ok and result.probes_ok and not result.signals:
        result.signals.append("all_green")
        result.safety_delta += +0.02

    # ── Clamp per-call ──────────────────────────────────────────
    delta = max(-_PER_CALL_MAX_ABS, min(_PER_CALL_MAX_ABS, result.safety_delta))
    result.safety_delta = delta

    # ── Apply to homeostasis ────────────────────────────────────
    try:
        variables = h.variables
        current = float(variables.get("safety", 0.8))
        new_value = max(0.0, min(1.0, current + delta))
        variables["safety"] = round(new_value, 4)
    except Exception:
        logger.debug("dgm_felt: homeostasis update failed",
                     exc_info=True)
        result.reason = "homeostasis update failed"
        return result

    # ── Narrative audit log ─────────────────────────────────────
    try:
        if narrative_audit_fn is None:
            from app.subia.safety.narrative_audit import append_audit
            narrative_audit_fn = append_audit
        severity = "info" if delta >= 0 else (
            "drift" if delta <= -0.15 else "warn"
        )
        narrative_audit_fn(
            finding=(
                f"DGM felt-constraint: safety {delta:+.2f} "
                f"(signals: {', '.join(result.signals) or 'none'})"
            ),
            loop_count=int(getattr(kernel, "loop_count", 0)),
            sources=["dgm_felt_constraint"],
            severity=severity,
        )
    except Exception:
        logger.debug("dgm_felt: audit append failed", exc_info=True)

    result.reason = "applied"
    return result
