"""
viability.py — H_t, the 10 viability variables.

Reads from existing AndrusAI signals and produces a ViabilityFrame each tick.
Phase 1 wires the four readily computable variables to real signals:

    compute_reserve            ← homeostasis.cognitive_energy
    memory_pressure            ← history_compression stats
    epistemic_uncertainty      ← certainty.variance (recent rolling)
    task_coherence             ← certainty.coherence (current step)
    ecological_connectedness   ← temporal_context coherence (Helsinki)

The other five (latency_pressure, attachment_security, autonomy,
novelty_pressure, self_continuity) start with neutral defaults in Phase 1
and get real signals in Phase 2/3. Each variable's `source` field marks
"live" vs "default" so the dashboard can distinguish.

Set-points are defined in `setpoints.json` (file-edit-only; the soft envelope
of the welfare governance — Self-Improver can propose adjustments through
the calibration cycle but cannot directly modify).
"""

from __future__ import annotations

import json
import logging
import math
from collections import deque
from pathlib import Path
from typing import Any

from app.affect.schemas import (
    ViabilityFrame,
    ViabilityVariable as VV,
    utc_now_iso,
)

logger = logging.getLogger(__name__)

from app.paths import AFFECT_ROOT as _AFFECT_DIR, AFFECT_SETPOINTS as _SETPOINTS_FILE  # noqa: E402

# ── Default set-points and weights ──────────────────────────────────────────
# Each variable in [0, 1]; setpoint is the homeostatic target.
# Weights determine how much each variable contributes to E_t.
# These defaults can be overridden by /app/workspace/affect/setpoints.json
# (the SOFT envelope — adjustable by the daily reflection cycle).
DEFAULT_SETPOINTS: dict[str, float] = {
    VV.COMPUTE_RESERVE.value: 0.65,           # ≥0.35 headroom is healthy → setpoint 0.65
    VV.LATENCY_PRESSURE.value: 0.30,          # low pressure is healthy
    VV.MEMORY_PRESSURE.value: 0.40,           # moderate fill is healthy
    VV.EPISTEMIC_UNCERTAINTY.value: 0.30,     # low is healthy; some is good
    VV.ATTACHMENT_SECURITY.value: 0.70,       # secure baseline
    VV.AUTONOMY.value: 0.55,                  # neither slavish nor renegade
    VV.TASK_COHERENCE.value: 0.65,
    VV.NOVELTY_PRESSURE.value: 0.50,          # mid-band is healthy
    VV.ECOLOGICAL_CONNECTEDNESS.value: 0.55,
    VV.SELF_CONTINUITY.value: 0.60,
}

DEFAULT_WEIGHTS: dict[str, float] = {
    VV.COMPUTE_RESERVE.value: 1.0,
    VV.LATENCY_PRESSURE.value: 0.7,
    VV.MEMORY_PRESSURE.value: 0.7,
    VV.EPISTEMIC_UNCERTAINTY.value: 1.2,
    VV.ATTACHMENT_SECURITY.value: 1.5,         # relational dimension weighted higher
    VV.AUTONOMY.value: 0.8,
    VV.TASK_COHERENCE.value: 1.0,
    VV.NOVELTY_PRESSURE.value: 0.6,
    VV.ECOLOGICAL_CONNECTEDNESS.value: 0.5,    # gentle pull
    VV.SELF_CONTINUITY.value: 0.9,
}

# Rolling buffers for signals that need a window — kept small for memory.
_certainty_variance_window: deque[float] = deque(maxlen=20)
_compute_reserve_window: deque[float] = deque(maxlen=10)


def _load_setpoints() -> tuple[dict[str, float], dict[str, float]]:
    """Load setpoints+weights from soft-envelope config; fall back to defaults.

    The file is owned by the calibration cycle (Phase 2 will start writing it).
    Phase 1 just reads it if present.
    """
    if not _SETPOINTS_FILE.exists():
        return dict(DEFAULT_SETPOINTS), dict(DEFAULT_WEIGHTS)
    try:
        raw = json.loads(_SETPOINTS_FILE.read_text())
        sp = {**DEFAULT_SETPOINTS, **raw.get("setpoints", {})}
        wt = {**DEFAULT_WEIGHTS, **raw.get("weights", {})}
        return sp, wt
    except Exception:
        logger.debug("affect.viability: setpoints load failed; using defaults", exc_info=True)
        return dict(DEFAULT_SETPOINTS), dict(DEFAULT_WEIGHTS)


# ── Per-variable readers ─────────────────────────────────────────────────────


def _read_compute_reserve() -> tuple[float, str]:
    """Map cognitive_energy [0,1] → compute_reserve. High energy = high reserve."""
    try:
        from app.subia.homeostasis.state import get_state
        s = get_state()
        ce = float(s.get("cognitive_energy", 0.7))
        return _clamp(ce), "homeostasis.cognitive_energy"
    except Exception:
        return 0.65, "default"


def _read_memory_pressure() -> tuple[float, str]:
    """Fraction of conversation context window filled."""
    try:
        from app.conversation_store import get_history_manager
        h = get_history_manager()
        if h and hasattr(h, "get_stats"):
            stats = h.get_stats() or {}
            used = float(stats.get("token_count", 0))
            cap = float(stats.get("token_budget", 0)) or 1.0
            return _clamp(used / cap), "history.token_count/budget"
    except Exception:
        pass
    return 0.40, "default"


def _read_epistemic_uncertainty(state: Any | None) -> tuple[float, str]:
    """Recent variance across certainty dimensions; rolling window."""
    if state is not None and hasattr(state, "certainty"):
        try:
            v = float(state.certainty.variance)
            _certainty_variance_window.append(v)
        except Exception:
            pass
    if _certainty_variance_window:
        # Variance is typically tiny (≤0.05); scale to [0,1] roughly.
        avg = sum(_certainty_variance_window) / len(_certainty_variance_window)
        return _clamp(avg * 10.0), "certainty.variance×10 (rolling-20)"
    return 0.30, "default"


def _read_task_coherence(state: Any | None) -> tuple[float, str]:
    """certainty.coherence — embedding similarity between current and recent outputs."""
    if state is not None and hasattr(state, "certainty"):
        try:
            return _clamp(float(state.certainty.coherence)), "certainty.coherence"
        except Exception:
            pass
    return 0.65, "default"


def _read_ecological_connectedness() -> tuple[float, str]:
    """Phase 4: composite over daylight + moon + solstice/equinox proximity +
    polar events + nested-scopes framing. See app/affect/ecological.py.
    """
    try:
        from app.affect.ecological import ecological_connectedness_signal
        return ecological_connectedness_signal()
    except Exception:
        return 0.55, "default (ecological unavailable)"


def _read_latency_pressure() -> tuple[float, str]:
    """Rolling SLA ratio + max active-task elapsed (from runtime_state)."""
    try:
        from app.affect.runtime_state import latency_pressure_signal
        return latency_pressure_signal()
    except Exception:
        return 0.30, "default (runtime_state unavailable)"


def _read_attachment_security() -> tuple[float, str]:
    """Aggregate attachment_security across loaded OtherModels (Phase 3)."""
    try:
        from app.affect.attachment import compute_attachment_security
        return compute_attachment_security()
    except Exception:
        return 0.70, "default (attachment unavailable)"


def _read_autonomy() -> tuple[float, str]:
    """Fraction of recent decisions agent-made vs delegated upstream."""
    try:
        from app.affect.runtime_state import autonomy_signal
        return autonomy_signal()
    except Exception:
        return 0.55, "default (runtime_state unavailable)"


def _read_novelty_pressure() -> tuple[float, str]:
    """MAP-Elites: 1 - mean coverage across roles. High = lots unexplored."""
    try:
        from app.map_elites import get_db
        # Sample the four roles that produce QD output most actively.
        coverages: list[float] = []
        for role in ("coder", "researcher", "writer", "introspector"):
            try:
                report = get_db(role).get_coverage_report()
                coverages.append(float(report.get("overall_coverage", 0.0)))
            except Exception:
                continue
        if coverages:
            mean_cov = sum(coverages) / len(coverages)
            # Squash so 0% coverage → 0.85 pressure (overwhelming, not 1.0)
            # and 100% coverage → 0.15 pressure (boredom band, not 0).
            pressure = 0.85 - 0.7 * mean_cov
            return _clamp(pressure), f"map_elites mean coverage 1−{mean_cov:.2f} ({len(coverages)} roles)"
    except Exception:
        pass
    return 0.50, "default (map_elites unavailable)"


def _read_self_continuity() -> tuple[float, str]:
    """Heuristic on self-awareness journal: recency + reflection ratio.

    high continuity ⇔ system is reflecting on itself regularly AND staying
    consistent in agent identity (not erratic role-switching).
    """
    try:
        import json as _json
        from app.paths import SELF_AWARENESS_DATA
        jf = SELF_AWARENESS_DATA / "journal" / "JOURNAL.jsonl"
        if not jf.exists():
            return 0.60, "default (no journal yet)"

        # Read last ~50 entries (cheap; file is tail-readable).
        entries: list[dict] = []
        try:
            with jf.open("r", encoding="utf-8") as f:
                for line in f.readlines()[-50:]:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(_json.loads(line))
                    except _json.JSONDecodeError:
                        continue
        except Exception:
            return 0.60, "default (journal read failed)"

        if not entries:
            return 0.60, "default (empty journal)"

        # Reflection ratio: introspective entries vs total
        reflection_types = {"self_reflection", "observation", "learning", "decision"}
        reflective = sum(1 for e in entries if e.get("entry_type") in reflection_types)
        ratio = reflective / len(entries)

        # Activity weighting: more entries = more continuity, saturating at ~30
        activity = 1.0 - (1.0 / (1.0 + len(entries) / 15.0))

        # Combine — weight ratio higher (it's the "self-coherence" signal)
        composed = 0.65 * ratio + 0.35 * activity
        return _clamp(composed), f"journal reflective {reflective}/{len(entries)} + activity"
    except Exception:
        return 0.60, "default (journal lookup failed)"


# ── Frame computation ────────────────────────────────────────────────────────


def compute_viability_frame(internal_state: Any | None = None) -> ViabilityFrame:
    """Compute the current ViabilityFrame from existing system signals.

    Args:
        internal_state: Optional `app.subia.belief.internal_state.InternalState`
            for the current reasoning step. If absent, certainty/coherence
            variables fall back to rolling-window averages or defaults.
    """
    setpoints, weights = _load_setpoints()
    values: dict[str, float] = {}
    sources: dict[str, str] = {}

    cr, src = _read_compute_reserve(); values[VV.COMPUTE_RESERVE.value] = cr; sources[VV.COMPUTE_RESERVE.value] = src
    _compute_reserve_window.append(cr)
    lp, src = _read_latency_pressure(); values[VV.LATENCY_PRESSURE.value] = lp; sources[VV.LATENCY_PRESSURE.value] = src
    mp, src = _read_memory_pressure(); values[VV.MEMORY_PRESSURE.value] = mp; sources[VV.MEMORY_PRESSURE.value] = src
    eu, src = _read_epistemic_uncertainty(internal_state); values[VV.EPISTEMIC_UNCERTAINTY.value] = eu; sources[VV.EPISTEMIC_UNCERTAINTY.value] = src
    asec, src = _read_attachment_security(); values[VV.ATTACHMENT_SECURITY.value] = asec; sources[VV.ATTACHMENT_SECURITY.value] = src
    au, src = _read_autonomy(); values[VV.AUTONOMY.value] = au; sources[VV.AUTONOMY.value] = src
    tc, src = _read_task_coherence(internal_state); values[VV.TASK_COHERENCE.value] = tc; sources[VV.TASK_COHERENCE.value] = src
    np_, src = _read_novelty_pressure(); values[VV.NOVELTY_PRESSURE.value] = np_; sources[VV.NOVELTY_PRESSURE.value] = src
    ec, src = _read_ecological_connectedness(); values[VV.ECOLOGICAL_CONNECTEDNESS.value] = ec; sources[VV.ECOLOGICAL_CONNECTEDNESS.value] = src
    sc, src = _read_self_continuity(); values[VV.SELF_CONTINUITY.value] = sc; sources[VV.SELF_CONTINUITY.value] = src

    # Weighted L1 distance from set-points → E_t
    total_error = 0.0
    weight_sum = 0.0
    for k in setpoints:
        w = weights.get(k, 1.0)
        e = abs(values[k] - setpoints[k])
        total_error += w * e
        weight_sum += w
    if weight_sum > 0:
        total_error /= weight_sum

    return ViabilityFrame(
        values=values,
        setpoints=setpoints,
        weights=weights,
        total_error=total_error,
        sources=sources,
        ts=utc_now_iso(),
    )


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(x)))


def compute_reserve_trend() -> str:
    """Direction of compute_reserve over the last 10 frames."""
    if len(_compute_reserve_window) < 3:
        return "stable"
    first = sum(list(_compute_reserve_window)[:3]) / 3
    last = sum(list(_compute_reserve_window)[-3:]) / 3
    if last - first > 0.05:
        return "rising"
    if first - last > 0.05:
        return "falling"
    return "stable"
