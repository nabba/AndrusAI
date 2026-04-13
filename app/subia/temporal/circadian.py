"""Circadian processing modes (Proposal §3.3).

Different homeostatic set-points and processing strategies depending on
local time of day. Modes are constants in this Tier-3 module so DGM /
Self-Improver cannot retune the windows in its own favour.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# Hour ranges are LOCAL time, half-open [start, end). 24h wrap supported.
CIRCADIAN_MODES: dict[str, dict] = {
    "active_hours": {
        "hours": (8, 20),
        "description": "Primary work period. Responsive, task-focused.",
        "homeostatic_setpoint_overrides": {
            "overload":         0.5,    # high tolerance — can absorb load
            "novelty_balance":  0.5,
            "progress":         0.7,
        },
        "reverie_allowed":      False,
        "cascade_preference":   "efficiency",
        "social_model_weight":  1.0,
        "wonder_threshold_delta": 0.0,
    },
    "deep_work_hours": {
        "hours": (20, 24),
        "description": "Deep analysis. Less responsive, more thorough.",
        "homeostatic_setpoint_overrides": {
            "overload":         0.4,
            "novelty_balance":  0.6,
            "progress":         0.5,
        },
        "reverie_allowed":      True,
        "cascade_preference":   "depth",
        "social_model_weight":  0.5,
        "wonder_threshold_delta": -0.1,   # easier to enter wonder
    },
    "consolidation_hours": {
        "hours": (0, 6),
        "description": "Memory consolidation. Minimal task execution.",
        "homeostatic_setpoint_overrides": {
            "overload":         0.3,
            "novelty_balance":  0.3,
            "progress":         0.3,
        },
        "reverie_allowed":      True,
        "cascade_preference":   "minimal",
        "social_model_weight":  0.1,
        "wonder_threshold_delta": -0.05,
        "special_processes": [
            "retrospective_memory_review",
            "wiki_lint",
            "understanding_passes",
            "shadow_analysis_prep",
        ],
    },
    "dawn_transition": {
        "hours": (6, 8),
        "description": "Session preparation. Refresh TSAL, load hot.md.",
        "homeostatic_setpoint_overrides": {
            "overload":         0.5,
            "novelty_balance":  0.4,
        },
        "reverie_allowed":      True,
        "cascade_preference":   "efficiency",
        "social_model_weight":  0.7,
        "wonder_threshold_delta": 0.0,
        "special_processes": [
            "load_hot_md",
            "refresh_tsal",
            "precompute_scene_candidates",
            "check_commitment_deadlines",
        ],
    },
}


def _hour_in_range(hour: int, hr_range: tuple) -> bool:
    start, end = hr_range
    if start <= end:
        return start <= hour < end
    # Wrap (e.g. 22-2)
    return hour >= start or hour < end


def current_circadian_mode(local_hour: Optional[int] = None) -> str:
    """Return the circadian mode for `local_hour` (0-23).

    If `local_hour` is None, reads the current time from
    `app.temporal_context.get_temporal_context()` (already cached
    per-minute, no extra cost).
    """
    if local_hour is None:
        try:
            from app.temporal_context import get_temporal_context
            tc = get_temporal_context()
            local_hour = int((tc.get("time_str") or "0:0").split(":")[0])
        except Exception:
            local_hour = 12  # default mid-day if temporal_context fails
    for name, mode in CIRCADIAN_MODES.items():
        if _hour_in_range(int(local_hour), mode["hours"]):
            return name
    return "active_hours"  # safe fallback


def apply_circadian_setpoints(homeostasis, mode: str) -> dict:
    """Override homeostatic set-points per the active circadian mode.

    Closed-loop wired: this is the BEHAVIOURAL CONSEQUENCE of mode
    selection. Without this call, the mode would be a label only.
    Returns the diff applied (var → new setpoint).
    """
    cfg = CIRCADIAN_MODES.get(mode, {})
    overrides = cfg.get("homeostatic_setpoint_overrides", {}) or {}
    diff: dict[str, float] = {}
    for var, sp in overrides.items():
        if var in homeostasis.set_points:
            if homeostasis.set_points[var] != sp:
                diff[var] = sp
                homeostasis.set_points[var] = sp
    return diff


def circadian_allows_reverie(mode: str) -> bool:
    return bool(CIRCADIAN_MODES.get(mode, {}).get("reverie_allowed", False))


def circadian_cascade_preference(mode: str) -> str:
    return CIRCADIAN_MODES.get(mode, {}).get("cascade_preference", "efficiency")


def circadian_wonder_threshold_delta(mode: str) -> float:
    return float(CIRCADIAN_MODES.get(mode, {}).get("wonder_threshold_delta", 0.0))


def circadian_special_processes(mode: str) -> list:
    return list(CIRCADIAN_MODES.get(mode, {}).get("special_processes", []))
