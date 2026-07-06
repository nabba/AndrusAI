"""Tests for ``_merge_crew_results`` — the multi-crew aggregation step.

Origin: 2026-07-05 incident (trace 8d75862285ab). A "write a comprehensive
20-page paper with credible sources" request was routed to both the
``research`` and ``writing`` crews in parallel; an OpenRouter credit outage
made both crews time out. The old aggregation code did
``combined = parts[0] if parts else ""`` — an all-failed dispatch produced
an empty string that survived ``vet_response``'s short-circuit unchanged
and reached ``send_durable()`` as an empty Signal message, which Signal
silently rejected. The user received nothing at all, with no error
surfaced anywhere.

These tests pin the fix: an all-failed dispatch must produce a non-empty,
explanatory response, and a partial failure must be visibly noted rather
than silently dropped.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.agents.commander.orchestrator import _merge_crew_results


@dataclass
class _FakeParallelResult:
    """Mirrors app.crews.parallel_runner.ParallelResult's public shape."""
    label: str
    success: bool
    result: str | None = None
    error: str | None = None


def test_single_success_passes_through_unchanged():
    # max_diff floors at 5 by design (see _merge_crew_results), so use a
    # difficulty above that floor to actually exercise the max() logic.
    results = [_FakeParallelResult(label="writing", success=True, result="the answer")]
    combined, max_diff, note = _merge_crew_results(results, {"writing": 7}, "task")

    assert combined == "the answer"
    assert max_diff == 7
    assert note == ""


def test_all_crews_failed_produces_explanatory_message_not_empty_string():
    """The core regression pin: never ship an empty combined result."""
    results = [
        _FakeParallelResult(label="writing", success=False, error="Timed out"),
        _FakeParallelResult(label="research", success=False, error="Timed out"),
    ]
    combined, max_diff, note = _merge_crew_results(results, {}, "write a paper")

    assert combined != ""
    assert combined.strip() != ""
    assert len(combined) >= 10  # would have tripped vet_response's short-circuit
    assert "writing" in combined
    assert "research" in combined
    assert "Timed out" in combined
    assert note == ""  # no partial note when nothing succeeded


def test_all_crews_failed_with_no_error_message_still_produces_output():
    """Defensive: even a falsy/missing error string must not crash or
    produce an empty result (``r.error or "unknown error"`` guard)."""
    results = [_FakeParallelResult(label="research", success=False, error=None)]
    combined, _max_diff, _note = _merge_crew_results(results, {}, "task")

    assert combined != ""
    assert "unknown error" in combined


def test_partial_failure_appends_note_without_touching_combined():
    results = [
        _FakeParallelResult(label="writing", success=True, result="partial answer"),
        _FakeParallelResult(label="research", success=False, error="Timed out"),
    ]
    combined, _max_diff, note = _merge_crew_results(results, {"writing": 6}, "task")

    assert combined == "partial answer"
    assert note != ""
    assert "research" in note
    assert "1 of 2" in note


def test_no_failures_no_note():
    results = [
        _FakeParallelResult(label="writing", success=True, result="ok"),
    ]
    _combined, _max_diff, note = _merge_crew_results(results, {}, "task")
    assert note == ""


def test_multi_success_synthesizes_via_llm(monkeypatch):
    calls = []

    class _FakeLLM:
        def call(self, prompt):
            calls.append(prompt)
            return "synthesized answer with enough length to pass the >=30 char check"

    def _fake_create_specialist_llm(**kwargs):
        return _FakeLLM()

    monkeypatch.setattr(
        "app.llm_factory.create_specialist_llm", _fake_create_specialist_llm,
    )

    results = [
        _FakeParallelResult(label="research", success=True, result="research findings"),
        _FakeParallelResult(label="writing", success=True, result="draft prose"),
    ]
    combined, max_diff, note = _merge_crew_results(
        results, {"research": 6, "writing": 4}, "write a paper",
    )

    assert combined == "synthesized answer with enough length to pass the >=30 char check"
    assert max_diff == 6
    assert note == ""
    assert len(calls) == 1


def test_multi_success_falls_back_to_raw_concat_on_synthesis_failure(monkeypatch):
    def _raising_create_specialist_llm(**kwargs):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr(
        "app.llm_factory.create_specialist_llm", _raising_create_specialist_llm,
    )

    results = [
        _FakeParallelResult(label="research", success=True, result="research findings"),
        _FakeParallelResult(label="writing", success=True, result="draft prose"),
    ]
    combined, _max_diff, _note = _merge_crew_results(results, {}, "write a paper")

    assert "research findings" in combined
    assert "draft prose" in combined


def test_empty_results_list_produces_explanatory_message():
    combined, _max_diff, note = _merge_crew_results([], {}, "task")
    assert combined != ""
    assert note == ""


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
