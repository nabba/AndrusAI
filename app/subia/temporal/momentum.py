"""Homeostatic momentum — per-variable trajectory (rising/falling/stable + rate).

The system feels not just WHERE its variables ARE but WHERE THEY'RE
GOING (Proposal §3.1). Cheap to compute, large effect on context
texture: `coherence:0.45↓` reads very differently from `coherence:0.45↑`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# Stability threshold: deltas below this count as stable (noise floor).
_STABLE_EPS = 0.02


@dataclass
class MomentumEntry:
    direction: str         # 'rising' | 'falling' | 'stable'
    rate: float            # |delta| since previous tick
    previous: float        # previous value (for downstream reuse)


def _classify(delta: float) -> str:
    if delta > _STABLE_EPS:
        return "rising"
    if delta < -_STABLE_EPS:
        return "falling"
    return "stable"


def update_momentum(
    homeostasis,
    *,
    previous_values: Optional[dict] = None,
) -> dict:
    """Refresh `homeostasis.momentum` in place. Returns the momentum dict.

    Idempotent. Safe to call when `previous_values` is None — every
    variable is then classified as 'stable' with rate 0.0.
    """
    prev = previous_values or {}
    out: dict[str, dict] = {}
    for var, val in homeostasis.variables.items():
        try:
            curr = float(val)
        except (TypeError, ValueError):
            continue
        prev_val = float(prev.get(var, curr))
        delta = curr - prev_val
        out[var] = {
            "direction": _classify(delta),
            "rate": round(abs(delta), 4),
            "previous": round(prev_val, 4),
        }
    homeostasis.momentum = out
    return out


# ── Rendering helpers (used by compact context injection) ────────────

_ARROW = {"rising": "↑", "falling": "↓", "stable": "→"}


def render_momentum_arrows(homeostasis, *, vars_to_render: Optional[list] = None) -> str:
    """Format `var:value↑/↓/→ ...` — Proposal §3.1 example output."""
    parts = []
    selected = vars_to_render or list(homeostasis.variables.keys())
    for var in selected:
        if var not in homeostasis.variables:
            continue
        val = homeostasis.variables[var]
        m = (homeostasis.momentum or {}).get(var, {})
        arrow = _ARROW.get(m.get("direction", "stable"), "→")
        parts.append(f"{var}:{float(val):+.2f}{arrow}")
    return " ".join(parts)
