"""SpeciousPresent — Husserl/James felt-now with retention + primal + protention.

The kernel state is a snapshot AT time T. The SpeciousPresent makes
T-1, T-2, T-3 SIMULTANEOUSLY PRESENT alongside T (retention) and a
short-horizon prediction of T+1 (protention). All three temporal layers
are processed at once in the next CIL loop.

This is NOT a log. The dual-tier memory consolidator already records
history. The specious present is *experienced state* — it sits on the
kernel, not in storage, and gets injected into every context block
together with the current scene.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# Default retention depth — Proposal §3.1 ("Last 2-3 CIL loop states").
_DEFAULT_RETENTION_DEPTH = 3


@dataclass
class KernelMoment:
    """A compressed kernel snapshot held in retention.

    Not a full kernel — only the deltas that distinguish it from the
    moment before it. Storage cost is bounded.
    """
    loop_count: int
    timestamp: str
    scene_delta: dict = field(default_factory=dict)
    # {"entered": [item_ids], "exited": [item_ids],
    #  "salience_changes": {item_id: delta}}
    homeostatic_delta: dict = field(default_factory=dict)
    # {var: signed_delta}
    prediction_outcome: Optional[str] = None  # 'confirmed' | 'violated' | None
    affect_trajectory: str = "stable"          # 'rising' | 'falling' | 'stable'


@dataclass
class SpeciousPresent:
    """The felt now — temporal width."""
    retention: list = field(default_factory=list)        # [KernelMoment], oldest first
    current: dict = field(default_factory=dict)          # primal impression snapshot
    protention: dict = field(default_factory=dict)       # next-moment forecast
    tempo: float = 0.0                                   # 0.0 (slow) – 1.0 (fast)
    direction: str = "stable"                            # 'trending_positive' |
                                                         # 'trending_negative' |
                                                         # 'stable' | 'turbulent'
    retention_depth: int = _DEFAULT_RETENTION_DEPTH

    def is_empty(self) -> bool:
        return not self.retention and not self.current

    def lingering_items(self) -> set:
        """Items that just exited the focal scene (in retention but not current)."""
        retained_ids: set = set()
        for moment in self.retention:
            retained_ids.update(moment.scene_delta.get("entered", []) or [])
        current_ids = set((self.current or {}).get("focal_item_ids", []) or [])
        return retained_ids - current_ids

    def stable_items(self) -> set:
        """Items present in EVERY retention frame + current (persistent)."""
        if not self.retention:
            return set()
        per_frame = []
        for moment in self.retention:
            per_frame.append(set(moment.scene_delta.get("entered", []) or []))
        if not per_frame:
            return set()
        intersection = per_frame[0]
        for s in per_frame[1:]:
            intersection &= s
        intersection &= set((self.current or {}).get("focal_item_ids", []) or [])
        return intersection


# ─────────────────────────────────────────────────────────────────────
# Pure reducers
# ─────────────────────────────────────────────────────────────────────

def _scene_delta(prev_focal_ids: set, curr_focal_ids: set) -> dict:
    return {
        "entered": sorted(curr_focal_ids - prev_focal_ids),
        "exited":  sorted(prev_focal_ids - curr_focal_ids),
        "stayed":  sorted(prev_focal_ids & curr_focal_ids),
    }


def _homeostatic_delta(prev: dict, curr: dict) -> dict:
    out: dict[str, float] = {}
    for var, val in (curr or {}).items():
        prev_val = float((prev or {}).get(var, val))
        delta = round(float(val) - prev_val, 4)
        if abs(delta) >= 1e-4:
            out[var] = delta
    return out


def _derive_tempo(retention: list) -> float:
    """Tempo = normalised scene-turnover rate over the retention window.

    Many entries/exits per retention frame ⇒ tempo near 1.0 (fast).
    Few changes ⇒ tempo near 0.0 (slow).
    """
    if not retention:
        return 0.0
    total_changes = 0
    for m in retention:
        sd = m.scene_delta or {}
        total_changes += len(sd.get("entered", [])) + len(sd.get("exited", []))
    # Normalise: 6 changes per frame on average ≈ tempo 1.0.
    return round(min(1.0, total_changes / max(1, len(retention)) / 6.0), 4)


def _derive_direction(homeostatic_delta: dict) -> str:
    """Aggregate direction across favourable and unfavourable variables."""
    if not homeostatic_delta:
        return "stable"
    POS_VARS = {"coherence", "progress", "trustworthiness",
                "social_alignment", "self_coherence"}
    NEG_VARS = {"contradiction_pressure", "overload"}
    score = 0.0
    for var, delta in homeostatic_delta.items():
        if var in POS_VARS:
            score += delta
        elif var in NEG_VARS:
            score -= delta
    # Turbulence: large absolute changes, small net direction
    abs_total = sum(abs(d) for d in homeostatic_delta.values())
    if abs_total > 0.4 and abs(score) < 0.1:
        return "turbulent"
    if score > 0.1:
        return "trending_positive"
    if score < -0.1:
        return "trending_negative"
    return "stable"


def update_specious_present(
    kernel,
    *,
    previous_focal_ids: Optional[set] = None,
    previous_homeostasis: Optional[dict] = None,
    protention_forecast: Optional[dict] = None,
) -> SpeciousPresent:
    """Refresh the kernel's specious_present in place.

    Called by the temporal hook at the start of every CIL loop. Pure
    function over the kernel + the previous moment's signals.
    """
    sp: SpeciousPresent = getattr(kernel, "specious_present", None) or SpeciousPresent()
    curr_focal_ids = {i.id for i in kernel.focal_scene()}
    prev_focal = previous_focal_ids if previous_focal_ids is not None else curr_focal_ids
    prev_homeo = previous_homeostasis if previous_homeostasis is not None else dict(kernel.homeostasis.variables)

    # 1. Push the most recent moment into retention
    moment = KernelMoment(
        loop_count=kernel.loop_count,
        timestamp=kernel.last_loop_at or "",
        scene_delta=_scene_delta(prev_focal, curr_focal_ids),
        homeostatic_delta=_homeostatic_delta(prev_homeo, kernel.homeostasis.variables),
        affect_trajectory=_derive_direction(
            _homeostatic_delta(prev_homeo, kernel.homeostasis.variables)
        ),
    )
    sp.retention.append(moment)
    if len(sp.retention) > sp.retention_depth:
        sp.retention = sp.retention[-sp.retention_depth:]

    # 2. Primal impression — the current snapshot
    sp.current = {
        "loop_count": kernel.loop_count,
        "focal_item_ids": sorted(curr_focal_ids),
        "homeostasis": dict(kernel.homeostasis.variables),
    }

    # 3. Protention — forecast supplied by the caller (typically the
    #    Predictor's most recent prediction).
    if protention_forecast is not None:
        sp.protention = dict(protention_forecast)

    # 4. Derive tempo + direction from retention
    sp.tempo = _derive_tempo(sp.retention)
    sp.direction = _derive_direction(moment.homeostatic_delta)

    kernel.specious_present = sp
    return sp
