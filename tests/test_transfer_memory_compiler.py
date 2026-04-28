"""Tests for app.transfer_memory.compiler.

Strategy: rather than patching ``app.llm_factory.create_specialist_llm``
(which requires the real module — and its ``pydantic_settings`` /
chromadb dependency chain — to be importable), we inject a stub module
into ``sys.modules`` before the compiler runs. This works identically in
production (overriding the real module for the test) and in minimal CI
environments where the LLM stack is not installed.
"""

import sys
import time
import types
from unittest.mock import MagicMock

import pytest

from app.transfer_memory import queue as q
from app.transfer_memory.compiler import _compile_one, run_compile
from app.transfer_memory.types import TransferEvent, TransferKind


# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_queue_dir(tmp_path, monkeypatch):
    """Redirect queue dir to a temp path for the duration of a test."""
    monkeypatch.setenv("TRANSFER_MEMORY_DIR", str(tmp_path))
    yield tmp_path


@pytest.fixture
def stub_llm_factory(monkeypatch):
    """Inject a fake ``app.llm_factory`` so the compiler's lazy import
    succeeds without pulling the real LLM stack into the test process."""
    fake = types.ModuleType("app.llm_factory")
    fake.create_specialist_llm = MagicMock()
    monkeypatch.setitem(sys.modules, "app.llm_factory", fake)
    yield fake


@pytest.fixture
def stub_llm_mode(monkeypatch):
    """Stub ``set_mode`` / ``get_mode`` so the compiler's force_llm_mode
    block doesn't actually mutate runtime state during tests."""
    monkeypatch.setattr("app.transfer_memory.llm_scope.set_mode", MagicMock())
    monkeypatch.setattr(
        "app.transfer_memory.llm_scope.get_mode",
        MagicMock(return_value="balanced"),
    )


def _good_learner_output() -> str:
    return (
        "# [verification] Verify external numeric claims before finalising\n"
        "## Signal\n"
        "When the response includes a numeric claim sourced from prior memory.\n"
        "## Practice\n"
        "Always validate against the authoritative source or registered belief "
        "store. Treat prior memory as a cue, not evidence. Escalate when no "
        "source is reachable.\n"
        "## Contraindications\n"
        "Do not apply when the claim is inherently subjective or aesthetic.\n"
        "## Evidence\n"
        "Source: grounding_correction event."
    )


# ── run_compile public entry point ──────────────────────────────────────

def test_run_compile_skipped_by_cadence_guard(tmp_queue_dir):
    """A run within 24h of the previous run is a no-op."""
    q.write_last_compile_at(time.time())  # just now
    summary = run_compile()
    assert summary["skipped_cadence"] is True
    assert summary["compiled"] == 0


def test_run_compile_empty_queue_records_timestamp(tmp_queue_dir):
    summary = run_compile()
    assert summary["ran"] is False
    assert summary["queue_depth"] == 0
    # Timestamp written so the cadence guard doesn't spin on empty queues.
    assert q.read_last_compile_at() > 0.0


# ── _compile_one ────────────────────────────────────────────────────────

def test_compile_one_happy_path(tmp_queue_dir, stub_llm_factory):
    """Clean Learner output yields a SkillDraft with shadow scope."""
    fake_llm = MagicMock()
    fake_llm.call.return_value = _good_learner_output()
    stub_llm_factory.create_specialist_llm.return_value = fake_llm

    event = TransferEvent(
        event_id="evt_h_1",
        kind=TransferKind.HEALING,
        source_id="error_x",
        summary="summary",
        payload={
            "error_signature": "abc",
            "error_description": "transient failure",
            "fix_type": "code",
            "fix_applied": "added retry",
            "outcome": "resolved",
            "times_applied": 3,
        },
    )

    outcome = _compile_one(event)

    assert outcome.draft is not None
    assert outcome.draft.source_kind == "healing"
    assert outcome.draft.transfer_scope == "shadow"
    assert outcome.draft.source_domain == "healing"
    assert outcome.draft.id.startswith("draft_xfer_")
    # Sanitiser should allow GLOBAL_META on this clean content; that fact
    # is captured in the rationale string.
    assert "global_meta" in outcome.draft.rationale


def test_compile_one_hard_reject_drops_draft(tmp_queue_dir, stub_llm_factory):
    """An LLM that leaks a credential gets the draft hard-rejected."""
    leaked_output = (
        "# [recovery] Use this token to recover\n"
        "## Signal\nWhen recovery needed.\n"
        "## Practice\nUse sk-ant-leak1234567890abcdefghij in the call.\n"
        "## Contraindications\nNever in production.\n"
        "## Evidence\nFrom run abc."
    )
    fake_llm = MagicMock()
    fake_llm.call.return_value = leaked_output
    stub_llm_factory.create_specialist_llm.return_value = fake_llm

    event = TransferEvent(
        event_id="evt_h_2",
        kind=TransferKind.HEALING,
        source_id="error_y",
        payload={"error_signature": "z"},
    )

    outcome = _compile_one(event)

    assert outcome.draft is None
    assert any("hard_reject" in f[0] for f in outcome.sanitizer_findings)


def test_compile_one_too_short_drops(tmp_queue_dir, stub_llm_factory):
    fake_llm = MagicMock()
    fake_llm.call.return_value = "tiny"
    stub_llm_factory.create_specialist_llm.return_value = fake_llm

    event = TransferEvent(
        event_id="evt_h_3",
        kind=TransferKind.HEALING,
        source_id="error_z",
        payload={},
    )

    outcome = _compile_one(event)

    assert outcome.draft is None
    assert any("too_short" in f[0] for f in outcome.sanitizer_findings)


def test_compile_one_llm_error_returned_as_outcome(tmp_queue_dir, stub_llm_factory):
    fake_llm = MagicMock()
    fake_llm.call.side_effect = RuntimeError("boom")
    stub_llm_factory.create_specialist_llm.return_value = fake_llm

    event = TransferEvent(
        event_id="evt_h_4",
        kind=TransferKind.HEALING,
        source_id="error_w",
        payload={},
    )

    outcome = _compile_one(event)

    assert outcome.draft is None
    assert "llm_call_failed" in outcome.error


def test_compile_one_grounding_correction(tmp_queue_dir, stub_llm_factory):
    """Grounding correction events compile via their own template."""
    fake_llm = MagicMock()
    fake_llm.call.return_value = _good_learner_output()
    stub_llm_factory.create_specialist_llm.return_value = fake_llm

    event = TransferEvent(
        event_id="evt_g_1",
        kind=TransferKind.GROUNDING_CORRECTION,
        source_id="grounding_topic_x",
        payload={
            "topic_hint": "share_price",
            "suggested_source_phrase": "Tallinn Stock Exchange",
            "attributed_date": "2026-04-15",
            "matched_pattern": "actually_X",
        },
    )

    outcome = _compile_one(event)

    assert outcome.draft is not None
    assert outcome.draft.source_kind == "grounding_correction"
    assert outcome.draft.source_domain == "grounding"


# ── End-to-end run_compile ──────────────────────────────────────────────

def test_run_compile_writes_shadow_drafts_end_to_end(
    tmp_queue_dir, stub_llm_factory, stub_llm_mode,
):
    """End-to-end: append events, run compile, verify shadow_drafts.jsonl
    receives the compiled draft."""
    fake_llm = MagicMock()
    fake_llm.call.return_value = _good_learner_output()
    stub_llm_factory.create_specialist_llm.return_value = fake_llm

    q.append_event(
        TransferKind.HEALING,
        "error_e2e",
        summary="end-to-end test",
        payload={
            "error_signature": "e2e",
            "error_description": "test error",
            "fix_type": "config",
            "fix_applied": "updated env",
            "outcome": "resolved",
        },
    )

    summary = run_compile()

    assert summary["ran"] is True
    assert summary["compiled"] >= 1
    assert (tmp_queue_dir / "shadow_drafts.jsonl").exists()


def test_run_compile_failed_events_pushed_to_retry(
    tmp_queue_dir, stub_llm_factory, stub_llm_mode,
):
    """LLM failures route the event to the retry queue."""
    fake_llm = MagicMock()
    fake_llm.call.side_effect = RuntimeError("transient")
    stub_llm_factory.create_specialist_llm.return_value = fake_llm

    q.append_event(
        TransferKind.HEALING,
        "error_retry",
        payload={"error_signature": "r"},
    )

    summary = run_compile()

    assert summary["errors"] >= 1
    retries = q.drain_retries()
    assert len(retries) == 1
    assert retries[0].source_id == "error_retry"
    assert retries[0].attempts == 1
