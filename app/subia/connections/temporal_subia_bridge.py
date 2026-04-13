"""Temporal → SubIA bridges (Phase 14).

Five closed-loop bridges:
  1. circadian_to_setpoints          — circadian mode shifts homeostasis
                                       set-points (active vs deep-work
                                       vs consolidation tolerances).
  2. density_to_wonder_threshold     — felt density lowers the wonder
                                       threshold so dense periods make
                                       wonder easier to enter.
  3. circadian_to_idle_scheduler     — gates Reverie/Understanding/
                                       Shadow by circadian mode (no
                                       reverie during active hours).
  4. specious_present_to_context     — renders the felt-now as a
                                       compact context block (~80 tok)
                                       for prompt injection.
  5. rhythm_discovery_to_self_state  — TSAL-style: discovered rhythms
                                       become self_state.capabilities
                                       entries with `discovered=True`.

Closed-loop discipline: every signal has a behavioural consequence.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.subia.kernel import SubjectivityKernel
from app.subia.config import SUBIA_CONFIG

logger = logging.getLogger(__name__)


# ── 1. Circadian → setpoints ─────────────────────────────────────────

def circadian_to_setpoints(kernel: SubjectivityKernel) -> dict:
    """Apply current circadian mode's set-point overrides. Returns diff."""
    from app.subia.temporal.circadian import (
        current_circadian_mode, apply_circadian_setpoints,
    )
    tc = getattr(kernel, "temporal_context", None)
    mode = tc.circadian_mode if tc else current_circadian_mode()
    return apply_circadian_setpoints(kernel.homeostasis, mode)


# ── 2. Density → wonder threshold ────────────────────────────────────

def density_to_wonder_threshold(kernel: SubjectivityKernel) -> float:
    """Compute and return an EFFECTIVE wonder threshold by combining
    the SUBIA_CONFIG base, the circadian delta, and the density delta.
    The Wonder Register reads this through `effective_wonder_threshold`.
    """
    from app.subia.temporal.density import density_to_wonder_threshold_delta
    from app.subia.temporal.circadian import circadian_wonder_threshold_delta
    base = float(SUBIA_CONFIG.get("WONDER_INHIBIT_THRESHOLD", 0.3))
    tc = getattr(kernel, "temporal_context", None)
    if tc is None:
        return base
    delta = (density_to_wonder_threshold_delta(tc.processing_density)
             + circadian_wonder_threshold_delta(tc.circadian_mode))
    return round(max(0.05, min(0.9, base + delta)), 4)


def effective_wonder_threshold(kernel: SubjectivityKernel) -> float:
    return density_to_wonder_threshold(kernel)


# ── 3. Circadian → idle scheduler gating ────────────────────────────

def circadian_should_run_reverie(kernel: SubjectivityKernel) -> bool:
    from app.subia.temporal.circadian import circadian_allows_reverie
    tc = getattr(kernel, "temporal_context", None)
    return circadian_allows_reverie(tc.circadian_mode if tc else "active_hours")


def circadian_special_processes_now(kernel: SubjectivityKernel) -> list:
    from app.subia.temporal.circadian import circadian_special_processes
    tc = getattr(kernel, "temporal_context", None)
    return circadian_special_processes(tc.circadian_mode if tc else "active_hours")


# ── 4. Specious present → compact context block ─────────────────────

def render_specious_present_block(kernel: SubjectivityKernel) -> str:
    """One-paragraph injection describing the felt-now (~80 tokens)."""
    sp = getattr(kernel, "specious_present", None)
    if sp is None or sp.is_empty():
        return ""
    from app.subia.temporal.momentum import render_momentum_arrows
    lines = ["[Felt-now]"]
    # Retention: what just happened
    if sp.retention:
        last = sp.retention[-1]
        sd = last.scene_delta or {}
        if sd.get("entered"):
            lines.append(f"  just entered: {', '.join(sd['entered'][:3])}")
        if sd.get("exited"):
            lines.append(f"  just exited: {', '.join(sd['exited'][:3])} (lingering)")
    # Tempo + direction
    lines.append(f"  tempo={sp.tempo:.2f} direction={sp.direction}")
    # Momentum arrows
    arrows = render_momentum_arrows(kernel.homeostasis,
                                    vars_to_render=["coherence", "progress",
                                                     "novelty_balance",
                                                     "contradiction_pressure"])
    if arrows:
        lines.append(f"  H: {arrows}")
    # Protention
    if sp.protention:
        nxt = sp.protention.get("next_scene_change") or sp.protention.get("predicted_next_task_type")
        if nxt:
            lines.append(f"  protention: {nxt}")
    # Temporal context summary
    tc = getattr(kernel, "temporal_context", None)
    if tc:
        lines.append(
            f"  clock: {tc.current_time} {tc.tz_name} ({tc.circadian_mode}); "
            f"{tc.subjective_time}"
        )
    return "\n".join(lines)


# ── 5. Rhythm discovery → self_state.capabilities ───────────────────

def rhythms_to_self_state(kernel: SubjectivityKernel, rhythms: list) -> int:
    """Like the TSAL bridge: capabilities populated with `discovered=True`."""
    if not rhythms:
        return 0
    n = 0
    for r in rhythms:
        key = f"rhythm:{r.kind}:{r.name}"
        new = {
            "name": r.name,
            "frequency": r.frequency,
            "typical_hours": r.typical_hours,
            "typical_weekdays": r.typical_weekdays,
            "sample_size": r.sample_size,
            "confidence": r.confidence,
            "discovered": True,
        }
        if kernel.self_state.capabilities.get(key) != new:
            kernel.self_state.capabilities[key] = new
            n += 1
    return n


# ── Predictor enrichment ────────────────────────────────────────────

def enrich_prediction_with_temporal_context(
    base_prompt: str,
    kernel: SubjectivityKernel,
) -> str:
    sp = getattr(kernel, "specious_present", None)
    tc = getattr(kernel, "temporal_context", None)
    if sp is None and tc is None:
        return base_prompt
    bits = ["\n\nTemporal context for this prediction:"]
    if tc:
        bits.append(
            f"- Circadian mode: {tc.circadian_mode} "
            f"(cascade preference: {tc.cascade_preference})"
        )
        bits.append(f"- Subjective time: {tc.subjective_time}")
        if tc.andrus_active:
            bits.append("- Andrus is in his typical active hours.")
    if sp and sp.retention:
        bits.append(f"- Tempo {sp.tempo:.2f}, direction {sp.direction}")
    bits.append(
        "Prefer predictions that respect rate-of-change, not just current values."
    )
    return base_prompt + "\n".join(bits)
