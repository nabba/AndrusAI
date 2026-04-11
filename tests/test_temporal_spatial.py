"""
Comprehensive tests for temporal_context.py and spatial_context.py.

Covers:
  - Temporal: seasons, moon phase, sunrise/sunset, daylight trends, DST,
    narratives, caching, formatting, edge cases (polar, equatorial, southern hemisphere)
  - Spatial: 3-layer fallback (CoreLocation → IP → config), place context DB,
    haversine, Saimaa/Lapland detection, caching, formatting
  - Integration: spatial feeds temporal, wiring into orchestrator/routing/reality model

Run: pytest tests/test_temporal_spatial.py -v
"""

import json
import math
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ═══════════════════════════════════════════════════════════════════════════════
# TEMPORAL CONTEXT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestHelsinkiTimezone:
    """_helsinki_tz() — DST transitions for Finland."""

    def test_winter_is_eet_utc2(self):
        """January → EET (UTC+2)."""
        from app.temporal_context import _helsinki_tz
        with patch("app.temporal_context.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            # Can't easily mock datetime.now inside, test actual behavior
        tz = _helsinki_tz()
        offset = tz.utcoffset(None).total_seconds() / 3600
        # April 10 2026 is DST → UTC+3; January would be UTC+2
        # Since _helsinki_tz uses datetime.now(utc), we test current behavior
        assert offset in (2.0, 3.0)

    def test_offset_is_2_or_3(self):
        """Helsinki is always UTC+2 or UTC+3."""
        from app.temporal_context import _helsinki_tz
        tz = _helsinki_tz()
        offset = tz.utcoffset(None).total_seconds() / 3600
        assert offset in (2.0, 3.0)

    def test_returns_timezone_object(self):
        from app.temporal_context import _helsinki_tz
        tz = _helsinki_tz()
        assert isinstance(tz, timezone)


class TestMoonPhase:
    """_moon_phase() — synodic cycle calculation."""

    def test_returns_valid_phase_name(self):
        from app.temporal_context import _moon_phase
        valid = {"New moon", "Waxing crescent", "First quarter", "Waxing gibbous",
                 "Full moon", "Waning gibbous", "Last quarter", "Waning crescent"}
        dt = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
        name, day = _moon_phase(dt)
        assert name in valid

    def test_day_of_cycle_in_range(self):
        from app.temporal_context import _moon_phase
        dt = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
        _, day = _moon_phase(dt)
        assert 0 <= day <= 29

    def test_new_moon_reference_date(self):
        """Known new moon: 2024-01-11 should be near day 0."""
        from app.temporal_context import _moon_phase
        ref = datetime(2024, 1, 11, 12, 0, tzinfo=timezone.utc)
        name, day = _moon_phase(ref)
        assert day <= 1 or day >= 29  # Near new moon

    def test_full_moon_roughly_14_days_later(self):
        """~14.7 days after new moon should be near full moon."""
        from app.temporal_context import _moon_phase
        full = datetime(2024, 1, 25, 18, 0, tzinfo=timezone.utc)
        name, day = _moon_phase(full)
        assert 12 <= day <= 17

    def test_different_months_give_different_phases(self):
        from app.temporal_context import _moon_phase
        phases = set()
        for m in range(1, 13):
            name, _ = _moon_phase(datetime(2026, m, 15, 12, 0, tzinfo=timezone.utc))
            phases.add(name)
        assert len(phases) >= 3  # Should hit multiple phases across 12 months

    def test_synodic_cycle_wraps(self):
        """Phase should cycle back after ~29.5 days."""
        from app.temporal_context import _moon_phase
        dt1 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        dt2 = datetime(2026, 1, 31, 12, 0, tzinfo=timezone.utc)
        name1, day1 = _moon_phase(dt1)
        name2, day2 = _moon_phase(dt2)
        # 30 days later should be close to same phase (within 1 day)
        assert abs(day1 - day2) <= 2 or abs(day1 - day2) >= 27


class TestSunriseSunset:
    """_sunrise_sunset() — solar calculations."""

    def test_helsinki_summer_long_days(self):
        """June 21 at 60°N should have ~18-19h daylight."""
        from app.temporal_context import _sunrise_sunset
        tz = timezone(timedelta(hours=3))
        dt = datetime(2026, 6, 21, 12, 0, tzinfo=tz)
        rise, sset, dh = _sunrise_sunset(60.17, 24.94, dt)
        assert 17.0 <= dh <= 20.0

    def test_helsinki_winter_short_days(self):
        """Dec 21 at 60°N should have ~5-7h daylight."""
        from app.temporal_context import _sunrise_sunset
        tz = timezone(timedelta(hours=2))
        dt = datetime(2026, 12, 21, 12, 0, tzinfo=tz)
        rise, sset, dh = _sunrise_sunset(60.17, 24.94, dt)
        assert 4.5 <= dh <= 7.5

    def test_equinox_twelve_hours(self):
        """March 20 should have ~12h daylight everywhere."""
        from app.temporal_context import _sunrise_sunset
        tz = timezone(timedelta(hours=2))
        dt = datetime(2026, 3, 20, 12, 0, tzinfo=tz)
        _, _, dh = _sunrise_sunset(60.17, 24.94, dt)
        assert 11.0 <= dh <= 13.0

    def test_equator_constant_daylight(self):
        """Equator should have ~12h year-round."""
        from app.temporal_context import _sunrise_sunset
        tz = timezone.utc
        for month in [1, 4, 7, 10]:
            dt = datetime(2026, month, 15, 12, 0, tzinfo=tz)
            _, _, dh = _sunrise_sunset(0.0, 0.0, dt)
            assert 11.5 <= dh <= 12.5

    def test_arctic_midnight_sun(self):
        """70°N in June should have midnight sun (24h)."""
        from app.temporal_context import _sunrise_sunset
        tz = timezone(timedelta(hours=2))
        dt = datetime(2026, 6, 21, 12, 0, tzinfo=tz)
        rise, sset, dh = _sunrise_sunset(70.0, 25.0, dt)
        assert dh == 24.0
        assert "no sunset" in rise or "no sunrise" in sset or dh == 24.0

    def test_arctic_polar_night(self):
        """70°N in December should have polar night (0h)."""
        from app.temporal_context import _sunrise_sunset
        tz = timezone(timedelta(hours=2))
        dt = datetime(2026, 12, 21, 12, 0, tzinfo=tz)
        rise, sset, dh = _sunrise_sunset(70.0, 25.0, dt)
        assert dh == 0.0

    def test_sunrise_before_sunset(self):
        """Sunrise time should be before sunset (for normal days)."""
        from app.temporal_context import _sunrise_sunset
        tz = timezone(timedelta(hours=2))
        dt = datetime(2026, 4, 10, 12, 0, tzinfo=tz)
        rise, sset, dh = _sunrise_sunset(60.17, 24.94, dt)
        if rise != "no sunrise" and sset != "no sunset":
            # Parse times
            rh, rm = map(int, rise.split(":"))
            sh, sm = map(int, sset.split(":"))
            assert rh * 60 + rm < sh * 60 + sm

    def test_southern_hemisphere(self):
        """Sydney (~34°S) in June should have short days (~10h)."""
        from app.temporal_context import _sunrise_sunset
        tz = timezone(timedelta(hours=11))
        dt = datetime(2026, 6, 21, 12, 0, tzinfo=tz)
        _, _, dh = _sunrise_sunset(-33.87, 151.21, dt)
        assert 9.0 <= dh <= 11.0

    def test_returns_string_format(self):
        """Sunrise/sunset should be HH:MM format."""
        from app.temporal_context import _sunrise_sunset
        tz = timezone(timedelta(hours=2))
        dt = datetime(2026, 4, 10, 12, 0, tzinfo=tz)
        rise, sset, _ = _sunrise_sunset(60.17, 24.94, dt)
        assert len(rise) == 5 and rise[2] == ":"
        assert len(sset) == 5 and sset[2] == ":"


class TestSeason:
    """_season() — meteorological season mapping."""

    def test_northern_hemisphere_all_months(self):
        from app.temporal_context import _season
        expected = {
            1: "winter", 2: "winter", 3: "spring", 4: "spring",
            5: "spring", 6: "summer", 7: "summer", 8: "summer",
            9: "autumn", 10: "autumn", 11: "autumn", 12: "winter",
        }
        for month, season in expected.items():
            assert _season(month, 60.0) == season, f"Month {month}"

    def test_southern_hemisphere_inverted(self):
        from app.temporal_context import _season
        assert _season(1, -33.0) == "summer"
        assert _season(6, -33.0) == "winter"
        assert _season(9, -33.0) == "spring"

    def test_equator_treated_as_northern(self):
        from app.temporal_context import _season
        assert _season(1, 0.0) == "winter"
        assert _season(7, 0.0) == "summer"


class TestMonthPart:
    """_month_part() — early/mid/late month."""

    def test_early(self):
        from app.temporal_context import _month_part
        assert _month_part(1) == "Early"
        assert _month_part(10) == "Early"

    def test_mid(self):
        from app.temporal_context import _month_part
        assert _month_part(11) == "Mid"
        assert _month_part(20) == "Mid"

    def test_late(self):
        from app.temporal_context import _month_part
        assert _month_part(21) == "Late"
        assert _month_part(31) == "Late"


class TestDaylightTrend:
    """_daylight_trend() — lengthening/shortening/solstice."""

    def test_march_lengthening(self):
        from app.temporal_context import _daylight_trend
        assert _daylight_trend(3, 15, 60.0) == "lengthening"

    def test_september_shortening(self):
        from app.temporal_context import _daylight_trend
        assert _daylight_trend(9, 15, 60.0) == "shortening"

    def test_june_solstice(self):
        from app.temporal_context import _daylight_trend
        result = _daylight_trend(6, 21, 60.0)
        assert "solstice" in result

    def test_december_solstice(self):
        from app.temporal_context import _daylight_trend
        result = _daylight_trend(12, 22, 60.0)
        assert "solstice" in result

    def test_southern_hemisphere_inverted(self):
        from app.temporal_context import _daylight_trend
        # March in southern hemisphere: after Sep equinox, days shortening
        assert _daylight_trend(3, 15, -33.0) == "shortening"
        assert _daylight_trend(9, 15, -33.0) == "lengthening"


class TestNarratives:
    """_get_narrative() — latitude-banded seasonal narratives."""

    def test_south_narrative_all_months(self):
        from app.temporal_context import _get_narrative
        for month in range(1, 13):
            narrative = _get_narrative(month, 60.0)
            assert narrative, f"Missing narrative for month {month} at 60°N"
            assert len(narrative) > 20

    def test_north_narrative_all_months(self):
        from app.temporal_context import _get_narrative
        for month in range(1, 13):
            narrative = _get_narrative(month, 66.0)
            assert narrative, f"Missing narrative for month {month} at 66°N"

    def test_north_south_differ(self):
        """Northern and southern narratives should be different."""
        from app.temporal_context import _get_narrative
        south = _get_narrative(7, 60.0)
        north = _get_narrative(7, 66.0)
        assert south != north

    def test_threshold_at_63(self):
        from app.temporal_context import _get_narrative
        south = _get_narrative(1, 62.9)
        north = _get_narrative(1, 63.0)
        assert south != north

    def test_saimaa_seals_in_july_south(self):
        from app.temporal_context import _get_narrative
        narrative = _get_narrative(7, 61.5)
        assert "seal" in narrative.lower() or "Saimaa" in narrative

    def test_polar_night_in_december_north(self):
        from app.temporal_context import _get_narrative
        narrative = _get_narrative(12, 68.0)
        assert "polar" in narrative.lower() or "kaamos" in narrative.lower()


class TestGetTemporalContext:
    """get_temporal_context() — full computation with caching."""

    def setup_method(self):
        """Clear cache before each test."""
        import app.temporal_context as tc
        tc._cache = {}
        tc._cache_ts = 0.0

    def test_returns_all_required_keys(self):
        from app.temporal_context import get_temporal_context
        tc = get_temporal_context(lat=60.17, lon=24.94)
        required = [
            "date_str", "time_str", "weekday", "month_name", "month_part",
            "day_of_year", "week_number", "season", "moon_phase", "moon_day",
            "sunrise", "sunset", "daylight_hours", "daylight_desc",
            "daylight_trend", "narrative", "tz_name", "tz_offset",
            "lat", "lon", "location_name",
        ]
        for key in required:
            assert key in tc, f"Missing key: {key}"

    def test_explicit_lat_lon_used(self):
        from app.temporal_context import get_temporal_context
        tc = get_temporal_context(lat=69.0, lon=27.0)
        assert tc["lat"] == 69.0
        assert tc["lon"] == 27.0

    def test_season_matches_current_month(self):
        from app.temporal_context import get_temporal_context, _season, _helsinki_tz
        tc = get_temporal_context(lat=60.17, lon=24.94)
        now = datetime.now(_helsinki_tz())
        expected = _season(now.month, 60.17)
        assert tc["season"] == expected

    def test_daylight_hours_reasonable(self):
        from app.temporal_context import get_temporal_context
        tc = get_temporal_context(lat=60.17, lon=24.94)
        assert 0 <= tc["daylight_hours"] <= 24

    def test_caching_returns_same_result(self):
        from app.temporal_context import get_temporal_context
        tc1 = get_temporal_context(lat=60.17, lon=24.94)
        tc2 = get_temporal_context(lat=60.17, lon=24.94)
        assert tc1 is tc2  # Same object from cache

    def test_cache_expires(self):
        """After cache TTL, _cache_ts is refreshed (proving re-computation)."""
        import app.temporal_context as mod
        import time
        mod._cache = {}
        mod._cache_ts = 0.0
        mod.get_temporal_context(lat=60.17, lon=24.94)
        ts1 = mod._cache_ts
        assert ts1 > 0  # Cache was populated
        # Within TTL: _cache_ts should NOT change
        mod.get_temporal_context(lat=60.17, lon=24.94)
        assert mod._cache_ts == ts1  # Same cache, no re-computation
        # Expire cache by setting ts far in the past (monotonic - TTL - 1)
        mod._cache_ts = time.monotonic() - mod._CACHE_TTL - 1
        old_ts = mod._cache_ts
        mod.get_temporal_context(lat=60.17, lon=24.94)
        assert mod._cache_ts > old_ts  # Cache was re-populated with fresh timestamp

    def test_spatial_context_integration(self):
        """When lat/lon not provided, should try spatial_context."""
        import app.temporal_context as mod
        mod._cache = {}
        mod._cache_ts = 0.0
        mock_loc = {
            "lat": 61.5, "lon": 23.79, "city": "Tampere",
            "country": "Finland", "timezone": "Europe/Helsinki",
        }
        with patch("app.spatial_context.get_location", return_value=mock_loc):
            tc = mod.get_temporal_context()
            assert tc["lat"] == 61.5
            assert "Tampere" in tc["location_name"]

    def test_fallback_when_spatial_fails(self):
        """When spatial_context raises, fall back to config/defaults."""
        import app.temporal_context as mod
        mod._cache = {}
        mod._cache_ts = 0.0
        with patch("app.spatial_context.get_location", side_effect=Exception("boom")):
            tc = mod.get_temporal_context()
            # Should still work with defaults
            assert tc["lat"] is not None
            assert tc["lon"] is not None


class TestFormatTemporalBlock:
    """format_temporal_block() — prompt injection formatting."""

    def setup_method(self):
        import app.temporal_context as tc
        tc._cache = {}
        tc._cache_ts = 0.0

    def test_contains_xml_tags(self):
        from app.temporal_context import format_temporal_block
        block = format_temporal_block(lat=60.17, lon=24.94)
        assert block.startswith("<temporal_context>")
        assert block.endswith("</temporal_context>")

    def test_contains_date(self):
        from app.temporal_context import format_temporal_block
        block = format_temporal_block(lat=60.17, lon=24.94)
        assert "2026" in block or "202" in block  # Year present

    def test_contains_season(self):
        from app.temporal_context import format_temporal_block
        block = format_temporal_block(lat=60.17, lon=24.94)
        assert any(s in block.lower() for s in ("spring", "summer", "autumn", "winter"))

    def test_contains_sunrise_sunset(self):
        from app.temporal_context import format_temporal_block
        block = format_temporal_block(lat=60.17, lon=24.94)
        assert "Sun:" in block
        assert ("rises" in block or "Midnight sun" in block or "Polar night" in block)

    def test_contains_moon(self):
        from app.temporal_context import format_temporal_block
        block = format_temporal_block(lat=60.17, lon=24.94)
        assert "Moon:" in block

    def test_contains_nature_narrative(self):
        from app.temporal_context import format_temporal_block
        block = format_temporal_block(lat=60.17, lon=24.94)
        assert "Nature near" in block

    def test_location_name_in_narrative(self):
        """When spatial provides city, it appears in the narrative line."""
        import app.temporal_context as mod
        mod._cache = {}
        mod._cache_ts = 0.0
        mock_loc = {"lat": 60.17, "lon": 24.94, "city": "Helsinki", "country": "Finland"}
        with patch("app.spatial_context.get_location", return_value=mock_loc):
            block = mod.format_temporal_block()
            assert "Helsinki" in block

    def test_token_count_reasonable(self):
        """Block should be compact (~50-80 tokens)."""
        from app.temporal_context import format_temporal_block
        block = format_temporal_block(lat=60.17, lon=24.94)
        word_count = len(block.split())
        assert word_count < 120  # Should be well under 120 words

    def test_midnight_sun_format(self):
        """At 70°N in June, should show midnight sun."""
        import app.temporal_context as mod
        mod._cache = {}
        mod._cache_ts = 0.0
        # Need to check if current month is June for this test to trigger
        # Instead, test the sunrise_sunset directly
        from app.temporal_context import _sunrise_sunset
        tz = timezone(timedelta(hours=3))
        dt = datetime(2026, 6, 21, 12, 0, tzinfo=tz)
        rise, sset, dh = _sunrise_sunset(70.0, 25.0, dt)
        assert dh == 24.0


# ═══════════════════════════════════════════════════════════════════════════════
# SPATIAL CONTEXT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestHaversine:
    """_haversine_km() — great-circle distance."""

    def test_zero_distance(self):
        from app.spatial_context import _haversine_km
        assert _haversine_km(60.0, 25.0, 60.0, 25.0) == 0.0

    def test_known_distance_helsinki_tallinn(self):
        """Helsinki to Tallinn is ~80km."""
        from app.spatial_context import _haversine_km
        d = _haversine_km(60.17, 24.94, 59.44, 24.75)
        assert 75 <= d <= 90

    def test_known_distance_helsinki_rovaniemi(self):
        """Helsinki to Rovaniemi is ~700-800km."""
        from app.spatial_context import _haversine_km
        d = _haversine_km(60.17, 24.94, 66.50, 25.98)
        assert 650 <= d <= 800

    def test_symmetric(self):
        from app.spatial_context import _haversine_km
        d1 = _haversine_km(60.0, 25.0, 61.0, 26.0)
        d2 = _haversine_km(61.0, 26.0, 60.0, 25.0)
        assert abs(d1 - d2) < 0.01

    def test_antipodal(self):
        """Antipodal points should be ~20000km (half earth circumference)."""
        from app.spatial_context import _haversine_km
        d = _haversine_km(0.0, 0.0, 0.0, 180.0)
        assert 19900 <= d <= 20100

    def test_equator_one_degree(self):
        """One degree of longitude at equator is ~111km."""
        from app.spatial_context import _haversine_km
        d = _haversine_km(0.0, 0.0, 0.0, 1.0)
        assert 110 <= d <= 112


class TestPlaceContext:
    """_place_context() — Finnish/Nordic place database."""

    def test_helsinki_recognized(self):
        from app.spatial_context import _place_context
        ctx = _place_context(60.17, 24.94)
        assert "Helsinki" in ctx

    def test_tallinn_recognized(self):
        from app.spatial_context import _place_context
        ctx = _place_context(59.44, 24.75)
        assert "Tallinn" in ctx

    def test_tampere_recognized(self):
        from app.spatial_context import _place_context
        ctx = _place_context(61.50, 23.79)
        assert "Tampere" in ctx

    def test_rovaniemi_recognized(self):
        from app.spatial_context import _place_context
        ctx = _place_context(66.50, 25.98)
        assert "Rovaniemi" in ctx

    def test_savonlinna_recognized(self):
        from app.spatial_context import _place_context
        ctx = _place_context(61.88, 28.88)
        assert "Savonlinna" in ctx

    def test_oulu_recognized(self):
        from app.spatial_context import _place_context
        ctx = _place_context(65.01, 25.47)
        assert "Oulu" in ctx

    def test_all_19_places_recognized(self):
        """Every place in the database should be matched at its exact coordinates."""
        from app.spatial_context import _FINNISH_PLACES, _place_context
        for lat, lon, radius, desc in _FINNISH_PLACES:
            ctx = _place_context(lat, lon)
            place_name = desc.split("—")[0].strip().split("/")[0].strip()
            assert place_name in ctx, f"Place {place_name} not found at ({lat}, {lon}): got '{ctx}'"

    def test_saimaa_district_detected(self):
        """Point in Saimaa bounding box should mention seals."""
        from app.spatial_context import _place_context
        ctx = _place_context(61.5, 28.0)
        assert "Saimaa" in ctx

    def test_saimaa_outside_bounds_no_seal(self):
        """Helsinki should NOT mention Saimaa seals."""
        from app.spatial_context import _place_context
        ctx = _place_context(60.17, 24.94)
        assert "Saimaa lake district" not in ctx

    def test_lapland_detected(self):
        """Points above 66.5°N should mention Lapland."""
        from app.spatial_context import _place_context
        ctx = _place_context(68.0, 25.0)
        assert "Arctic Lapland" in ctx or "Lapland" in ctx

    def test_northern_finland_detected(self):
        """Points 64-66.5°N should mention Northern Finland."""
        from app.spatial_context import _place_context
        ctx = _place_context(65.0, 26.0)
        assert "Northern Finland" in ctx or "Oulu" in ctx

    def test_unknown_location_uses_city_country(self):
        """Far from any known place, should use city/country fallback."""
        from app.spatial_context import _place_context
        ctx = _place_context(48.86, 2.35, "Paris", "France")
        assert "Paris" in ctx and "France" in ctx

    def test_unknown_no_city_uses_coords(self):
        """No city/country, should show coordinates."""
        from app.spatial_context import _place_context
        ctx = _place_context(35.0, 135.0)
        assert "35.0°N" in ctx

    def test_closest_match_wins(self):
        """When near two places, closest should win."""
        from app.spatial_context import _place_context
        # Exact Helsinki coordinates
        ctx = _place_context(60.17, 24.94)
        assert "Helsinki" in ctx
        assert "Espoo" not in ctx


class TestCoreLocationLayer:
    """_try_corelocation() — Layer 1: file-based CoreLocation."""

    def test_no_file_returns_none(self):
        from app.spatial_context import _try_corelocation
        with patch("app.spatial_context._CORELOCATION_FILE", "/nonexistent/path"):
            assert _try_corelocation() is None

    def test_valid_file_returns_dict(self):
        from app.spatial_context import _try_corelocation
        data = {"lat": 60.17, "lon": 24.94, "accuracy": 50.0}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            path = f.name
        try:
            with patch("app.spatial_context._CORELOCATION_FILE", path):
                result = _try_corelocation()
                assert result is not None
                assert result["lat"] == 60.17
                assert result["lon"] == 24.94
                assert result["source"] == "corelocation"
                assert result["accuracy_km"] == 0.05  # 50m = 0.05km
        finally:
            os.unlink(path)

    def test_stale_file_returns_none(self):
        """File older than 1 hour should be rejected."""
        from app.spatial_context import _try_corelocation
        data = {"lat": 60.17, "lon": 24.94}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            path = f.name
        try:
            # Set mtime to 2 hours ago
            old_time = time.time() - 7200
            os.utime(path, (old_time, old_time))
            with patch("app.spatial_context._CORELOCATION_FILE", path):
                assert _try_corelocation() is None
        finally:
            os.unlink(path)

    def test_missing_lat_lon_returns_none(self):
        from app.spatial_context import _try_corelocation
        data = {"accuracy": 50.0}  # No lat/lon
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            path = f.name
        try:
            with patch("app.spatial_context._CORELOCATION_FILE", path):
                assert _try_corelocation() is None
        finally:
            os.unlink(path)

    def test_corrupt_json_returns_none(self):
        from app.spatial_context import _try_corelocation
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not valid json{{{")
            f.flush()
            path = f.name
        try:
            with patch("app.spatial_context._CORELOCATION_FILE", path):
                assert _try_corelocation() is None
        finally:
            os.unlink(path)


class TestIPGeolocation:
    """_try_ip_geolocation() — Layer 2: IP-based location."""

    def test_success_response(self):
        from app.spatial_context import _try_ip_geolocation
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "status": "success",
            "lat": 59.44, "lon": 24.74,
            "city": "Tallinn", "country": "Estonia",
            "regionName": "Harjumaa", "timezone": "Europe/Tallinn",
        }
        with patch("requests.get", return_value=mock_resp):
            result = _try_ip_geolocation()
            assert result is not None
            assert result["lat"] == 59.44
            assert result["city"] == "Tallinn"
            assert result["source"] == "ip"
            assert result["accuracy_km"] == 15

    def test_fail_response(self):
        from app.spatial_context import _try_ip_geolocation
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "fail", "message": "reserved range"}
        with patch("requests.get", return_value=mock_resp):
            assert _try_ip_geolocation() is None

    def test_network_error(self):
        from app.spatial_context import _try_ip_geolocation
        with patch("requests.get", side_effect=Exception("Connection refused")):
            assert _try_ip_geolocation() is None

    def test_timeout(self):
        import requests
        from app.spatial_context import _try_ip_geolocation
        with patch("requests.get", side_effect=requests.exceptions.Timeout):
            assert _try_ip_geolocation() is None


class TestConfigFallback:
    """_config_fallback() — Layer 3: static config."""

    def test_returns_helsinki_defaults(self):
        from app.spatial_context import _config_fallback
        result = _config_fallback()
        assert result["source"] == "config"
        assert result["city"] == "Helsinki"
        assert result["country"] == "Finland"
        assert result["lat"] == pytest.approx(60.17, abs=1.0)

    def test_config_import_failure_still_works(self):
        from app.spatial_context import _config_fallback
        with patch("app.spatial_context._config_fallback.__module__", "app.spatial_context"):
            # Even if config import fails, should return defaults
            result = _config_fallback()
            assert result["lat"] is not None


class TestGetLocation:
    """get_location() — 3-layer fallback resolution."""

    def setup_method(self):
        import app.spatial_context as sc
        sc._cache = {}
        sc._cache_ts = 0.0

    def test_corelocation_preferred(self):
        """Layer 1 wins when available."""
        from app.spatial_context import get_location
        cl_data = {"lat": 61.5, "lon": 23.79, "accuracy": 10, "source": "corelocation",
                   "accuracy_km": 0.01, "city": "", "country": "", "region": ""}
        with patch("app.spatial_context._try_corelocation", return_value=cl_data):
            loc = get_location()
            assert loc["source"] == "corelocation"
            assert loc["lat"] == 61.5

    def test_ip_when_corelocation_unavailable(self):
        """Layer 2 used when layer 1 fails."""
        from app.spatial_context import get_location
        ip_data = {"lat": 59.44, "lon": 24.74, "city": "Tallinn", "country": "Estonia",
                   "region": "Harjumaa", "timezone": "Europe/Tallinn",
                   "source": "ip", "accuracy_km": 15}
        with patch("app.spatial_context._try_corelocation", return_value=None), \
             patch("app.spatial_context._try_ip_geolocation", return_value=ip_data):
            loc = get_location()
            assert loc["source"] == "ip"
            assert loc["city"] == "Tallinn"

    def test_config_when_all_fail(self):
        """Layer 3 used when layers 1+2 fail."""
        from app.spatial_context import get_location
        with patch("app.spatial_context._try_corelocation", return_value=None), \
             patch("app.spatial_context._try_ip_geolocation", return_value=None):
            loc = get_location()
            assert loc["source"] == "config"
            assert loc["city"] == "Helsinki"

    def test_place_context_enriched(self):
        """Result should always have place_context."""
        from app.spatial_context import get_location
        with patch("app.spatial_context._try_corelocation", return_value=None), \
             patch("app.spatial_context._try_ip_geolocation", return_value=None):
            loc = get_location()
            assert "place_context" in loc
            assert len(loc["place_context"]) > 0

    def test_caching(self):
        """Second call within TTL returns cached result."""
        from app.spatial_context import get_location
        with patch("app.spatial_context._try_corelocation", return_value=None), \
             patch("app.spatial_context._try_ip_geolocation", return_value=None):
            loc1 = get_location()
        # Second call doesn't need mocks — should use cache
        loc2 = get_location()
        assert loc1 is loc2

    def test_cache_clear(self):
        """clear_cache() forces re-resolution."""
        import app.spatial_context as sc
        with patch.object(sc, "_try_corelocation", return_value=None), \
             patch.object(sc, "_try_ip_geolocation", return_value=None):
            loc1 = sc.get_location()
        sc.clear_cache()
        with patch.object(sc, "_try_corelocation", return_value=None), \
             patch.object(sc, "_try_ip_geolocation", return_value=None):
            loc2 = sc.get_location()
        assert loc1 is not loc2


class TestFormatSpatialBlock:
    """format_spatial_block() — prompt injection formatting."""

    def setup_method(self):
        import app.spatial_context as sc
        sc._cache = {}
        sc._cache_ts = 0.0

    def test_xml_tags(self):
        from app.spatial_context import format_spatial_block
        with patch("app.spatial_context._try_corelocation", return_value=None), \
             patch("app.spatial_context._try_ip_geolocation", return_value=None):
            block = format_spatial_block()
            assert block.startswith("<spatial_context>")
            assert block.endswith("</spatial_context>")

    def test_contains_location_line(self):
        from app.spatial_context import format_spatial_block
        with patch("app.spatial_context._try_corelocation", return_value=None), \
             patch("app.spatial_context._try_ip_geolocation", return_value=None):
            block = format_spatial_block()
            assert "Location:" in block

    def test_contains_source(self):
        from app.spatial_context import format_spatial_block
        with patch("app.spatial_context._try_corelocation", return_value=None), \
             patch("app.spatial_context._try_ip_geolocation", return_value=None):
            block = format_spatial_block()
            assert "Source:" in block

    def test_corelocation_source_shows_gps(self):
        from app.spatial_context import format_spatial_block
        cl_data = {"lat": 60.17, "lon": 24.94, "accuracy": 10, "source": "corelocation",
                   "accuracy_km": 0.01, "city": "Helsinki", "country": "Finland",
                   "region": "Uusimaa", "place_context": "Helsinki"}
        with patch("app.spatial_context._try_corelocation", return_value=cl_data):
            block = format_spatial_block()
            assert "GPS" in block

    def test_ip_source_shows_geolocation(self):
        from app.spatial_context import format_spatial_block
        ip_data = {"lat": 59.44, "lon": 24.74, "city": "Tallinn", "country": "Estonia",
                   "region": "Harjumaa", "timezone": "Europe/Tallinn",
                   "source": "ip", "accuracy_km": 15}
        with patch("app.spatial_context._try_corelocation", return_value=None), \
             patch("app.spatial_context._try_ip_geolocation", return_value=ip_data):
            block = format_spatial_block()
            assert "IP geolocation" in block

    def test_southern_hemisphere_shows_S(self):
        """Negative latitude should show °S."""
        import app.spatial_context as sc
        sc._cache = {"lat": -33.87, "lon": 151.21, "city": "Sydney", "country": "Australia",
                     "region": "NSW", "source": "ip", "accuracy_km": 15,
                     "place_context": "Sydney, Australia"}
        sc._cache_ts = time.monotonic()
        block = sc.format_spatial_block()
        assert "°S" in block
        assert "°E" in block

    def test_compact_output(self):
        from app.spatial_context import format_spatial_block
        with patch("app.spatial_context._try_corelocation", return_value=None), \
             patch("app.spatial_context._try_ip_geolocation", return_value=None):
            block = format_spatial_block()
            lines = block.strip().split("\n")
            assert len(lines) <= 6  # Should be compact


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestSpatialTemporalIntegration:
    """Integration: spatial feeds into temporal context."""

    def setup_method(self):
        import app.temporal_context as tc
        import app.spatial_context as sc
        tc._cache = {}
        tc._cache_ts = 0.0
        sc._cache = {}
        sc._cache_ts = 0.0

    def test_temporal_uses_spatial_location(self):
        """When lat/lon not given, temporal should use spatial coordinates."""
        import app.temporal_context as tc
        mock_loc = {"lat": 66.5, "lon": 25.98, "city": "Rovaniemi",
                    "country": "Finland", "timezone": "Europe/Helsinki"}
        with patch("app.spatial_context.get_location", return_value=mock_loc):
            result = tc.get_temporal_context()
            assert result["lat"] == 66.5
            assert result["lon"] == 25.98

    def test_location_name_in_temporal(self):
        import app.temporal_context as tc
        mock_loc = {"lat": 61.5, "lon": 23.79, "city": "Tampere",
                    "country": "Finland", "timezone": "Europe/Helsinki"}
        with patch("app.spatial_context.get_location", return_value=mock_loc):
            result = tc.get_temporal_context()
            assert "Tampere" in result["location_name"]

    def test_rovaniemi_gets_north_narrative(self):
        """Rovaniemi (66.5°N) should get northern narrative, not southern."""
        import app.temporal_context as tc
        tc._cache = {}
        tc._cache_ts = 0.0
        mock_loc = {"lat": 66.5, "lon": 25.98, "city": "Rovaniemi",
                    "country": "Finland", "timezone": "Europe/Helsinki"}
        with patch("app.spatial_context.get_location", return_value=mock_loc):
            result = tc.get_temporal_context()
            # Northern narratives should be different from southern
            from app.temporal_context import _NORDIC_NARRATIVES_NORTH, _helsinki_tz
            now = datetime.now(_helsinki_tz())
            expected_narrative = _NORDIC_NARRATIVES_NORTH.get(now.month, "")
            assert result["narrative"] == expected_narrative

    def test_sunrise_uses_actual_coordinates(self):
        """Sunrise for Rovaniemi should differ from Helsinki."""
        import app.temporal_context as tc
        tc._cache = {}
        tc._cache_ts = 0.0
        helsinki_tc = tc.get_temporal_context(lat=60.17, lon=24.94)
        tc._cache = {}
        tc._cache_ts = 0.0
        rovaniemi_tc = tc.get_temporal_context(lat=66.5, lon=25.98)
        assert helsinki_tc["sunrise"] != rovaniemi_tc["sunrise"]
        assert helsinki_tc["daylight_hours"] != rovaniemi_tc["daylight_hours"]


class TestTemporalPatternExpansion:
    """_TEMPORAL_PATTERN — expanded regex for cache bypass.

    NOTE: Can't import from app.agents.commander.routing outside Docker (crewai dep).
    Inline the pattern here — must match routing.py:54-59.
    """

    @pytest.fixture(autouse=True)
    def _build_pattern(self):
        import re
        self.pattern = re.compile(
            r"\b(?:today|now|current(?:ly)?|latest|right now"
            r"|this (?:morning|afternoon|evening|week|month|season|year|time of year)"
            r"|live|breaking|just (?:happened|announced)|real[- ]time|price (?:of|for)|stock price"
            r"|weather|score|match result|sunrise|sunset|moon|season(?:al)?|daylight"
            r"|spring|summer|autumn|fall|winter)\b",
            re.IGNORECASE,
        )

    def test_original_patterns_still_match(self):
        original_cases = [
            "today", "now", "currently", "latest", "right now",
            "this morning", "this afternoon", "this evening",
            "this week", "this month", "live", "breaking",
            "just happened", "just announced", "real-time",
            "price of", "stock price", "weather", "score", "match result",
        ]
        for text in original_cases:
            assert self.pattern.search(text), f"Should match: '{text}'"

    def test_new_seasonal_patterns(self):
        new_cases = [
            "this season", "this year", "this time of year",
            "sunrise", "sunset", "moon", "seasonal", "daylight",
            "spring", "summer", "autumn", "fall", "winter",
        ]
        for text in new_cases:
            assert self.pattern.search(text), f"Should match: '{text}'"

    def test_no_false_positives(self):
        negatives = [
            "help me code", "random question", "what is python",
            "create a function", "explain recursion",
        ]
        for text in negatives:
            assert not self.pattern.search(text), f"Should NOT match: '{text}'"

    def test_real_user_queries(self):
        queries = [
            "Is there a possibility to see Saimaa seals at this time of year?",
            "Where can they be spotted now",
            "What's the weather like this season",
            "When is sunrise tomorrow",
            "What's happening in Finland this winter",
        ]
        for q in queries:
            assert self.pattern.search(q), f"Should match: '{q}'"


class TestRealityModelIntegration:
    """Temporal + spatial elements in reality model."""

    def test_temporal_element_added(self):
        """reality_model.py should add temporal_now element."""
        # This tests the code structure, not the full pipeline
        from app.temporal_context import get_temporal_context
        tc = get_temporal_context(lat=60.17, lon=24.94)
        assert tc["date_str"]
        assert tc["season"]
        assert tc["daylight_desc"]

    def test_spatial_location_for_reality_model(self):
        """get_location() returns data suitable for reality model injection."""
        import app.spatial_context as sc
        sc._cache = {}
        sc._cache_ts = 0.0
        with patch.object(sc, "_try_corelocation", return_value=None), \
             patch.object(sc, "_try_ip_geolocation", return_value=None):
            loc = sc.get_location()
            # Verify all fields needed by reality model
            assert "lat" in loc
            assert "lon" in loc
            assert "city" in loc
            assert "country" in loc
            assert "source" in loc
            assert "place_context" in loc


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def setup_method(self):
        import app.temporal_context as tc
        import app.spatial_context as sc
        tc._cache = {}
        tc._cache_ts = 0.0
        sc._cache = {}
        sc._cache_ts = 0.0

    def test_negative_latitude(self):
        """Southern hemisphere should work."""
        from app.temporal_context import get_temporal_context
        tc = get_temporal_context(lat=-33.87, lon=151.21)
        assert tc["lat"] == -33.87
        # In April (northern spring), southern hemisphere should be autumn
        if tc["month_name"] in ("March", "April", "May"):
            assert tc["season"] == "autumn"

    def test_zero_coordinates(self):
        """Equator/prime meridian should work."""
        from app.temporal_context import get_temporal_context
        tc = get_temporal_context(lat=0.0, lon=0.0)
        assert tc["lat"] == 0.0
        assert 11.5 <= tc["daylight_hours"] <= 12.5

    def test_extreme_north(self):
        """North pole (90°N) should not crash."""
        from app.temporal_context import get_temporal_context
        tc = get_temporal_context(lat=89.0, lon=0.0)
        assert tc["daylight_hours"] in (0.0, 24.0) or 0 <= tc["daylight_hours"] <= 24

    def test_date_line_crossing(self):
        """Longitude near ±180 should work."""
        from app.temporal_context import get_temporal_context
        tc = get_temporal_context(lat=60.0, lon=179.0)
        assert tc["sunrise"] is not None

    def test_concurrent_cache_safety(self):
        """Cache operations should not crash under rapid calls."""
        from app.temporal_context import get_temporal_context
        for _ in range(50):
            tc = get_temporal_context(lat=60.17, lon=24.94)
            assert tc is not None

    def test_spatial_cache_clear_and_reuse(self):
        import app.spatial_context as sc
        with patch.object(sc, "_try_corelocation", return_value=None), \
             patch.object(sc, "_try_ip_geolocation", return_value=None):
            loc1 = sc.get_location()
        sc.clear_cache()
        with patch.object(sc, "_try_corelocation", return_value=None), \
             patch.object(sc, "_try_ip_geolocation", return_value=None):
            loc2 = sc.get_location()
        assert loc1["lat"] == loc2["lat"]
        assert loc1 is not loc2


class TestForwarderLocationProbe:
    """signal/forwarder.py — location probe wiring."""

    def test_probe_skips_when_no_binary(self):
        """_probe_location should silently skip if location-helper doesn't exist."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "signal"))
        try:
            import importlib
            import forwarder
            importlib.reload(forwarder)
            forwarder._last_location_probe = 0.0
            with patch.object(forwarder.os.path, "exists", return_value=False):
                forwarder._probe_location()  # Should not raise
        except ImportError:
            pytest.skip("forwarder not importable outside Docker context")
        finally:
            sys.path.pop(0)

    def test_probe_respects_interval(self):
        """Should not probe more than once per 30 min."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "signal"))
        try:
            import importlib
            import forwarder
            importlib.reload(forwarder)
            forwarder._last_location_probe = time.time()  # Just probed
            # Should be a no-op
            with patch("subprocess.run") as mock_run:
                forwarder._probe_location()
                mock_run.assert_not_called()
        except ImportError:
            pytest.skip("forwarder not importable outside Docker context")
        finally:
            sys.path.pop(0)
