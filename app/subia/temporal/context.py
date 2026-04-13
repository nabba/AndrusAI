"""TemporalContext — aggregate of clock + circadian + density + rhythms.

Lives on the kernel as `kernel.temporal_context`. Refreshed by the
temporal hook on every loop. Built ON `app.temporal_context` for clock
data — does not duplicate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .circadian import current_circadian_mode, circadian_allows_reverie, circadian_cascade_preference
from .density import DensitySample, compute_processing_density, density_describes_subjective_time


@dataclass
class TemporalContext:
    # Clock (sourced from app.temporal_context)
    current_time: str = ""
    current_date: str = ""
    weekday: str = ""
    season: str = ""
    tz_name: str = ""
    local_hour: int = 0

    # Circadian
    circadian_mode: str = "active_hours"
    cascade_preference: str = "efficiency"
    reverie_allowed: bool = False

    # Felt time
    processing_density: float = 0.0
    subjective_time: str = "subjectively routine"
    loops_today: int = 0
    average_loop_duration_ms: float = 0.0
    idle_seconds: float = 0.0

    # Discovered rhythms (filled by rhythm_discovery on slow cadence)
    external_rhythms: list = field(default_factory=list)

    # Andrus rhythm summary (derived if interaction log available)
    andrus_active: bool = False
    andrus_typical_active_hours: list = field(default_factory=list)


def refresh_temporal_context(
    kernel,
    *,
    density_sample: Optional[DensitySample] = None,
    rhythms: Optional[list] = None,
    clock_provider=None,
) -> TemporalContext:
    """Refresh `kernel.temporal_context` in place. Returns the context.

    `clock_provider` is injectable for tests; production reads from
    `app.temporal_context.get_temporal_context()`.
    """
    tc: TemporalContext = getattr(kernel, "temporal_context", None) or TemporalContext()

    # Clock data
    clock = {}
    if clock_provider is not None:
        try:
            clock = clock_provider() or {}
        except Exception:
            clock = {}
    else:
        try:
            from app.temporal_context import get_temporal_context
            clock = get_temporal_context() or {}
        except Exception:
            clock = {}

    tc.current_date = clock.get("date_str", tc.current_date)
    tc.current_time = clock.get("time_str", tc.current_time)
    tc.weekday      = clock.get("weekday",  tc.weekday)
    tc.season       = clock.get("season",   tc.season)
    tc.tz_name      = clock.get("tz_name",  tc.tz_name)
    try:
        tc.local_hour = int((clock.get("time_str") or "0:0").split(":")[0])
    except Exception:
        pass

    # Circadian
    tc.circadian_mode = current_circadian_mode(tc.local_hour)
    tc.reverie_allowed = circadian_allows_reverie(tc.circadian_mode)
    tc.cascade_preference = circadian_cascade_preference(tc.circadian_mode)

    # Felt time
    if density_sample is not None:
        tc.processing_density = compute_processing_density(density_sample)
        tc.subjective_time = density_describes_subjective_time(tc.processing_density)

    # Rhythms (from caller — rhythm_discovery is a slow IdleScheduler job)
    if rhythms is not None:
        tc.external_rhythms = list(rhythms)
        # Surface Andrus's typical active hours
        for r in rhythms:
            if getattr(r, "kind", None) == "andrus":
                tc.andrus_typical_active_hours = list(r.typical_hours)
                if tc.local_hour in r.typical_hours:
                    tc.andrus_active = True
                break

    kernel.temporal_context = tc
    return tc
