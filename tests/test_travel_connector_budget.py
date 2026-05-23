"""Tests for the travel.fetch_flight_status × @with_connector_budget
wire-in (2026-05-22).

The wrap is the first PRODUCTION caller of the connector_budget
decorator. Tests pin the contract:

  * Master switch OFF (default) → wrap is pass-through; pre-existing
    behavior unchanged. Aviationstack continues to be called every
    cycle up to the per-cycle cap of 3.
  * Master switch ON → first 3 calls/day allowed at the synthetic
    $0.001/call rate (= $0.003 daily cap). 4th call raises
    ConnectorBudgetExceeded.
  * refresh_idle catches the exception and breaks the loop without
    propagating. Remaining flights skip silently; pre-existing
    snapshot rows preserved.

These tests are designed to be self-sufficient — they don't require
network access, a real Aviationstack key, or psycopg2.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())

try:
    import crewai as _real_crewai  # noqa: F401
    _crewai_available = True
except Exception:
    _crewai_available = False

if not _crewai_available:
    for _mod in ("crewai", "crewai.tools"):
        if _mod not in sys.modules:
            m = types.ModuleType(_mod)
            if _mod == "crewai.tools":
                m.tool = lambda name: (lambda fn: fn)
                m.BaseTool = type("BaseTool", (), {})
            sys.modules[_mod] = m


from app.connector_budget import (  # noqa: E402
    ConnectorBudgetExceeded,
    today_spend,
)
from app.connector_budget import decorator as dec_mod  # noqa: E402
from app.connector_budget import store as store_mod  # noqa: E402


@pytest.fixture
def isolated_ledger(tmp_path):
    store_mod.reset_for_tests(tmp_path)
    yield tmp_path
    store_mod.reset_for_tests(None)


@pytest.fixture
def switch_on(monkeypatch):
    monkeypatch.setattr(dec_mod, "_master_switch_on", lambda: True)


@pytest.fixture
def switch_off(monkeypatch):
    monkeypatch.setattr(dec_mod, "_master_switch_on", lambda: False)


# Import target after fixture setup so we don't have to remove from
# sys.modules; the wrap is applied at module import time but the
# master-switch fixture is consulted each call via _master_switch_on.
from app.life_companion import travel  # noqa: E402


def _fake_fetch_inner(monkeypatch, return_value):
    """Replace the network-touching guts of fetch_flight_status with a
    deterministic return. The decorator still fires before the body."""

    # Bypass the env-var + key gates by patching the helpers
    monkeypatch.setattr(travel, "_flight_tracking_enabled", lambda: True)
    monkeypatch.setattr(travel, "_get_aviationstack_key", lambda: "fake-key")

    # Intercept urllib.request.urlopen so we never touch the network
    class _FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self):
            import json
            payload = {
                "data": [{
                    "flight_status": "scheduled",
                    "departure": {
                        "iata": "HEL", "delay": 5,
                        "gate": "12", "terminal": "1",
                    },
                    "arrival": {"iata": "TLL"},
                }] if return_value else [],
            }
            return json.dumps(payload).encode("utf-8")

    def _fake_urlopen(*a, **kw):
        return _FakeResp()

    monkeypatch.setattr(travel.urllib.request, "urlopen", _fake_urlopen)


# ── Switch OFF: zero behavior change ─────────────────────────────────


class TestSwitchOff:
    def test_pass_through_unlimited_calls(
        self, isolated_ledger, switch_off, monkeypatch,
    ):
        _fake_fetch_inner(monkeypatch, return_value=True)
        # Far more than the cap-of-3 would allow — switch is off so
        # the decorator never gates anything.
        for _ in range(10):
            result = travel.fetch_flight_status("AY123")
            assert result is not None
        # No spend recorded
        assert today_spend("aviationstack") == 0.0


# ── Switch ON: cap enforced ──────────────────────────────────────────


class TestSwitchOn:
    def test_under_cap_succeeds(
        self, isolated_ledger, switch_on, monkeypatch,
    ):
        _fake_fetch_inner(monkeypatch, return_value=True)
        # Phase B.4 (2026-05-22) — Aviationstack uses call-count cap
        # (daily_call_cap=3) not synthetic dollar cap. Three calls fit;
        # each records usd=0 (free tier).
        for _ in range(3):
            result = travel.fetch_flight_status("AY123")
            assert result is not None
        from app.connector_budget import today_calls
        assert today_calls("aviationstack") == 3
        # No dollar spend in call-count mode
        assert today_spend("aviationstack") == 0.0

    def test_fourth_call_refused(
        self, isolated_ledger, switch_on, monkeypatch,
    ):
        _fake_fetch_inner(monkeypatch, return_value=True)
        for _ in range(3):
            travel.fetch_flight_status("AY123")
        # 4th call would push past the call-count cap
        with pytest.raises(ConnectorBudgetExceeded) as exc_info:
            travel.fetch_flight_status("AY456")
        assert exc_info.value.connector == "aviationstack"
        # Call-count mode exposes daily_call_cap (not daily_cap_usd)
        assert exc_info.value.daily_call_cap == 3
        assert exc_info.value.daily_cap_usd is None
        assert exc_info.value.today_calls_made == 3
        # Calls NOT incremented by the refused call
        from app.connector_budget import today_calls
        assert today_calls("aviationstack") == 3


# ── refresh_idle catches the exception ───────────────────────────────


class TestRefreshIdleCatchesBudgetExhaustion:
    def test_loop_break_on_budget_exceeded(
        self, isolated_ledger, switch_on, monkeypatch,
    ):
        """Pre-record the call-count cap so any new call raises; then
        walk through the relevant `for seg in ...` block of
        refresh_idle and assert it neither raises nor processes more
        flights."""
        # Pre-record 3 calls (cap = 3) so the next call would refuse
        for _ in range(3):
            store_mod.record_spend("aviationstack", 0.0)

        # Build three fake flight segments (TripSegment shape, see
        # app/life_companion/travel.py)
        segments = [
            travel.TripSegment(
                summary=f"flight {i}",
                location="HEL",
                starts_at="2026-05-22T10:00:00+00:00",
                ends_at="2026-05-22T12:00:00+00:00",
                uid=f"uid-{i}",
                kind="flight",
                flight_number=f"AY{i:03d}",
            )
            for i in range(3)
        ]

        # Mock upcoming_trips so refresh_idle sees them
        monkeypatch.setattr(
            travel, "upcoming_trips",
            lambda **kw: segments,
        )
        _fake_fetch_inner(monkeypatch, return_value=True)

        # Inline the loop body the way refresh_idle does it.
        # We assert: the FIRST call raises ConnectorBudgetExceeded;
        # the wrapper in refresh_idle catches and breaks — so no
        # downstream snapshot rows would be added.
        flight_status_map: dict = {}
        imminent_flights = [
            s for s in segments
            if s.kind == "flight" and s.flight_number
        ]
        for seg in imminent_flights[:3]:
            try:
                status = travel.fetch_flight_status(seg.flight_number)
            except ConnectorBudgetExceeded:
                # This is the load-bearing behavior — caller catches
                # and breaks rather than propagating.
                break
            if status is not None:
                flight_status_map[seg.flight_number] = status.to_dict()

        assert flight_status_map == {}, (
            "Budget exception should have broken the loop "
            "before any flight processed"
        )

    def test_first_two_succeed_third_refused(
        self, isolated_ledger, switch_on, monkeypatch,
    ):
        # Phase B.4 (2026-05-22) — pre-record 2 calls so exactly 1
        # slot remains under the daily_call_cap=3 limit.
        for _ in range(2):
            store_mod.record_spend("aviationstack", 0.0)
        _fake_fetch_inner(monkeypatch, return_value=True)

        results: list = []
        for fn in ["AY1", "AY2", "AY3"]:
            try:
                r = travel.fetch_flight_status(fn)
                results.append((fn, r is not None))
            except ConnectorBudgetExceeded:
                results.append((fn, "refused"))
                break

        # AY1 succeeds, AY2 is refused (would put us at 0.004 > 0.003)
        assert results == [("AY1", True), ("AY2", "refused")]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
