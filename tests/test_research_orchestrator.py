"""Tests for app.tools.research_orchestrator — structured matrix research."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.observability.task_progress import (
    current_task_id,
    output_progress_count,
    reset_task,
)
from app.tools import research_orchestrator as ro


# ══════════════════════════════════════════════════════════════════════
# Circuit breaker
# ══════════════════════════════════════════════════════════════════════

class TestDomainBreaker:

    def test_trips_after_max_consecutive_failures(self):
        b = ro._DomainBreaker(max_consecutive_failures=3)
        for _ in range(3):
            b.record_failure("linkedin.com", "403")
        assert b.is_tripped("linkedin.com")

    def test_does_not_trip_below_threshold(self):
        b = ro._DomainBreaker(max_consecutive_failures=3)
        b.record_failure("linkedin.com", "403")
        b.record_failure("linkedin.com", "403")
        assert not b.is_tripped("linkedin.com")

    def test_success_resets_consecutive_counter(self):
        b = ro._DomainBreaker(max_consecutive_failures=3)
        b.record_failure("linkedin.com", "403")
        b.record_failure("linkedin.com", "403")
        b.record_success("linkedin.com")
        b.record_failure("linkedin.com", "403")
        b.record_failure("linkedin.com", "403")
        # Reset cleared the 2 prior failures; only 2 more ≠ 3 strike trip
        assert not b.is_tripped("linkedin.com")

    def test_empty_domain_is_noop(self):
        b = ro._DomainBreaker(max_consecutive_failures=1)
        b.record_failure("", "err")
        # Empty domain must not trip anything
        assert b.tripped == {}


# ══════════════════════════════════════════════════════════════════════
# Known-hard short-circuit — THIS is the fix that kills the LinkedIn
# retry-loop failure mode
# ══════════════════════════════════════════════════════════════════════

class TestKnownHardShortCircuit:

    def test_known_hard_field_returns_na_without_calling_adapters(self):
        """A field tagged known_hard=True must NOT invoke any adapter."""
        adapter_called = {"count": 0}

        def spy_adapter(subj, fs):
            adapter_called["count"] += 1
            return "SHOULD NOT BE CALLED"

        with patch.dict(ro._ADAPTERS, {"search": spy_adapter}, clear=False):
            value, source = ro._research_field(
                subject={"name": "X"},
                field_spec={
                    "key": "linkedin_personal",
                    "known_hard": True,
                    "reason": "LinkedIn blocks scraping",
                },
                source_priority=["search"],
                breaker=ro._DomainBreaker(),
                per_call_timeout=5.0,
            )
        assert value == "N/A"
        assert "known-hard" in source
        assert "LinkedIn blocks" in source
        assert adapter_called["count"] == 0


# ══════════════════════════════════════════════════════════════════════
# Adapter chain ordering + short-circuit on first hit
# ══════════════════════════════════════════════════════════════════════

class TestAdapterChain:

    def test_first_success_short_circuits(self):
        calls = []

        def first(subj, fs):
            calls.append("first")
            return "HIT"

        def second(subj, fs):
            calls.append("second")
            return "ALSO_HIT"

        with patch.dict(ro._ADAPTERS, {"a": first, "b": second}, clear=True):
            value, source = ro._research_field(
                subject={"name": "X"},
                field_spec={"key": "k"},
                source_priority=["a", "b"],
                breaker=ro._DomainBreaker(),
                per_call_timeout=5.0,
            )
        assert value == "HIT"
        assert source == "a"
        assert calls == ["first"]  # 'second' never called

    def test_exhausted_when_all_return_none(self):
        def nil(subj, fs):
            return None

        with patch.dict(ro._ADAPTERS, {"a": nil, "b": nil}, clear=True):
            value, source = ro._research_field(
                subject={"name": "X"},
                field_spec={"key": "k"},
                source_priority=["a", "b"],
                breaker=ro._DomainBreaker(),
                per_call_timeout=5.0,
            )
        assert value == "N/A"
        assert source == "no source hit"

    def test_adapter_raising_is_caught_and_next_is_tried(self):
        def boom(subj, fs):
            raise RuntimeError("network-dead")

        def ok(subj, fs):
            return "FALLBACK"

        with patch.dict(ro._ADAPTERS, {"first": boom, "second": ok}, clear=True):
            value, source = ro._research_field(
                subject={"name": "X"},
                field_spec={"key": "k"},
                source_priority=["first", "second"],
                breaker=ro._DomainBreaker(),
                per_call_timeout=5.0,
            )
        assert value == "FALLBACK"
        assert source == "second"

    def test_tripped_domain_is_skipped(self):
        called = {"n": 0}

        def would_fail(subj, fs):
            called["n"] += 1
            return None

        breaker = ro._DomainBreaker()
        breaker.tripped["example.com"] = "pre-tripped"

        with patch.dict(ro._ADAPTERS, {"search": would_fail}, clear=True):
            value, source = ro._research_field(
                subject={"name": "X", "homepage": "https://example.com"},
                field_spec={"key": "k"},
                source_priority=["search"],
                breaker=breaker,
                per_call_timeout=5.0,
            )
        assert value == "N/A"
        # Tripped: adapter must NOT be called
        assert called["n"] == 0


# ══════════════════════════════════════════════════════════════════════
# Per-call timeout — the core protection against hung fetches
# ══════════════════════════════════════════════════════════════════════

class TestPerCallTimeout:

    def test_adapter_timeout_does_not_block_row(self):
        import time as _t

        def slow(subj, fs):
            _t.sleep(3.0)
            return "LATE"

        def fast(subj, fs):
            return "FAST"

        with patch.dict(ro._ADAPTERS, {"slow": slow, "fast": fast}, clear=True):
            t0 = _t.monotonic()
            value, source = ro._research_field(
                subject={"name": "X"},
                field_spec={"key": "k"},
                source_priority=["slow", "fast"],
                breaker=ro._DomainBreaker(),
                per_call_timeout=0.3,
            )
            elapsed = _t.monotonic() - t0
        assert value == "FAST"
        assert source == "fast"
        # Enforced timeout: we must not have waited the full 3s
        assert elapsed < 2.0, f"per-call timeout failed to enforce: {elapsed:.1f}s"


# ══════════════════════════════════════════════════════════════════════
# End-to-end orchestration with stubbed adapters
# ══════════════════════════════════════════════════════════════════════

class TestOrchestrateResearch:

    def test_happy_path_produces_row_per_subject(self):
        def stub(subj, fs):
            return f"{fs['key']}-of-{subj['name']}"

        spec = {
            "title": "test",
            "subjects": [
                {"id": "s1", "name": "Alpha", "market": "Estonia"},
                {"id": "s2", "name": "Beta",  "market": "Latvia"},
            ],
            "fields": [{"key": "url"}, {"key": "email"}],
            "max_subjects_in_parallel": 2,
            "budget_seconds": 30,
            "per_call_timeout_seconds": 5,
            "source_priority": ["stub"],
        }

        with patch.dict(ro._ADAPTERS, {"stub": stub}, clear=True):
            result = ro.orchestrate_research(spec)

        assert len(result["rows"]) == 2
        names = {r["name"] for r in result["rows"]}
        assert names == {"Alpha", "Beta"}
        # Every cell filled
        for row in result["rows"]:
            for key in ("url", "email"):
                cell = row["values"][key]
                assert cell["value"] == f"{key}-of-{row['name']}"
                assert cell["source"] == "stub"

    def test_budget_exhaustion_skips_remaining_subjects(self):
        import time as _t

        def sleepy(subj, fs):
            _t.sleep(0.5)
            return "X"

        spec = {
            "subjects": [
                {"id": f"s{i}", "name": f"Subject{i}", "market": "EE"}
                for i in range(5)
            ],
            "fields": [{"key": "k"}],
            "max_subjects_in_parallel": 1,  # serial
            "budget_seconds": 0.6,           # tight budget
            "per_call_timeout_seconds": 2.0,
            "source_priority": ["sleepy"],
        }

        with patch.dict(ro._ADAPTERS, {"sleepy": sleepy}, clear=True):
            result = ro.orchestrate_research(spec)

        # Some rows complete, but not all 5 — and nothing silently drops
        total = len(result["rows"]) + len(result["skipped"])
        assert total == 5
        assert len(result["skipped"]) > 0
        for skip in result["skipped"]:
            assert "budget_exhausted" in skip["reason"]

    def test_known_hard_fields_skipped_with_na(self):
        def stub(subj, fs):
            return "REAL_VALUE"

        spec = {
            "subjects": [{"id": "s1", "name": "Alpha", "market": "EE"}],
            "fields": [
                {"key": "url"},
                {
                    "key": "linkedin_personal",
                    "known_hard": True,
                    "reason": "blocked",
                },
            ],
            "source_priority": ["stub"],
            "max_subjects_in_parallel": 1,
        }

        with patch.dict(ro._ADAPTERS, {"stub": stub}, clear=True):
            result = ro.orchestrate_research(spec)

        row = result["rows"][0]
        assert row["values"]["url"]["value"] == "REAL_VALUE"
        assert row["values"]["linkedin_personal"]["value"] == "N/A"
        assert "known-hard" in row["values"]["linkedin_personal"]["source"]

    def test_records_output_progress_per_completed_row(self):
        """This is the STALL-DETECTOR-FIX test: every completed row must
        record progress so the handle_task stall detector sees activity."""
        def stub(subj, fs):
            return "X"

        tid = "test-tid-orchestrator-progress"
        reset_task(tid)

        spec = {
            "subjects": [
                {"id": "s1", "name": "A", "market": "EE"},
                {"id": "s2", "name": "B", "market": "LV"},
                {"id": "s3", "name": "C", "market": "LT"},
            ],
            "fields": [{"key": "k"}],
            "source_priority": ["stub"],
            "max_subjects_in_parallel": 3,
        }

        token = current_task_id.set(tid)
        try:
            with patch.dict(ro._ADAPTERS, {"stub": stub}, clear=True):
                ro.orchestrate_research(spec)
        finally:
            current_task_id.reset(token)

        # 3 subjects × 1 progress event each = 3 events minimum.
        # (The scoping-note path doesn't fire because no field is known_hard.)
        assert output_progress_count(tid) >= 3

    def test_missing_subjects_or_fields_returns_error(self):
        assert "error" in ro.orchestrate_research({"subjects": [], "fields": []})
        assert "error" in ro.orchestrate_research({"fields": [{"key": "k"}]})
        assert "error" in ro.orchestrate_research({"subjects": [{"name": "x"}]})

    def test_one_subject_error_does_not_drop_others(self):
        def flaky(subj, fs):
            if subj["name"] == "Bad":
                raise RuntimeError("kaboom")
            return "OK"

        spec = {
            "subjects": [
                {"id": "s1", "name": "Good",  "market": "EE"},
                {"id": "s2", "name": "Bad",   "market": "LV"},
                {"id": "s3", "name": "Good2", "market": "LT"},
            ],
            "fields": [{"key": "k"}],
            "source_priority": ["flaky"],
            "max_subjects_in_parallel": 3,
        }

        with patch.dict(ro._ADAPTERS, {"flaky": flaky}, clear=True):
            result = ro.orchestrate_research(spec)

        # Bad raises inside _research_field but is caught; its row still
        # materialises with N/A.  So all 3 rows land.  (This actually
        # PROVES the row-independence invariant — one subject's failure
        # does not take down any other.)
        names_ok = {r["name"] for r in result["rows"]}
        assert names_ok == {"Good", "Bad", "Good2"}
        # The Bad subject's cell is N/A with an exhausted-source source
        bad_row = next(r for r in result["rows"] if r["name"] == "Bad")
        assert bad_row["values"]["k"]["value"] == "N/A"


# ══════════════════════════════════════════════════════════════════════
# CrewAI tool wrapper (JSON in → JSON out)
# ══════════════════════════════════════════════════════════════════════

class TestToolWrapper:

    def test_tool_accepts_json_string(self):
        def stub(subj, fs):
            return "X"

        spec = {
            "subjects": [{"id": "s1", "name": "A", "market": "EE"}],
            "fields": [{"key": "k"}],
            "source_priority": ["stub"],
            "max_subjects_in_parallel": 1,
        }

        with patch.dict(ro._ADAPTERS, {"stub": stub}, clear=True):
            out = ro.research_orchestrator.run(spec_json=json.dumps(spec))

        parsed = json.loads(out)
        assert parsed["rows"][0]["name"] == "A"

    def test_tool_returns_error_on_bad_json(self):
        out = ro.research_orchestrator.run(spec_json="not-valid-json")
        parsed = json.loads(out)
        assert "error" in parsed

    def test_tool_returns_error_on_non_object(self):
        out = ro.research_orchestrator.run(spec_json='["a","b","c"]')
        parsed = json.loads(out)
        assert "error" in parsed


# ══════════════════════════════════════════════════════════════════════
# register_adapter — extension point for market-specific sources
# ══════════════════════════════════════════════════════════════════════

class TestRegisterAdapter:

    def test_register_adapter_adds_to_registry(self):
        sentinel = object()

        def my_adapter(subj, fs):  # noqa: ARG001
            return "HIT"

        try:
            ro.register_adapter("my_custom_source", my_adapter)
            assert ro._ADAPTERS["my_custom_source"] is my_adapter
        finally:
            ro._ADAPTERS.pop("my_custom_source", None)
