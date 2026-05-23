"""weather — Helsinki today + tomorrow via Open-Meteo (no API key).

The operator is in Helsinki (60.17 N, 24.94 E). Open-Meteo is free,
no auth, ~50ms latency. Soft fail on network errors."""
from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ID = "weather"
DISPLAY_NAME = "🌤  Weather (Helsinki)"
DESCRIPTION = (
    "Today + tomorrow conditions for Helsinki via free Open-Meteo API. "
    "Min/max temp + precipitation + dominant condition."
)

_LAT, _LON = 60.1699, 24.9384
_URL = (
    "https://api.open-meteo.com/v1/forecast?"
    f"latitude={_LAT}&longitude={_LON}"
    "&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,"
    "weather_code,sunrise,sunset"
    "&forecast_days=2&timezone=Europe%2FHelsinki"
)

# Open-Meteo's WMO weather codes → emoji + label (compact). Source:
# https://open-meteo.com/en/docs (WMO 4677). Trimmed to the buckets
# that matter for the briefing line.
_WMO: dict[int, tuple[str, str]] = {
    0: ("☀️", "clear"),
    1: ("🌤", "mainly clear"),
    2: ("⛅️", "partly cloudy"),
    3: ("☁️", "overcast"),
    45: ("🌫", "fog"),
    48: ("🌫", "rime fog"),
    51: ("🌦", "light drizzle"),
    53: ("🌦", "drizzle"),
    55: ("🌦", "dense drizzle"),
    61: ("🌧", "light rain"),
    63: ("🌧", "rain"),
    65: ("🌧", "heavy rain"),
    71: ("🌨", "light snow"),
    73: ("🌨", "snow"),
    75: ("🌨", "heavy snow"),
    77: ("❄️", "snow grains"),
    80: ("🌦", "rain showers"),
    81: ("🌧", "heavy showers"),
    82: ("⛈", "violent showers"),
    85: ("🌨", "snow showers"),
    86: ("🌨", "heavy snow showers"),
    95: ("⛈", "thunderstorm"),
    96: ("⛈", "thunder + hail"),
    99: ("⛈", "heavy thunder + hail"),
}


def _code_label(code: int) -> str:
    icon, label = _WMO.get(int(code), ("🌡", f"code {code}"))
    return f"{icon} {label}"


def gather() -> list[str]:
    try:
        req = urllib.request.Request(_URL, headers={"User-Agent": "AndrusAI/1.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        logger.debug("weather.gather: fetch failed", exc_info=True)
        return []
    daily = data.get("daily") or {}
    days = daily.get("time") or []
    if not days:
        return []
    out: list[str] = []
    labels = ("Today", "Tomorrow")
    for i in range(min(2, len(days))):
        try:
            t_lo = float(daily["temperature_2m_min"][i])
            t_hi = float(daily["temperature_2m_max"][i])
            precip = float(daily["precipitation_sum"][i])
            code = int(daily["weather_code"][i])
        except Exception:
            continue
        label = labels[i] if i < len(labels) else days[i]
        cond = _code_label(code)
        precip_part = f", {precip:.1f}mm" if precip > 0 else ""
        out.append(f"  • {label}: {cond}, {t_lo:.0f}–{t_hi:.0f}°C{precip_part}")
    return out
