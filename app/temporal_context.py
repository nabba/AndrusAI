from __future__ import annotations

"""
temporal_context.py — System-wide temporal awareness.

Computes current date, time, season, moon phase, sunrise/sunset, and
seasonal narrative for injection into agent prompts. Pure stdlib — no
external dependencies.

Cached per minute (temporal data changes slowly).
"""

import calendar
import math
import time as _time
from datetime import datetime, timedelta, timezone

_cache: dict = {}
_cache_ts: float = 0.0
_CACHE_TTL = 60  # seconds


def _helsinki_tz() -> timezone:
    """Return current Helsinki offset (EET UTC+2 or EEST UTC+3).

    DST: last Sunday of March 03:00 → last Sunday of October 04:00.
    """
    now_utc = datetime.now(timezone.utc)
    year = now_utc.year

    # Last Sunday of March
    mar31 = datetime(year, 3, 31, 3, 0, tzinfo=timezone.utc)
    dst_start = mar31 - timedelta(days=(mar31.weekday() + 1) % 7)

    # Last Sunday of October
    oct31 = datetime(year, 10, 31, 4, 0, tzinfo=timezone.utc)
    dst_end = oct31 - timedelta(days=(oct31.weekday() + 1) % 7)

    if dst_start <= now_utc < dst_end:
        return timezone(timedelta(hours=3))  # EEST
    return timezone(timedelta(hours=2))  # EET


def _moon_phase(dt: datetime) -> tuple[str, int]:
    """Moon phase from synodic cycle. Returns (phase_name, day_of_cycle)."""
    # Known new moon: 2024-01-11 11:57 UTC
    ref = datetime(2024, 1, 11, 11, 57, tzinfo=timezone.utc)
    diff = (dt.astimezone(timezone.utc) - ref).total_seconds()
    synodic = 29.53058867
    day = (diff / 86400) % synodic
    idx = int(day / synodic * 8) % 8
    names = [
        "New moon", "Waxing crescent", "First quarter", "Waxing gibbous",
        "Full moon", "Waning gibbous", "Last quarter", "Waning crescent",
    ]
    return names[idx], int(day)


def _sunrise_sunset(lat: float, lon: float, dt: datetime) -> tuple[str, str, float]:
    """Approximate sunrise/sunset using solar declination + hour angle.

    Accuracy: ~10 minutes. Returns (sunrise_str, sunset_str, daylight_hours).
    """
    day_of_year = dt.timetuple().tm_yday
    lat_rad = math.radians(lat)

    # Solar declination (Spencer, 1971 — simplified)
    gamma = 2 * math.pi / 365 * (day_of_year - 1)
    decl = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
            - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma))

    # Hour angle at sunrise/sunset (geometric, no refraction correction)
    cos_ha = -math.tan(lat_rad) * math.tan(decl)

    if cos_ha < -1:
        # Midnight sun
        return "no sunset", "no sunrise", 24.0
    if cos_ha > 1:
        # Polar night
        return "no sunrise", "no sunset", 0.0

    ha = math.acos(cos_ha)
    daylight_hours = 2 * ha * 12 / math.pi

    # Solar noon in UTC hours (approximate via longitude)
    solar_noon_utc = 12.0 - lon / 15.0

    # Convert to local timezone
    tz_offset = dt.utcoffset().total_seconds() / 3600 if dt.utcoffset() else 0
    solar_noon_local = solar_noon_utc + tz_offset

    rise_h = solar_noon_local - daylight_hours / 2
    set_h = solar_noon_local + daylight_hours / 2

    def _fmt(h: float) -> str:
        h = h % 24
        hh = int(h)
        mm = int((h - hh) * 60)
        return f"{hh:02d}:{mm:02d}"

    return _fmt(rise_h), _fmt(set_h), daylight_hours


def _season(month: int, lat: float) -> str:
    """Meteorological season for given hemisphere."""
    northern = lat >= 0
    if northern:
        seasons = {12: "winter", 1: "winter", 2: "winter",
                   3: "spring", 4: "spring", 5: "spring",
                   6: "summer", 7: "summer", 8: "summer",
                   9: "autumn", 10: "autumn", 11: "autumn"}
    else:
        seasons = {12: "summer", 1: "summer", 2: "summer",
                   3: "autumn", 4: "autumn", 5: "autumn",
                   6: "winter", 7: "winter", 8: "winter",
                   9: "spring", 10: "spring", 11: "spring"}
    return seasons[month]


def _month_part(day: int) -> str:
    if day <= 10:
        return "Early"
    elif day <= 20:
        return "Mid"
    return "Late"


def _daylight_trend(month: int, day: int, lat: float) -> str:
    """Whether daylight is increasing or decreasing."""
    northern = lat >= 0
    # Summer solstice ~Jun 21, Winter solstice ~Dec 21
    day_of_year = datetime(2024, month, min(day, 28)).timetuple().tm_yday
    if northern:
        if 165 <= day_of_year <= 175:
            return "near summer solstice (longest days)"
        elif 350 <= day_of_year or day_of_year <= 5:
            return "near winter solstice (shortest days)"
        elif day_of_year < 172:
            return "lengthening"
        else:
            return "shortening"
    else:
        if 350 <= day_of_year or day_of_year <= 5:
            return "near summer solstice (longest days)"
        elif 165 <= day_of_year <= 175:
            return "near winter solstice (shortest days)"
        elif day_of_year < 172:
            return "shortening"
        else:
            return "lengthening"


# Latitude-banded narratives: southern (55-63°N), northern (63-66°N), arctic (66°N+)
_NORDIC_NARRATIVES_SOUTH: dict[int, str] = {
    1: "Deep winter. Lakes and sea frozen. Short days (~6h). Aurora borealis visible. Snow cover thick.",
    2: "Late winter. Days lengthening noticeably. Still frozen lakes. Cross-country skiing season.",
    3: "Late winter transitioning to spring. Snow melting in southern regions. Daylight increasing rapidly.",
    4: "Early spring. Snow mostly melted, lakes beginning to thaw. Migratory birds returning. Daylight increasing ~5 min/day.",
    5: "Spring. Trees leafing out, lakes thawed. Bird migration peak. White nights beginning.",
    6: "Early summer. Midsummer (Juhannus) celebrations. Near-continuous daylight. Nature in full bloom.",
    7: "Midsummer. Warmest month. Lakes at swimming temperature. Saimaa ringed seals visible. Berries ripening.",
    8: "Late summer. Berry and mushroom season (blueberries, lingonberries, chanterelles). Nights returning.",
    9: "Early autumn (ruska). Fall colors starting. Mushroom foraging. First frosts possible.",
    10: "Autumn. Fall colors in full. First snow possible in north. Migrating birds departing. Dark evenings.",
    11: "Late autumn. First snow in many areas. Lakes beginning to freeze. Very short days.",
    12: "Early winter. Snow cover establishing. Shortest days (~6h). Christmas markets. Aurora season.",
}
_NORDIC_NARRATIVES_NORTH: dict[int, str] = {
    1: "Deep winter. Polar twilight, ~2-3h of dim light. Thick snow. Aurora season at peak.",
    2: "Late winter. Sun returning but still very short days. Snow excellent for skiing.",
    3: "Winter still firm. Increasing daylight. Snowmobile and reindeer safari season.",
    4: "Late winter/early spring. Snow still deep. Spring migration of reindeer beginning.",
    5: "Spring thaw beginning. Rivers breaking up. Snow retreating. Midnight sun approaching.",
    6: "Midnight sun period beginning. Snow gone in valleys. Summer reindeer herding. Mosquito season starts.",
    7: "Full midnight sun. Warmest period. Hiking season peak. Berries beginning to ripen.",
    8: "Late summer. Ruska (autumn colors) starting early. Berry picking peak. Nights returning.",
    9: "Ruska in full glory. Spectacular fall colors on fells. First snow possible on peaks.",
    10: "Autumn. First lasting snow. Kaamos (polar night) approaching. Aurora season returning.",
    11: "Early polar night (kaamos). Very little daylight. Snow settling. Northern lights active.",
    12: "Polar night. No direct sunlight. Aurora borealis best viewing. Deep snow. Christmas traditions.",
}
_NORDIC_NARRATIVES_ARCTIC = _NORDIC_NARRATIVES_NORTH  # Same for >66°N, could differentiate further


def _get_narrative(month: int, lat: float) -> str:
    """Get latitude-appropriate seasonal narrative."""
    if lat >= 63:
        return _NORDIC_NARRATIVES_NORTH.get(month, "")
    return _NORDIC_NARRATIVES_SOUTH.get(month, "")


def get_temporal_context(lat: float | None = None, lon: float | None = None) -> dict:
    """Compute current temporal context. Pure stdlib, no external deps.

    Returns dict with all temporal fields + pre-formatted block.
    Cached per minute.
    """
    global _cache, _cache_ts
    now_mono = _time.monotonic()
    if _cache and (now_mono - _cache_ts) < _CACHE_TTL:
        return _cache

    # Resolve location dynamically (GPS → IP → config)
    location_name = ""
    location_tz = None
    if lat is None or lon is None:
        try:
            from app.spatial_context import get_location
            loc = get_location()
            lat = loc["lat"]
            lon = loc["lon"]
            location_name = f"{loc.get('city', '')}, {loc.get('country', '')}"
            # Use timezone from IP geolocation if available (handles travel)
            if loc.get("timezone"):
                location_tz = loc["timezone"]
        except Exception:
            try:
                from app.config import get_settings
                s = get_settings()
                lat = getattr(s, "default_latitude", 60.17)
                lon = getattr(s, "default_longitude", 24.94)
            except Exception:
                lat, lon = 60.17, 24.94

    tz = _helsinki_tz()
    now = datetime.now(tz)

    season = _season(now.month, lat)
    month_part = _month_part(now.day)
    moon_name, moon_day = _moon_phase(now)
    sunrise, sunset, daylight_h = _sunrise_sunset(lat, lon, now)
    trend = _daylight_trend(now.month, now.day, lat)

    # Timezone name
    offset_h = tz.utcoffset(None).total_seconds() / 3600
    tz_name = "EEST" if offset_h == 3 else "EET"

    # Daylight description
    if daylight_h >= 24:
        daylight_desc = "Midnight sun (continuous daylight)"
    elif daylight_h <= 0:
        daylight_desc = "Polar night (no sunrise)"
    else:
        dh = int(daylight_h)
        dm = int((daylight_h - dh) * 60)
        daylight_desc = f"{dh}h {dm}min daylight, {trend}"

    # Seasonal narrative (latitude-aware)
    narrative = _get_narrative(now.month, lat)

    date_str = now.strftime("%A, %B %d, %Y")
    time_str = now.strftime("%H:%M")

    result = {
        "date_str": date_str,
        "time_str": time_str,
        "weekday": now.strftime("%A"),
        "month_name": now.strftime("%B"),
        "month_part": month_part,
        "day_of_year": now.timetuple().tm_yday,
        "week_number": now.isocalendar()[1],
        "season": season,
        "moon_phase": moon_name,
        "moon_day": moon_day,
        "sunrise": sunrise,
        "sunset": sunset,
        "daylight_hours": round(daylight_h, 1),
        "daylight_desc": daylight_desc,
        "daylight_trend": trend,
        "narrative": narrative,
        "tz_name": tz_name,
        "tz_offset": f"UTC+{int(offset_h)}",
        "lat": lat,
        "lon": lon,
        "location_name": location_name,
    }

    _cache = result
    _cache_ts = now_mono
    return result


def format_temporal_block(lat: float | None = None, lon: float | None = None) -> str:
    """Format temporal context as a compact block for prompt injection (~50-60 tokens)."""
    tc = get_temporal_context(lat, lon)
    lines = [
        "<temporal_context>",
        f"Current: {tc['date_str']}, {tc['time_str']} {tc['tz_name']} ({tc['tz_offset']})",
        f"Season: {tc['month_part']} {tc['season']}. Day {tc['day_of_year']}/365.",
    ]
    if tc["sunrise"] != "no sunrise" and tc["sunset"] != "no sunset":
        lines.append(f"Sun: rises {tc['sunrise']}, sets {tc['sunset']} ({tc['daylight_desc']})")
    else:
        lines.append(f"Sun: {tc['daylight_desc']}")
    lines.append(f"Moon: {tc['moon_phase']} (day {tc['moon_day']} of cycle)")
    if tc["narrative"]:
        loc_label = tc.get("location_name", "").strip(", ") or f"{tc['lat']:.0f}°N"
        lines.append(f"Nature near {loc_label} ({tc['lat']:.0f}°N): {tc['narrative']}")
    lines.append("</temporal_context>")
    return "\n".join(lines)
