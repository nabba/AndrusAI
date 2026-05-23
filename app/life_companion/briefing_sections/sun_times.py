"""sun_times — sunrise / sunset / golden-hour for Helsinki.

Finland sits at 60°N so day length varies from ~6h in December to
~19h in June. This section gives the operator a one-line "light
budget" for the day. Uses the same Open-Meteo response weather.py
pulled — Open-Meteo includes sunrise/sunset in the daily forecast,
no extra API call.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ID = "sun-times"
DISPLAY_NAME = "🌅 Sunlight today"
DESCRIPTION = (
    "Sunrise, sunset, and day length for Helsinki. Useful at 60°N where "
    "day length swings from 6h winter to 19h summer."
)

_LAT, _LON = 60.1699, 24.9384
_URL = (
    "https://api.open-meteo.com/v1/forecast?"
    f"latitude={_LAT}&longitude={_LON}"
    "&daily=sunrise,sunset"
    "&forecast_days=1&timezone=Europe%2FHelsinki"
)
_TZ = ZoneInfo("Europe/Helsinki")


def _fmt_hm(iso: str) -> str:
    # Open-Meteo returns local-naive ISO ("2026-05-23T05:14") in the
    # timezone we asked for; strip the date part.
    try:
        return iso.split("T", 1)[1][:5]
    except Exception:
        return iso


def gather() -> list[str]:
    try:
        req = urllib.request.Request(_URL, headers={"User-Agent": "AndrusAI/1.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        logger.debug("sun_times.gather: fetch failed", exc_info=True)
        return []
    daily = data.get("daily") or {}
    sunrises = daily.get("sunrise") or []
    sunsets = daily.get("sunset") or []
    if not sunrises or not sunsets:
        return []
    sr_iso, ss_iso = sunrises[0], sunsets[0]
    try:
        sr = datetime.fromisoformat(sr_iso).replace(tzinfo=_TZ)
        ss = datetime.fromisoformat(ss_iso).replace(tzinfo=_TZ)
    except Exception:
        return []
    day_h = (ss - sr).total_seconds() / 3600.0
    return [
        f"  • Sunrise {_fmt_hm(sr_iso)} · sunset {_fmt_hm(ss_iso)} · {day_h:.1f}h of daylight",
    ]
