"""Tests for the 2026-04-25 vetting-timeout fix.

Background — task #88 stalled 17.7 min in `_verify_full` because the
upstream LLM (gpt-5.5 / openrouter) didn't return.  The synthesis output
that the crew had ALREADY produced was lost, and the user got a
soft-timeout reply instead of the answer.

These tests prove that:

  1. ``_call_llm_bounded`` fires ``VettingTimeout`` when an LLM call
     exceeds the wall-clock budget (and does NOT block forever).
  2. ``_verify_full`` returns the **original unvetted response** when
     its LLM call times out — so callers see graceful degradation, not
     a stall.
  3. ``_verify_cheap`` returns ``(False, response)`` on timeout so the
     caller escalates to full (which itself is bounded).
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from app import vetting
from app.vetting import (
    VettingTimeout,
    _call_llm_bounded,
    _verify_cheap,
    _verify_full,
)


# ══════════════════════════════════════════════════════════════════════
# _call_llm_bounded — wall-clock ceiling
# ══════════════════════════════════════════════════════════════════════

class TestCallLlmBounded:

    def test_returns_quickly_when_llm_returns_quickly(self):
        llm = MagicMock()
        llm.call.return_value = "PASS"
        out = _call_llm_bounded(llm, "test prompt", timeout_s=5)
        assert out == "PASS"

    def test_strips_whitespace(self):
        llm = MagicMock()
        llm.call.return_value = "  hello world  \n"
        out = _call_llm_bounded(llm, "test prompt", timeout_s=5)
        assert out == "hello world"

    def test_raises_vetting_timeout_when_llm_hangs(self):
        """The CRITICAL test — without this guard, task 88 stalled 17m."""
        llm = MagicMock()

        def _hang(*_a, **_kw):
            time.sleep(60)  # would hang 60s if uncapped
            return "PASS"

        llm.call.side_effect = _hang
        t0 = time.monotonic()
        with pytest.raises(VettingTimeout) as exc_info:
            _call_llm_bounded(llm, "test prompt", timeout_s=0.5)
        elapsed = time.monotonic() - t0
        # Must fail fast — within ~1s, not 60s
        assert elapsed < 2.0, f"timeout didn't fire in time (took {elapsed:.2f}s)"
        assert "0s" in str(exc_info.value) or "exceeded" in str(exc_info.value)

    def test_raises_vetting_timeout_propagates_inner_exception(self):
        """When the LLM raises, _call_llm_bounded re-raises the original."""
        llm = MagicMock()
        llm.call.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError, match="boom"):
            _call_llm_bounded(llm, "test prompt", timeout_s=5)


# ══════════════════════════════════════════════════════════════════════
# _verify_full — graceful degradation on timeout
# ══════════════════════════════════════════════════════════════════════

class TestVerifyFullTimeout:

    def test_returns_original_response_on_llm_timeout(self, monkeypatch):
        """The 2026-04-25 task-88 regression — vetting must NOT lose the
        synthesis result when the upstream LLM hangs."""
        slow_llm = MagicMock()

        def _hang(*_a, **_kw):
            time.sleep(60)
            return "PASS"

        slow_llm.call.side_effect = _hang
        monkeypatch.setattr(vetting, "_get_full_vetting_llm", lambda: slow_llm)
        # Force the bounded-call deadline very low so the test runs fast.
        monkeypatch.setattr(vetting, "_VET_LLM_TIMEOUT_S", 0.5)

        original = "The PSP table the crew produced — many rows, real data."
        result = _verify_full(
            user_request="enrich PSP list",
            response=original,
            crew_name="research",
        )
        # Must echo the synthesis result back unchanged.
        assert result == original

    def test_records_failed_outcome_on_timeout(self, monkeypatch):
        """Timeout-as-failure feeds the benchmarks signal so the selector
        learns the slow model is unreliable."""
        slow_llm = MagicMock()
        slow_llm.call.side_effect = lambda *_a, **_kw: time.sleep(60) or "PASS"
        monkeypatch.setattr(vetting, "_get_full_vetting_llm", lambda: slow_llm)
        monkeypatch.setattr(vetting, "_VET_LLM_TIMEOUT_S", 0.5)

        recorded = []
        monkeypatch.setattr(
            vetting, "_record_vetting_outcome",
            lambda model, crew, passed, ms: recorded.append(
                {"model": model, "crew": crew, "passed": passed}
            ),
        )

        _verify_full(
            user_request="enrich PSP list",
            response="real synthesis output here",
            crew_name="research",
            generating_model="gemma-4-31b-it",
        )
        # The outcome must reflect that vetting failed (timeout).
        assert recorded == [{
            "model": "gemma-4-31b-it",
            "crew": "research",
            "passed": False,
        }]

    def test_passes_through_on_normal_pass_verdict(self, monkeypatch):
        """Sanity — happy path still works when vetting LLM responds in time."""
        good_llm = MagicMock()
        good_llm.call.return_value = '{"verdict": "PASS"}'
        monkeypatch.setattr(vetting, "_get_full_vetting_llm", lambda: good_llm)

        result = _verify_full(
            user_request="enrich PSP list",
            response="real output",
            crew_name="research",
        )
        assert result == "real output"


# ══════════════════════════════════════════════════════════════════════
# _verify_cheap — escalates to full on timeout
# ══════════════════════════════════════════════════════════════════════

class TestVerifyCheapTimeout:

    def test_returns_false_on_timeout_so_caller_escalates(self, monkeypatch):
        slow_llm = MagicMock()
        slow_llm.call.side_effect = lambda *_a, **_kw: time.sleep(60) or "PASS"
        monkeypatch.setattr(vetting, "_get_cheap_vetting_llm", lambda: slow_llm)
        monkeypatch.setattr(vetting, "_VET_LLM_TIMEOUT_S", 0.5)

        passed, returned = _verify_cheap(
            user_request="enrich PSP list",
            response="some output",
            crew_name="research",
        )
        # The contract: cheap-tier returns (False, response) on failure
        # so the orchestrator escalates to _verify_full (which is itself
        # bounded — see TestVerifyFullTimeout above).
        assert passed is False
        assert returned == "some output"
