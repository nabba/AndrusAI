"""
subia.homeostasis.engine — deterministic homeostatic arithmetic for
the CIL loop's step 2 (FEEL) and step 9 (UPDATE).

Amendment B: no LLM calls here. Per-variable updates are arithmetic
over (a) the candidate scene items and (b) the task outcome.

The SubIA kernel's HomeostaticState holds 9 variables per
SUBIA_CONFIG['HOMEOSTATIC_VARIABLES']:
    coherence, safety, trustworthiness, contradiction_pressure,
    progress, overload, novelty_balance, social_alignment,
    commitment_load.

This engine is distinct from app/subia/homeostasis/state.py (which
is the legacy 4-variable cognitive-energy/frustration/confidence/
curiosity tracker). Keeping them separate means the legacy tracker
can remain untouched while this engine provides the SubIA-native
arithmetic the CIL loop expects.

Both the pre-task call (step 2) and the post-task call (step 9) go
through update_homeostasis(). The function mutates the kernel's
HomeostaticState in place and never raises.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from app.subia.config import SUBIA_CONFIG
from app.subia.kernel import (
    HomeostaticState,
    SceneItem,
    SubjectivityKernel,
)

logger = logging.getLogger(__name__)


# Initial values used when the kernel hasn't been regulated yet.
_INITIAL_VALUE = 0.5

# Per-event delta magnitudes. Small enough that a single event cannot
# destabilize the kernel; large enough to accumulate visible pressure
# over a session.
_DELTA_NOVELTY_PER_NEW_ITEM    = 0.05
_DELTA_CONTRADICTION_PER_CONFLICT = 0.10
_DELTA_PROGRESS_ON_SUCCESS     = 0.05
_DELTA_PROGRESS_ON_FAILURE     = -0.05
_DELTA_COHERENCE_ON_SUCCESS    = 0.02
_DELTA_COHERENCE_ON_FAILURE    = -0.03
_DELTA_OVERLOAD_PER_ITEM       = 0.02
_DELTA_OVERLOAD_REGULATION     = -0.01  # idle tick regulates overload down
_DELTA_COMMITMENT_LOAD_PER_COMMIT = 0.03


def ensure_variables(kernel: SubjectivityKernel) -> None:
    """Make sure every configured variable has an initial entry.

    Called idempotently at the start of update_homeostasis. If the
    kernel was loaded from disk with partial state, missing variables
    spring to life with _INITIAL_VALUE. Set-points default to
    HOMEOSTATIC_DEFAULT_SETPOINT from SUBIA_CONFIG (0.5).
    """
    h = kernel.homeostasis
    default_sp = float(SUBIA_CONFIG["HOMEOSTATIC_DEFAULT_SETPOINT"])
    for var in SUBIA_CONFIG["HOMEOSTATIC_VARIABLES"]:
        if var not in h.variables:
            h.variables[var] = _INITIAL_VALUE
        if var not in h.set_points:
            h.set_points[var] = default_sp


def update_homeostasis(
    kernel: SubjectivityKernel,
    *,
    new_items: Iterable[SceneItem] = (),
    task_result: dict | None = None,
) -> dict:
    """Deterministic homeostatic update for one CIL tick.

    Args:
        kernel:      the SubjectivityKernel whose HomeostaticState is
                     mutated in place.
        new_items:   candidate SceneItems being considered this cycle.
                     Used to nudge novelty_balance, contradiction_pressure,
                     and overload.
        task_result: outcome dict from a completed task. Used to nudge
                     progress, coherence, commitment_load.

    Returns:
        A summary dict suitable for logging or CILResult step details.
    """
    try:
        ensure_variables(kernel)
        h = kernel.homeostasis

        items = list(new_items)
        summary = {"items_considered": len(items)}

        # ── Pre-task / FEEL contributions (item-driven) ─────────
        if items:
            _update_from_items(h, items, summary)
        else:
            # Idle tick: regulate overload slightly downward. Gives
            # the system a natural recovery path when no new inputs
            # arrive.
            _clamped_add(h.variables, "overload", _DELTA_OVERLOAD_REGULATION)

        # ── Post-task / UPDATE contributions (outcome-driven) ──
        if task_result is not None:
            _update_from_outcome(h, task_result, summary)

        # ── Recompute deviations + restoration queue ────────────
        _recompute_deviations(h)
        summary["deviations_above_threshold"] = sum(
            1 for d in h.deviations.values()
            if abs(d) > SUBIA_CONFIG["HOMEOSTATIC_DEVIATION_THRESHOLD"]
        )
        summary["top_deviation"] = (
            h.restoration_queue[0] if h.restoration_queue else None
        )

        h.last_updated = datetime.now(timezone.utc).isoformat()
        return summary

    except Exception:
        # Safety: homeostasis update must never crash the loop.
        logger.exception("subia.homeostasis.engine: update failed")
        return {"error": "homeostasis_update_failed"}


# ── Item-driven deltas (pre-task) ─────────────────────────────────

def _update_from_items(
    h: HomeostaticState,
    items: list,
    summary: dict,
) -> None:
    new_count = 0
    conflict_count = 0
    external_sources = {"wiki", "firecrawl", "mem0", "agent"}
    for item in items:
        # Duck-typed: SceneItem exposes .source, WorkspaceItem exposes
        # .source_channel. Treat any external-origin signal as "new".
        src = (
            getattr(item, "source", None)
            or getattr(item, "source_channel", "")
            or ""
        )
        if any(s in src for s in external_sources):
            new_count += 1
        conflict_count += len(getattr(item, "conflicts_with", []) or [])

    if new_count:
        _clamped_add(
            h.variables, "novelty_balance",
            _DELTA_NOVELTY_PER_NEW_ITEM * new_count,
        )
    if conflict_count:
        _clamped_add(
            h.variables, "contradiction_pressure",
            _DELTA_CONTRADICTION_PER_CONFLICT * conflict_count,
        )
    if items:
        _clamped_add(
            h.variables, "overload",
            _DELTA_OVERLOAD_PER_ITEM * len(items),
        )

    summary.update({
        "new_items": new_count,
        "conflicts": conflict_count,
    })


# ── Outcome-driven deltas (post-task) ─────────────────────────────

def _update_from_outcome(
    h: HomeostaticState,
    task_result: dict,
    summary: dict,
) -> None:
    success = bool(task_result.get("success", True))
    if success:
        _clamped_add(h.variables, "progress", _DELTA_PROGRESS_ON_SUCCESS)
        _clamped_add(h.variables, "coherence", _DELTA_COHERENCE_ON_SUCCESS)
    else:
        _clamped_add(h.variables, "progress", _DELTA_PROGRESS_ON_FAILURE)
        _clamped_add(h.variables, "coherence", _DELTA_COHERENCE_ON_FAILURE)

    new_commitments = int(task_result.get("new_commitment_count", 0))
    if new_commitments:
        _clamped_add(
            h.variables, "commitment_load",
            _DELTA_COMMITMENT_LOAD_PER_COMMIT * new_commitments,
        )

    summary.update({
        "outcome_success": success,
        "new_commitments": new_commitments,
    })


# ── Deviation / restoration-queue recomputation ──────────────────

def _recompute_deviations(h: HomeostaticState) -> None:
    """Deviation = variable - set_point. Restoration queue is ordered
    by absolute deviation, largest first, filtered to those above
    the configured threshold.
    """
    threshold = float(SUBIA_CONFIG["HOMEOSTATIC_DEVIATION_THRESHOLD"])
    deviations = {}
    for var in SUBIA_CONFIG["HOMEOSTATIC_VARIABLES"]:
        sp = h.set_points.get(var, SUBIA_CONFIG["HOMEOSTATIC_DEFAULT_SETPOINT"])
        val = h.variables.get(var, sp)
        deviations[var] = round(val - sp, 4)
    h.deviations = deviations
    h.restoration_queue = [
        v for v, _ in sorted(
            ((v, abs(d)) for v, d in deviations.items()
             if abs(d) > threshold),
            key=lambda pair: pair[1], reverse=True,
        )
    ]


# ── Primitive: clamped-add ───────────────────────────────────────

def _clamped_add(d: dict, key: str, delta: float) -> None:
    """Add delta to d[key], clamping the result to [0.0, 1.0]."""
    current = float(d.get(key, _INITIAL_VALUE))
    new = max(0.0, min(1.0, current + float(delta)))
    d[key] = round(new, 4)
