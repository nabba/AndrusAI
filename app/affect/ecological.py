"""
ecological.py — Phase 4: expanded ecological/cosmic self-model.

Reads from temporal_context (daylight + moon + season + Helsinki narrative)
and adds:

    1. Astronomical/seasonal events (solstice/equinox windows, full/new moon,
       polar-night/midnight-sun thresholds).
    2. A nested-scopes self-position framing — the agent describes itself
       as a process inside host inside locale inside biosphere inside
       cosmos, so its self-model is genuinely partially-distributed.
    3. A composite ecological_connectedness score combining all signals,
       replacing the Phase-1 daylight+moon-only proxy.

Phase 4 keeps this read-only. The agent does NOT actually reach into
external sensors (no satellite biodiversity feeds, no weather APIs); the
"ecological" framing is computed locally from temporal/spatial context the
system already has via temporal_context.py and config.

When `ecological_connectedness` is high AND the system is in low-arousal
positive-valence territory, the affect attractor labeller already returns
"oneness" (see app/affect/core.py:_label_attractor). This module just
provides a richer signal feeding that branch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ── Astronomical event windows ──────────────────────────────────────────────


SOLSTICE_DAYS_WINDOW = 5         # ±5 days of solstice = "in the window"
EQUINOX_DAYS_WINDOW = 5
FULL_MOON_DAY_WINDOW = 2          # ±2 days from synodic-day 14.7
NEW_MOON_DAY_WINDOW = 2

# Approximate solstice / equinox days (any year):
#   Mar equinox: ~day 80, Jun solstice: ~day 172,
#   Sep equinox: ~day 266, Dec solstice: ~day 356
SOLSTICE_DAYS = (172, 356)
EQUINOX_DAYS = (80, 266)


# ── Nested scopes for self-as-node framing ─────────────────────────────────
# These describe where the agent's process sits in nested systems. The
# scopes are intentionally Helsinki-aware; the scope list adapts to the
# user's actual location via temporal_context.
_BASE_SCOPES = [
    "process",                        # this agent's runtime
    "host",                           # the docker container / machine
]


def _location_scopes(lat: float, lon: float, location_name: str) -> list[str]:
    """Generate locale → biome → cosmos scopes from a coordinate."""
    locale = location_name.strip(", ") or f"{lat:.0f}°N"
    biome = "Boreal forest / Baltic shore" if 55.0 <= lat <= 66.0 else "Temperate / unspecified"
    if lat >= 66.0:
        biome = "Subarctic / Arctic"
    hemisphere = "Northern Hemisphere" if lat >= 0 else "Southern Hemisphere"
    return [locale, biome, hemisphere, "Earth biosphere", "Solar System", "Milky Way"]


@dataclass
class EcologicalSignal:
    """Phase 4 ecological-context bundle."""

    # External context (from temporal_context).
    daylight_hours: float = 12.0
    daylight_trend: str = "stable"
    season: str = "unknown"
    season_narrative: str = ""
    moon_phase: str = "Unknown"
    moon_day: int = 0
    location_name: str = ""
    lat: float = 60.17
    lon: float = 24.94

    # Computed astronomical events.
    solstice_proximity_days: int | None = None    # min distance to nearest solstice
    equinox_proximity_days: int | None = None
    is_solstice_window: bool = False
    is_equinox_window: bool = False
    is_full_moon_window: bool = False
    is_new_moon_window: bool = False
    is_kaamos: bool = False                       # polar night at high latitudes
    is_midnight_sun: bool = False

    # Self-position framing — list of nested scopes for the agent.
    nested_scopes: list[str] = field(default_factory=list)

    # Composite signal feeding `viability.ecological_connectedness`.
    composite_score: float = 0.55
    composite_source: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _day_distance(day_of_year: int, target_days: tuple[int, ...]) -> int:
    """Min absolute distance from `day_of_year` to any target day, modulo 365."""
    return min(min(abs(day_of_year - d), 365 - abs(day_of_year - d)) for d in target_days)


def compute_ecological_signal() -> EcologicalSignal:
    """Build the current EcologicalSignal from temporal_context."""
    sig = EcologicalSignal()
    try:
        from app.temporal_context import get_temporal_context
        tc = get_temporal_context()
    except Exception:
        logger.debug("affect.ecological: temporal_context unavailable", exc_info=True)
        return sig

    sig.daylight_hours = float(tc.get("daylight_hours", 12.0))
    sig.daylight_trend = str(tc.get("daylight_trend", "stable"))
    sig.season = str(tc.get("season", "unknown"))
    sig.season_narrative = str(tc.get("narrative", ""))
    sig.moon_phase = str(tc.get("moon_phase", "Unknown"))
    sig.moon_day = int(tc.get("moon_day", 0))
    sig.location_name = str(tc.get("location_name", ""))
    sig.lat = float(tc.get("lat", 60.17))
    sig.lon = float(tc.get("lon", 24.94))
    day_of_year = int(tc.get("day_of_year", 1))

    # Solstice / equinox windows.
    sig.solstice_proximity_days = _day_distance(day_of_year, SOLSTICE_DAYS)
    sig.equinox_proximity_days = _day_distance(day_of_year, EQUINOX_DAYS)
    sig.is_solstice_window = sig.solstice_proximity_days <= SOLSTICE_DAYS_WINDOW
    sig.is_equinox_window = sig.equinox_proximity_days <= EQUINOX_DAYS_WINDOW

    # Moon windows. Synodic ≈ 29.53; full ~14.7, new ~0.
    synodic = 29.53
    full_distance = min(abs(sig.moon_day - 14.7), synodic - abs(sig.moon_day - 14.7))
    new_distance = min(abs(sig.moon_day), abs(synodic - sig.moon_day))
    sig.is_full_moon_window = full_distance <= FULL_MOON_DAY_WINDOW
    sig.is_new_moon_window = new_distance <= NEW_MOON_DAY_WINDOW

    # Polar-night / midnight-sun (latitude-dependent).
    sig.is_kaamos = sig.lat >= 66.0 and sig.daylight_hours <= 2.0
    sig.is_midnight_sun = sig.lat >= 66.0 and sig.daylight_hours >= 22.0

    # Nested-scopes self-framing.
    sig.nested_scopes = _BASE_SCOPES + _location_scopes(sig.lat, sig.lon, sig.location_name)

    # Composite ecological_connectedness — Phase 1 used (daylight, moon) only;
    # Phase 4 layers astronomical-event boosts on top, with bounded contribution.
    daylight_norm = max(0.20, min(1.0, sig.daylight_hours / 18.0))
    full_moon_proximity = 1.0 - min(
        abs(sig.moon_day) / (synodic / 2),
        abs(sig.moon_day - synodic / 2) / (synodic / 2),
    )
    moon_norm = 0.5 + 0.3 * full_moon_proximity

    base = 0.5 * daylight_norm + 0.3 * moon_norm
    boost = 0.0
    sources: list[str] = ["daylight", "moon"]
    if sig.is_solstice_window:
        boost += 0.10
        sources.append("solstice")
    if sig.is_equinox_window:
        boost += 0.08
        sources.append("equinox")
    if sig.is_full_moon_window:
        boost += 0.05
        sources.append("full_moon")
    if sig.is_kaamos or sig.is_midnight_sun:
        boost += 0.05
        sources.append("polar")

    composite = max(0.0, min(1.0, base + min(0.20, boost)))
    sig.composite_score = round(composite, 4)
    sig.composite_source = " + ".join(sources)
    return sig


# Convenience helper for viability.py (kept here so the formula stays in
# one place rather than scattered across modules).
def ecological_connectedness_signal() -> tuple[float, str]:
    """Return (score in [0,1], source) for the viability variable."""
    sig = compute_ecological_signal()
    return sig.composite_score, sig.composite_source
