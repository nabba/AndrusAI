"""Tests for ``adaptive_parallel_timeout`` — the multi-crew dispatch timeout.

Origin: 2026-07-24 incident (reports/ANSWER_QUALITY_DIAGNOSIS_2026-07-24.md).
A "make me a report on Estonia forest health" request was dispatched to
research+writing crews in parallel; ``run_parallel``'s flat 120s default
gave up while both crews were still genuinely working (research finished
at 652s, writing at 345s — both discarded as "orphaned"). This function
replaces the flat constant with a per-crew-class estimate so real
report/research work isn't silently thrown away.
"""
from __future__ import annotations

from app.agents.commander.orchestrator import (
    _MULTI_CREW_TIMEOUT_CEILING,
    adaptive_parallel_timeout,
)


def test_writing_only_uses_the_writing_floor(monkeypatch):
    monkeypatch.setattr(
        "app.conversation_store.get_crew_avg_duration", lambda crew: 10.0,
    )
    assert adaptive_parallel_timeout(["writing"]) == 180


def test_research_plus_writing_uses_the_slower_floor(monkeypatch):
    monkeypatch.setattr(
        "app.conversation_store.get_crew_avg_duration", lambda crew: 10.0,
    )
    # research's 480s floor must win over writing's 180s — run_parallel
    # waits for the SLOWEST dispatched crew, not the fastest.
    assert adaptive_parallel_timeout(["research", "writing"]) == 480


def test_historical_average_can_raise_the_estimate_above_the_floor(monkeypatch):
    # 500s * 1.5 safety margin = 750s, above research's 480s floor but
    # still under the ceiling.
    monkeypatch.setattr(
        "app.conversation_store.get_crew_avg_duration", lambda crew: 500.0,
    )
    assert adaptive_parallel_timeout(["research"]) == 750


def test_estimate_never_exceeds_the_ceiling(monkeypatch):
    monkeypatch.setattr(
        "app.conversation_store.get_crew_avg_duration", lambda crew: 10_000.0,
    )
    assert adaptive_parallel_timeout(["deep_research"]) == _MULTI_CREW_TIMEOUT_CEILING


def test_unknown_crew_class_uses_the_default_floor(monkeypatch):
    monkeypatch.setattr(
        "app.conversation_store.get_crew_avg_duration", lambda crew: 10.0,
    )
    assert adaptive_parallel_timeout(["some_new_crew"]) == 300


def test_survives_conversation_store_import_failure(monkeypatch):
    """If historical lookup is unavailable, fall back to the floor table
    instead of raising — the timeout must always resolve to a number."""
    import builtins

    real_import = builtins.__import__

    def _blocked_import(name, *args, **kwargs):
        if name == "app.conversation_store":
            raise ImportError("simulated unavailability")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)
    assert adaptive_parallel_timeout(["research"]) == 480
