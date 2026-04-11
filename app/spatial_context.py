from __future__ import annotations

"""
spatial_context.py — Dynamic location awareness for agents.

3-layer resolution (best accuracy wins):
  1. CoreLocation (macOS GPS/WiFi) via host file /tmp/botarmy-location.json
  2. IP geolocation (ip-api.com, free, no key)
  3. Static config (DEFAULT_LATITUDE/DEFAULT_LONGITUDE)

Cached 30 minutes. No new pip dependencies.
"""

import json
import logging
import os
import time as _time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_cache: dict = {}
_cache_ts: float = 0.0
_CACHE_TTL = 1800  # 30 minutes

# CoreLocation result file — written by forwarder on host, readable by gateway
_CORELOCATION_FILE = os.environ.get(
    "CORELOCATION_FILE", "/tmp/botarmy-location.json"
)

# ── Finnish place context database ──────────────────────────────────────────

_FINNISH_PLACES: list[tuple[float, float, float, str]] = [
    # (lat, lon, radius_km, description)
    (60.17, 24.94, 15, "Helsinki — capital, southern coast, Baltic Sea, Suomenlinna fortress nearby"),
    (60.19, 24.76, 10, "Espoo — tech hub (Aalto, Nokia), Nuuksio National Park nearby"),
    (60.45, 22.27, 12, "Turku — oldest city, archipelago coast, Aura river, medieval castle"),
    (61.50, 23.79, 12, "Tampere — lake city between Näsijärvi and Pyhäjärvi, industrial heritage"),
    (61.69, 27.27, 10, "Mikkeli — eastern lakeland, gateway to Saimaa"),
    (61.88, 28.88, 10, "Savonlinna — Saimaa lakeland, Olavinlinna castle, opera festival"),
    (62.60, 29.76, 10, "Joensuu — eastern border, Karelian culture, university city"),
    (62.24, 25.75, 10, "Jyväskylä — central Finland, Alvar Aalto architecture, lake district"),
    (63.10, 21.62, 10, "Vaasa — west coast, bilingual (Finnish/Swedish), Kvarken Archipelago"),
    (63.84, 20.26, 15, "Umeå — Swedish side of Bothnian coast"),  # Cross-border awareness
    (65.01, 25.47, 12, "Oulu — northern tech city, Bothnian Bay, tar history"),
    (66.50, 25.98, 10, "Rovaniemi — Arctic Circle, Santa Claus Village, gateway to Lapland"),
    (68.42, 23.39, 15, "Enontekiö — Fell Lapland, reindeer herding, midnight sun/polar night"),
    (69.07, 27.02, 10, "Utsjoki — northernmost Finland, Sámi culture, Teno river salmon"),
    (67.37, 23.78, 10, "Kittilä/Levi — ski resort, aurora viewing, fell landscape"),
    (64.23, 27.73, 10, "Kajaani — Kainuu wilderness, tar canal history"),
    (61.06, 28.19, 15, "Lappeenranta — south Saimaa, Russian border, lake cruises"),
    (60.98, 25.66, 10, "Lahti — sports city, ski jumping, Vesijärvi lake"),
    (59.44, 24.75, 12, "Tallinn — Estonian capital across the Gulf of Finland"),
]

# Saimaa lake district bounding box
_SAIMAA_BOUNDS = (61.0, 62.5, 27.0, 30.0)  # lat_min, lat_max, lon_min, lon_max


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in km."""
    import math
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _place_context(lat: float, lon: float, city: str = "", country: str = "") -> str:
    """Generate location-aware place description."""
    # Check known Finnish/Nordic places
    best_place = None
    best_dist = 999.0
    for plat, plon, radius, desc in _FINNISH_PLACES:
        dist = _haversine_km(lat, lon, plat, plon)
        if dist < radius and dist < best_dist:
            best_place = desc
            best_dist = dist

    if best_place:
        context = best_place
    elif city and country:
        context = f"{city}, {country}"
    elif country:
        context = country
    else:
        context = f"{lat:.1f}°N, {lon:.1f}°E"

    # Saimaa lake district detection
    if (_SAIMAA_BOUNDS[0] <= lat <= _SAIMAA_BOUNDS[1]
            and _SAIMAA_BOUNDS[2] <= lon <= _SAIMAA_BOUNDS[3]):
        context += ". Saimaa lake district — home of the endangered Saimaa ringed seal"

    # Lapland detection
    if lat >= 66.5:
        context += ". Arctic Lapland — reindeer herding, Sámi culture"
    elif lat >= 64.0:
        context += ". Northern Finland — boreal forest, sub-Arctic conditions"

    return context


# ── Layer 1: CoreLocation (host GPS/WiFi) ──────────────────────────────────

def _try_corelocation() -> dict | None:
    """Read CoreLocation result from host file. Returns None if unavailable."""
    try:
        if not os.path.exists(_CORELOCATION_FILE):
            return None
        stat = os.stat(_CORELOCATION_FILE)
        age = _time.time() - stat.st_mtime
        if age > 3600:  # Stale (>1h old)
            return None
        with open(_CORELOCATION_FILE, "r") as f:
            data = json.load(f)
        lat = data.get("lat")
        lon = data.get("lon")
        if lat is None or lon is None:
            return None
        return {
            "lat": float(lat),
            "lon": float(lon),
            "accuracy_m": data.get("accuracy", 100),
            "city": data.get("city", ""),
            "country": data.get("country", ""),
            "region": data.get("region", ""),
            "source": "corelocation",
            "accuracy_km": round(data.get("accuracy", 100) / 1000, 2),
        }
    except Exception:
        return None


# ── Layer 2: IP Geolocation ─────────────────────────────────────────────────

def _try_ip_geolocation() -> dict | None:
    """Query ip-api.com for location from public IP. Free, no API key."""
    try:
        import requests
        resp = requests.get(
            "http://ip-api.com/json/?fields=status,lat,lon,city,regionName,country,timezone",
            timeout=3,
        )
        data = resp.json()
        if data.get("status") != "success":
            return None
        return {
            "lat": float(data["lat"]),
            "lon": float(data["lon"]),
            "city": data.get("city", ""),
            "country": data.get("country", ""),
            "region": data.get("regionName", ""),
            "timezone": data.get("timezone", ""),
            "source": "ip",
            "accuracy_km": 15,  # Typical city-level accuracy
        }
    except Exception as e:
        logger.debug(f"IP geolocation failed: {e}")
        return None


# ── Layer 3: Static Config ──────────────────────────────────────────────────

def _config_fallback() -> dict:
    """Fall back to config defaults."""
    try:
        from app.config import get_settings
        s = get_settings()
        lat = getattr(s, "default_latitude", 60.17)
        lon = getattr(s, "default_longitude", 24.94)
    except Exception:
        lat, lon = 60.17, 24.94
    return {
        "lat": lat,
        "lon": lon,
        "city": "Helsinki",
        "country": "Finland",
        "region": "Uusimaa",
        "source": "config",
        "accuracy_km": 0,  # Fixed point, no "accuracy"
    }


# ── Public API ──────────────────────────────────────────────────────────────

def get_location() -> dict:
    """Resolve current location using 3-layer fallback. Cached 30 min.

    Returns dict with: lat, lon, city, country, region, source, accuracy_km, place_context.
    """
    global _cache, _cache_ts
    now = _time.monotonic()
    if _cache and (now - _cache_ts) < _CACHE_TTL:
        return _cache

    # Try layers in order
    loc = _try_corelocation()
    if loc:
        logger.info(f"spatial: CoreLocation → {loc['lat']:.4f}, {loc['lon']:.4f} (±{loc['accuracy_km']}km)")
    else:
        loc = _try_ip_geolocation()
        if loc:
            logger.info(f"spatial: IP geolocation → {loc['city']}, {loc['country']}")
        else:
            loc = _config_fallback()
            logger.info("spatial: using config defaults (Helsinki)")

    # Enrich with place context
    loc["place_context"] = _place_context(
        loc["lat"], loc["lon"], loc.get("city", ""), loc.get("country", "")
    )

    _cache = loc
    _cache_ts = now
    return loc


def format_spatial_block() -> str:
    """Format spatial context for prompt injection (~30-40 tokens)."""
    loc = get_location()
    lat_dir = "N" if loc["lat"] >= 0 else "S"
    lon_dir = "E" if loc["lon"] >= 0 else "W"
    source_desc = {
        "corelocation": f"GPS (±{loc.get('accuracy_km', 0)}km)",
        "ip": "IP geolocation (~15km)",
        "config": "configured default",
    }.get(loc["source"], loc["source"])

    lines = [
        "<spatial_context>",
        f"Location: {loc.get('city', '?')}, {loc.get('region', '')}, {loc.get('country', '?')} "
        f"({abs(loc['lat']):.2f}°{lat_dir}, {abs(loc['lon']):.2f}°{lon_dir})",
        f"Source: {source_desc}",
    ]
    if loc.get("place_context"):
        lines.append(f"Local context: {loc['place_context']}")
    lines.append("</spatial_context>")
    return "\n".join(lines)


def clear_cache() -> None:
    """Force re-resolution on next call."""
    global _cache, _cache_ts
    _cache = {}
    _cache_ts = 0.0
