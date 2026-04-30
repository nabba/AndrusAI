"""Tests for the credit-aware Anthropic LLM with per-call wall-clock guard.

Background — added 2026-04-30 after a 28-minute PIM stall: a hung
Anthropic call (no response, no error) blocked the agent until the
orchestrator's 15-min soft-timeout fired with zero output. This test
suite locks in the failover semantics:

  1. Credit-exhausted 400 → trip breaker, build fallback, retry. (pre-
     existing behaviour the new code must preserve.)
  2. Wall-clock timeout on direct call → trip breaker, build fallback,
     retry. (NEW — the actual stall fix.)
  3. Generic exception (not credit-exhausted, not timeout) → propagate
     unchanged. The credit-aware layer is NOT a catch-all.

The Anthropic SDK is heavy and we can't hit the live API in tests, so
we patch the parent class's ``call`` / ``acall`` directly to inject
the failure shape we want.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from app.llms import credit_aware_anthropic as ca
from app.llms.credit_aware_anthropic import (
    CreditAwareAnthropicCompletion,
    _AnthropicCallTimeout,
    is_anthropic_timeout,
    is_credit_exhausted_error,
)


# ── Detection predicates ─────────────────────────────────────────────

class TestPredicates:

    def test_credit_exhausted_match(self):
        exc = Exception(
            "400 invalid_request_error: Your credit balance is too low "
            "to access the Anthropic API."
        )
        assert is_credit_exhausted_error(exc)

    def test_credit_exhausted_no_match_other_400(self):
        exc = Exception("400: temperature must be between 0 and 1")
        assert not is_credit_exhausted_error(exc)

    def test_timeout_match(self):
        exc = _AnthropicCallTimeout(180.0)
        assert is_anthropic_timeout(exc)
        assert not is_credit_exhausted_error(exc)

    def test_timeout_no_match_generic(self):
        exc = TimeoutError("connection timed out")  # generic socket timeout
        # Marker substring not present → must NOT trigger our failover path
        assert not is_anthropic_timeout(exc)


# ── Timeout config resolution ────────────────────────────────────────

class TestResolveCallTimeout:

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("CREDIT_AWARE_CALL_TIMEOUT_SECS", raising=False)
        assert ca._resolve_call_timeout() == ca._DEFAULT_CALL_TIMEOUT_SECS

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("CREDIT_AWARE_CALL_TIMEOUT_SECS", "30")
        assert ca._resolve_call_timeout() == 30.0

    def test_zero_disables(self, monkeypatch):
        monkeypatch.setenv("CREDIT_AWARE_CALL_TIMEOUT_SECS", "0")
        assert ca._resolve_call_timeout() == 0.0

    def test_negative_disables(self, monkeypatch):
        monkeypatch.setenv("CREDIT_AWARE_CALL_TIMEOUT_SECS", "-1")
        assert ca._resolve_call_timeout() == 0.0

    def test_floor_at_5s(self, monkeypatch):
        """A misconfigured 1s would make every legitimate call fail —
        floor it so a typo can't break production."""
        monkeypatch.setenv("CREDIT_AWARE_CALL_TIMEOUT_SECS", "1")
        assert ca._resolve_call_timeout() == 5.0

    def test_invalid_falls_back(self, monkeypatch):
        monkeypatch.setenv("CREDIT_AWARE_CALL_TIMEOUT_SECS", "garbage")
        assert ca._resolve_call_timeout() == ca._DEFAULT_CALL_TIMEOUT_SECS


# ── Failover semantics ───────────────────────────────────────────────
#
# We avoid instantiating the real CreditAwareAnthropicCompletion (which
# would try to validate Pydantic fields like model name + API key) by
# patching ``super().call`` indirectly via the parent module path.
# The simpler approach for these tests: instantiate via Pydantic's
# model_construct (skips validation) and then drive the methods.


@pytest.fixture
def make_llm(monkeypatch):
    """Factory that returns a barely-initialised CreditAwareAnthropicCompletion
    plus an attached fallback mock. Patches the parent's call/acall so we
    can simulate any failure shape."""
    def _factory(parent_call_behaviour, *, fallback_result="from-or"):
        # Bypass Pydantic validation — we don't need a real Anthropic
        # connection for these tests.
        llm = CreditAwareAnthropicCompletion.model_construct(
            model="claude-sonnet-4.6",
        )
        # Attach fallback mock (.call returns fixed string)
        fallback = MagicMock()
        fallback.call = MagicMock(return_value=fallback_result)
        async def _afallback(*a, **k):
            return fallback_result
        fallback.acall = _afallback
        llm.set_fallback_factory(lambda: fallback)
        # Patch the parent class methods used by `super().call(...)`.
        from crewai.llms.providers.anthropic.completion import AnthropicCompletion
        monkeypatch.setattr(AnthropicCompletion, "call", parent_call_behaviour)
        async def _aparent(self, *a, **k):
            return parent_call_behaviour(self, *a, **k)
        monkeypatch.setattr(AnthropicCompletion, "acall", _aparent)
        # Ensure breaker starts closed. The breaker module doesn't
        # expose a public reset; we record successes equal to the
        # current failure count to bring it back to CLOSED.
        from app import circuit_breaker
        b = circuit_breaker.get_breaker("anthropic_credits")
        for _ in range(b.failure_count + 1):
            circuit_breaker.record_success("anthropic_credits")
        return llm, fallback
    return _factory


def test_happy_path_uses_anthropic_directly(make_llm):
    """No error, no timeout — return parent's value, fallback never fires."""
    def _ok(self, *a, **k):
        return "from-anthropic"
    llm, fallback = make_llm(_ok)
    assert llm.call() == "from-anthropic"
    fallback.call.assert_not_called()


def test_credit_exhausted_400_routes_to_fallback(make_llm):
    """Pre-existing behaviour: a credit-exhausted 400 must trip the
    breaker and route to OpenRouter."""
    def _credit_400(self, *a, **k):
        raise Exception(
            "400 invalid_request_error: Your credit balance is too low."
        )
    llm, fallback = make_llm(_credit_400)
    out = llm.call()
    assert out == "from-or"
    fallback.call.assert_called_once()
    # Breaker should be tripped
    from app import circuit_breaker
    assert not circuit_breaker.is_available("anthropic_credits")


def test_unrelated_exception_propagates(make_llm):
    """Non-credit, non-timeout exceptions must not be swallowed."""
    def _bad(self, *a, **k):
        raise ValueError("temperature out of range")
    llm, fallback = make_llm(_bad)
    with pytest.raises(ValueError, match="temperature"):
        llm.call()
    fallback.call.assert_not_called()


def test_timeout_routes_to_fallback(monkeypatch, make_llm):
    """The headline fix: when the direct call hangs longer than the
    configured timeout, we fail over to OpenRouter rather than block."""
    monkeypatch.setenv("CREDIT_AWARE_CALL_TIMEOUT_SECS", "5")  # floor

    def _hang(self, *a, **k):
        # Simulate a hung Anthropic call — sleeps far longer than the
        # 5s test timeout. The wrapper should fire after 5s.
        time.sleep(60)
        return "would-have-been-anthropic"

    llm, fallback = make_llm(_hang)
    t0 = time.monotonic()
    out = llm.call()
    elapsed = time.monotonic() - t0
    # Failover must complete via fallback in roughly the timeout window
    # (5s) rather than waiting the full 60s sleep.
    assert out == "from-or"
    assert elapsed < 30, f"failover took {elapsed:.1f}s — timeout did not fire"
    fallback.call.assert_called_once()
    # Breaker should be tripped (timeout treated like credit exhaustion)
    from app import circuit_breaker
    assert not circuit_breaker.is_available("anthropic_credits")


def test_timeout_disabled_via_env(monkeypatch, make_llm):
    """Setting CREDIT_AWARE_CALL_TIMEOUT_SECS=0 must disable the
    wrapper entirely (escape hatch for callers who really want
    unbounded waits)."""
    monkeypatch.setenv("CREDIT_AWARE_CALL_TIMEOUT_SECS", "0")

    # Use a fast-returning parent so the test doesn't actually hang
    def _ok(self, *a, **k):
        return "no-timeout-wrap"
    llm, fallback = make_llm(_ok)
    assert llm.call() == "no-timeout-wrap"


def test_breaker_open_uses_fallback_without_direct_probe(make_llm):
    """When the breaker is already OPEN at call entry, we must skip
    the direct Anthropic path entirely — no probe at all. This is what
    keeps cached LLM instances honest after credits are exhausted."""
    from app import circuit_breaker

    direct_call_count = {"n": 0}

    def _count_calls(self, *a, **k):
        direct_call_count["n"] += 1
        return "should-not-be-called"

    llm, fallback = make_llm(_count_calls)
    # Trip the breaker BEFORE calling
    circuit_breaker.record_failure("anthropic_credits")
    assert not circuit_breaker.is_available("anthropic_credits")

    out = llm.call()
    assert out == "from-or"
    assert direct_call_count["n"] == 0
    fallback.call.assert_called_once()
