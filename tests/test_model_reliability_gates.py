"""Tests for the two reliability gates added 2026-04-25:

    1. Pareto-demotion sample-count gate — refuses to demote to a model
       with fewer than ``_MIN_SAMPLES_FOR_DEMOTION`` internal benchmark
       samples.  Treats "no samples" as "unknown quality", not "low
       quality".

    2. Per-model circuit breaker — tracks connection-shape failures
       per normalized model name and skips OPEN models during
       pareto-demotion.

Context: task #85 on 2026-04-24 pareto-demoted research to
``stepfun/step-3.5-flash`` — zero internal samples, only a scraped
external rank — and that model network-stalled mid-task for 262s.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app import rate_throttle as rt
from app.llm_selector import _pareto_cheaper_alternative


# ══════════════════════════════════════════════════════════════════════
# Fix 1 — pareto sample-count gate
# ══════════════════════════════════════════════════════════════════════

class TestParetoSampleGate:

    def _mk_entry(self, cost: float, tier: str = "budget") -> dict:
        return {"cost_output_per_m": cost, "tier": tier}

    def _get_model_fn(self, table: dict):
        return lambda name: table.get(name)

    def test_candidate_below_sample_floor_is_skipped(self):
        """A model with 0 internal samples must NOT be picked even if
        its external-blended score is higher than the default's."""
        default = "anthropic/claude-opus"
        models = {
            default: self._mk_entry(10.0, "premium"),
            "stepfun/step-3.5-flash": self._mk_entry(0.30),
        }
        # Candidate has score HIGHER than default — normally would win.
        bench_scores = {default: 0.30, "stepfun/step-3.5-flash": 0.39}
        # But it has ZERO samples.
        with patch(
            "app.llm_benchmarks.get_sample_counts",
            return_value={default: 200, "stepfun/step-3.5-flash": 0},
        ):
            alt = _pareto_cheaper_alternative(
                default, models[default], 0.30,
                bench_scores, self._get_model_fn(models),
                task_type="research",
            )
        assert alt is None, (
            "zero-sample model was picked as pareto target — the fix "
            "is regressing; stepfun was chosen exactly this way in the "
            "2026-04-24 outage"
        )

    def test_candidate_at_sample_floor_is_eligible(self):
        default = "anthropic/claude-opus"
        models = {
            default: self._mk_entry(10.0, "premium"),
            "trusted/model": self._mk_entry(0.30),
        }
        bench_scores = {default: 0.30, "trusted/model": 0.39}
        with patch(
            "app.llm_benchmarks.get_sample_counts",
            return_value={default: 200, "trusted/model": 5},
        ):
            alt = _pareto_cheaper_alternative(
                default, models[default], 0.30,
                bench_scores, self._get_model_fn(models),
                task_type="research",
            )
        assert alt == "trusted/model"

    def test_no_task_type_falls_back_to_no_sample_check(self):
        """Callers that don't pass task_type can't sample-gate — old
        behaviour preserved so non-task-specific paths still work."""
        default = "anthropic/claude-opus"
        models = {
            default: self._mk_entry(10.0, "premium"),
            "cheaper/model": self._mk_entry(0.30),
        }
        bench_scores = {default: 0.30, "cheaper/model": 0.39}
        # No task_type → no sample counts available → no gating.
        alt = _pareto_cheaper_alternative(
            default, models[default], 0.30,
            bench_scores, self._get_model_fn(models),
            task_type=None,
        )
        # Without gates, the cheaper model wins (legacy behaviour).
        # Circuit breaker also not triggered.
        assert alt == "cheaper/model"


# ══════════════════════════════════════════════════════════════════════
# Fix 2 — per-model circuit breaker
# ══════════════════════════════════════════════════════════════════════

class TestConnectionShapeDetection:

    @pytest.mark.parametrize("msg", [
        "Failed to connect to OpenAI API: Connection error.",
        "Connection refused",
        "Connection reset by peer",
        "read timed out",
        "Request timed out",
        "Service Unavailable",
        "APIConnectionError: couldn't reach host",
        "openai.APIConnectionError: connection error",
        "remote end closed connection without response",
    ])
    def test_connection_shaped_messages_trip(self, msg):
        assert rt._is_connection_shaped_error(Exception(msg)) is True

    @pytest.mark.parametrize("msg", [
        "Error code: 400 - Provider returned error",
        "Error code: 402 - insufficient credits",
        "Error code: 401 - invalid API key",
        "Tool validation failed: schema mismatch",
        "Rate limit exceeded",  # intentional — 429 is a business signal, not network
        "context length exceeded",
    ])
    def test_non_connection_messages_dont_trip(self, msg):
        assert rt._is_connection_shaped_error(Exception(msg)) is False

    def test_5xx_status_trips(self):
        class E(Exception):
            status_code = 503
        assert rt._is_connection_shaped_error(E("bad gateway")) is True

    def test_4xx_status_does_not_trip(self):
        class E(Exception):
            status_code = 400
        assert rt._is_connection_shaped_error(E("bad request")) is False


class TestModelNameNormalization:

    @pytest.mark.parametrize("raw,expected", [
        ("openrouter/stepfun/step-3.5-flash", "stepfun/step-3.5-flash"),
        ("openai/gpt-4",                      "gpt-4"),
        ("anthropic/claude-opus",             "claude-opus"),
        ("stepfun/step-3.5-flash",            "stepfun/step-3.5-flash"),  # no prefix
        ("litellm/foo",                       "foo"),
        ("",                                  ""),
        ("  openrouter/x  ",                  "x"),
    ])
    def test_normalization(self, raw, expected):
        assert rt._normalize_model_name(raw) == expected


class TestRecordModelReliability:

    def test_success_clears_failures(self):
        from app.circuit_breaker import get_breaker
        key = f"model:test_success_clears"
        b = get_breaker(key)
        b.failure_threshold = 3
        b.cooldown_seconds = 300
        b.record_failure()
        b.record_failure()
        assert b.failure_count == 2
        rt._record_model_reliability("test_success_clears", None, success=True)
        assert b.failure_count == 0

    def test_connection_error_records_failure(self):
        from app.circuit_breaker import get_breaker
        key = f"model:test_connerr"
        b = get_breaker(key)
        b.record_success()  # reset
        rt._record_model_reliability(
            "test_connerr",
            Exception("Failed to connect to OpenAI API: Connection error."),
            success=False,
        )
        assert b.failure_count == 1

    def test_non_connection_error_does_not_count(self):
        from app.circuit_breaker import get_breaker
        key = f"model:test_400err"
        b = get_breaker(key)
        b.record_success()  # reset
        rt._record_model_reliability(
            "test_400err",
            Exception("Error code: 400 - Provider returned error"),
            success=False,
        )
        assert b.failure_count == 0, (
            "400 is a client-side shape issue, not a model reliability "
            "signal — must not count toward the breaker"
        )

    def test_three_connection_errors_open_the_breaker(self):
        from app.circuit_breaker import get_breaker
        key = f"model:test_trip"
        b = get_breaker(key)
        b.record_success()  # reset
        for _ in range(3):
            rt._record_model_reliability(
                "test_trip",
                Exception("Connection error"),
                success=False,
            )
        assert b.is_open()

    def test_openrouter_prefix_shares_breaker_with_bare_model(self):
        """openrouter/stepfun/X and stepfun/X must map to the same key."""
        from app.circuit_breaker import get_breaker
        key = f"model:stepfun/step-share"
        b = get_breaker(key)
        b.record_success()  # reset
        rt._record_model_reliability(
            "openrouter/stepfun/step-share",
            Exception("Connection error"),
            success=False,
        )
        rt._record_model_reliability(
            "stepfun/step-share",
            Exception("Connection error"),
            success=False,
        )
        # Both mapped to model:stepfun/step-share → same breaker.
        assert b.failure_count == 2


# ══════════════════════════════════════════════════════════════════════
# Integration — open breaker blocks pareto-demotion
# ══════════════════════════════════════════════════════════════════════

class TestBreakerBlocksParetoDemotion:

    def test_open_breaker_skips_candidate(self):
        """A model with enough samples should still be skipped by
        pareto-demotion when its per-model breaker is OPEN."""
        from app.circuit_breaker import get_breaker
        key = f"model:flaky/model_test_block"
        b = get_breaker(key)
        b.failure_threshold = 3
        # Force-trip the breaker
        for _ in range(3):
            b.record_failure()
        assert b.is_open()

        default = "anthropic/claude-opus"
        models = {
            default: {"cost_output_per_m": 10.0, "tier": "premium"},
            "flaky/model_test_block": {"cost_output_per_m": 0.30, "tier": "budget"},
        }
        bench_scores = {default: 0.30, "flaky/model_test_block": 0.39}
        # Candidate has plenty of samples — would normally pass the
        # sample gate.  But the breaker is OPEN, so it must be skipped.
        with patch(
            "app.llm_benchmarks.get_sample_counts",
            return_value={default: 200, "flaky/model_test_block": 100},
        ):
            alt = _pareto_cheaper_alternative(
                default, models[default], 0.30,
                bench_scores, lambda n: models.get(n),
                task_type="research",
            )
        assert alt is None, (
            "pareto-demotion picked a model with OPEN breaker — "
            "breaker gate is regressed"
        )
        # Cleanup — reset the breaker
        b.record_success()
