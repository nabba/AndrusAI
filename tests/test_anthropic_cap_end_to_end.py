"""End-to-end pin: setting → pre_check → call_or_skip → real call site
(Phase D.3 integration follow-up, 2026-05-22).

Unit tests cover each layer individually. This test cuts straight
through the wire to prove the layers compose correctly:

  1. runtime_settings.set_anthropic_daily_cap_usd(LOW)
  2. An audit-log row written that records prior Anthropic spend ≥ cap
  3. analogy_populator._default_llm_call invoked
  4. → returns "" (skipped) WITHOUT calling anthropic SDK

The fail-mode this catches: any of the four layers silently breaks
the chain (e.g. the populator stops importing the budget module, or
the budget module stops reading runtime_settings, or pre_check stops
raising the right exception type, or call_or_skip catches too broadly).

Two integration paths are pinned:
  * **Cap exceeded** → real call site short-circuits.
  * **Cap not exceeded** → real call site proceeds to the (stubbed)
    anthropic call.

The anthropic SDK is mocked so the test runs without network or the
real SDK installed. The mock raises if called when the cap should have
short-circuited it — that's the load-bearing invariant.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Stubs ────────────────────────────────────────────────────────────


_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        return None
    return mod


# Load the budget module directly so we can monkeypatch its
# importlib.import_module path without dragging in runtime_settings.
ab = _load_module("_ab_e2e", "app/llm_anthropic_budget.py")


@pytest.fixture
def isolated_audit_log(tmp_path, monkeypatch):
    """Install a fake audit_log module that points at a tmp JSONL
    file, and seed it with rows."""
    log = tmp_path / "audit.jsonl"

    def _seed(rows: list[dict]) -> None:
        with log.open("w", encoding="utf-8") as fp:
            for r in rows:
                fp.write(json.dumps(r) + "\n")

    fake_al = MagicMock()
    fake_al._audit_log_path.return_value = log
    monkeypatch.setitem(sys.modules, "app.audit_log", fake_al)

    return _seed


@pytest.fixture
def isolated_runtime_settings(monkeypatch):
    """Install a fake runtime_settings module whose
    get_anthropic_daily_cap_usd is controllable per-test."""
    state = {"cap_usd": None}
    fake_rs = MagicMock()
    fake_rs.get_anthropic_daily_cap_usd.side_effect = (
        lambda: state["cap_usd"]
    )
    monkeypatch.setitem(sys.modules, "app.runtime_settings", fake_rs)

    def _set(cap_usd):
        state["cap_usd"] = cap_usd

    return _set


# ── End-to-end through call_or_skip ─────────────────────────────────


@pytest.mark.skipif(ab is None, reason="llm_anthropic_budget not loadable")
class TestCallOrSkipEndToEnd:
    """Pin the full read chain: settings → pre_check → call_or_skip."""

    def test_cap_exceeded_short_circuits(
        self, isolated_audit_log, isolated_runtime_settings,
    ):
        # 1. Operator sets the cap
        isolated_runtime_settings(25.0)
        # 2. Audit log already shows >= cap spent in last 24h
        now = datetime.now(timezone.utc).isoformat()
        isolated_audit_log([
            {"ts": now, "model": "claude-sonnet-4.5", "cost_usd": 30.0},
        ])
        # 3. A site invokes call_or_skip
        proceed = ab.call_or_skip(
            estimated_cost_usd=0.005, source="test:integration",
        )
        # 4. The site sees False → it must skip
        assert proceed is False

    def test_cap_not_exceeded_proceeds(
        self, isolated_audit_log, isolated_runtime_settings,
    ):
        isolated_runtime_settings(25.0)
        now = datetime.now(timezone.utc).isoformat()
        isolated_audit_log([
            # Small prior spend
            {"ts": now, "model": "claude-haiku-4-5", "cost_usd": 0.10},
        ])
        proceed = ab.call_or_skip(
            estimated_cost_usd=0.005, source="test:integration",
        )
        # 0.10 + 0.005 = 0.105 ≪ 25 → proceeds
        assert proceed is True

    def test_cap_disabled_always_proceeds(
        self, isolated_audit_log, isolated_runtime_settings,
    ):
        isolated_runtime_settings(None)  # cap disabled
        now = datetime.now(timezone.utc).isoformat()
        # Huge prior spend — would exceed any reasonable cap
        isolated_audit_log([
            {"ts": now, "model": "claude-opus", "cost_usd": 1_000.0},
        ])
        proceed = ab.call_or_skip(
            estimated_cost_usd=10.0, source="test:integration",
        )
        # Cap is disabled → no ceiling → always True
        assert proceed is True

    def test_anthropic_rows_only_counted(
        self, isolated_audit_log, isolated_runtime_settings,
    ):
        isolated_runtime_settings(25.0)
        now = datetime.now(timezone.utc).isoformat()
        # Big GPT-4o spend, small Anthropic spend
        isolated_audit_log([
            {"ts": now, "model": "gpt-4o", "cost_usd": 100.0},
            {"ts": now, "model": "claude-haiku-4-5", "cost_usd": 5.0},
        ])
        proceed = ab.call_or_skip(
            estimated_cost_usd=10.0, source="test:integration",
        )
        # Only the $5 Anthropic spend counts toward the cap
        # 5 + 10 = 15 < 25 → True
        assert proceed is True

    def test_state_snapshot_reflects_full_chain(
        self, isolated_audit_log, isolated_runtime_settings,
    ):
        # Operator views the state — reflects the full chain
        isolated_runtime_settings(25.0)
        now = datetime.now(timezone.utc).isoformat()
        isolated_audit_log([
            {"ts": now, "model": "claude-sonnet", "cost_usd": 10.0},
        ])
        snap = ab.state_snapshot()
        assert snap["enabled"] is True
        assert snap["cap_usd"] == 25.0
        assert snap["spent_usd_24h"] == 10.0
        assert snap["headroom_usd"] == 15.0


# ── End-to-end at the real call site (analogy_populator) ────────────


@pytest.mark.skipif(ab is None, reason="llm_anthropic_budget not loadable")
class TestAnalogyPopulatorIntegration:
    """The most realistic shape: populator's _default_llm_call invoked
    with the cap exceeded.

    Stubs anthropic SDK so the test doesn't need it installed. The
    stubbed client RAISES on instantiation — proving the
    short-circuit prevents reaching the SDK at all when the cap is
    breached. (If pre_check ever stopped firing, the AssertionError
    from the stub would fail the test loudly.)
    """

    def _load_populator(self):
        """Load analogy_populator with anthropic stubbed to a sentinel."""
        # Install a stub anthropic module that explodes on Anthropic()
        # call — proving the short-circuit prevents reaching it.
        sentinel_calls = []

        class _ExplodingClient:
            def __init__(self, *a, **k):
                sentinel_calls.append("INSTANTIATED")
                raise AssertionError(
                    "anthropic.Anthropic() was called — pre_check "
                    "short-circuit FAILED"
                )

        fake_anthropic = MagicMock()
        fake_anthropic.Anthropic = _ExplodingClient
        sys.modules["anthropic"] = fake_anthropic

        m = _load_module(
            "_ap_e2e", "app/creativity/analogy_populator.py",
        )
        return m, sentinel_calls

    def test_cap_exceeded_skips_real_anthropic_call(
        self, isolated_audit_log, isolated_runtime_settings,
    ):
        # Cap is set low; audit log shows we've exceeded it
        isolated_runtime_settings(1.00)
        now = datetime.now(timezone.utc).isoformat()
        isolated_audit_log([
            {"ts": now, "model": "claude-sonnet-4.5", "cost_usd": 2.50},
        ])

        # The populator's _default_llm_call must skip BEFORE
        # touching the anthropic SDK
        populator, sentinel_calls = self._load_populator()
        if populator is None or not hasattr(populator, "_default_llm_call"):
            pytest.skip(
                "analogy_populator import dependencies missing on host",
            )

        # Patch populator's reference to llm_anthropic_budget to point
        # at the directly-loaded `ab` module (which knows about our
        # isolated_runtime_settings + isolated_audit_log via the
        # importlib.import_module call inside get_cap / today_spent_usd).
        with patch.dict(
            sys.modules, {"app.llm_anthropic_budget": ab},
        ):
            result = populator._default_llm_call(
                system="test", user="test",
            )

        # The populator's empty-string sentinel — cap-out path
        assert result == ""
        # And the Anthropic SDK was NEVER touched
        assert sentinel_calls == [], (
            "anthropic SDK was instantiated; short-circuit failed"
        )

    def test_cap_disabled_does_attempt_call(
        self, isolated_audit_log, isolated_runtime_settings,
    ):
        # Cap is None → no ceiling → populator should reach the SDK
        # (which our stub raises AssertionError from on Anthropic()).
        # We expect the AssertionError to fire, proving the gate
        # didn't intercept.
        isolated_runtime_settings(None)
        isolated_audit_log([])

        populator, sentinel_calls = self._load_populator()
        if populator is None or not hasattr(populator, "_default_llm_call"):
            pytest.skip(
                "analogy_populator import dependencies missing on host",
            )

        with patch.dict(
            sys.modules, {"app.llm_anthropic_budget": ab},
        ):
            # The exploding stub turns AssertionError into a "" via
            # the populator's outer try/except. So result == "".
            # But sentinel_calls must show the SDK WAS touched.
            result = populator._default_llm_call(
                system="test", user="test",
            )
        assert result == ""  # outer try/except caught the AssertionError
        assert sentinel_calls == ["INSTANTIATED"], (
            "SDK was NOT reached when cap was disabled — "
            "gate is incorrectly intercepting"
        )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
