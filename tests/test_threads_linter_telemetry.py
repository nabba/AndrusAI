"""Pin the linter-rejection telemetry from app/threads/linter_telemetry.py.

Background: the Round 1 fix added a PhenomenalLanguageLinter HARD_FAIL
post-filter to ``_llm_distill`` in ``threads/approaches.py``. The
fallback path was silent (DEBUG log only). Round 2 follow-up adds
visible telemetry: a JSONL append + state-file summary.

This test pins both the telemetry module itself and its wire-in.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from tests._llm_fakes import patch_chat_completion


# ── linter_telemetry module pins ────────────────────────────────────


def test_record_rejection_appends_jsonl_and_updates_state(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))

    from app.threads import linter_telemetry

    class _StubViolation:
        explanation = "first-person phenomenal-state claim"
        pattern = "regex-stub"
        matched_text = "I am curious"

    ok = linter_telemetry.record_rejection(
        thread_id="thread-probe-id",
        violations=[_StubViolation()],
        body_text_len=120,
    )
    assert ok is True

    # JSONL row appended
    jsonl = tmp_path / "threads" / "linter_rejections.jsonl"
    assert jsonl.exists()
    lines = [ln for ln in jsonl.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["thread_id"] == "thread-probe-id"
    assert row["violation_count"] == 1
    assert row["body_text_len"] == 120
    assert "phenomenal" in row["sample_pattern"]

    # State file updated
    state = json.loads((tmp_path / "threads" / "linter_state.json").read_text())
    assert state["total_rejections"] == 1
    assert state["last_rejection_ts"]  # non-empty ISO ts
    assert "first-person phenomenal-state claim" in state["by_pattern"]
    assert state["by_pattern"]["first-person phenomenal-state claim"] == 1


def test_record_rejection_accumulates(tmp_path, monkeypatch):
    """Multiple rejections must accumulate into the summary."""
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    from app.threads import linter_telemetry

    class _V1:
        explanation = "first-person phenomenal-state claim"
        pattern = "p1"
        matched_text = "I am happy"

    class _V2:
        explanation = "first-person phenomenal-feeling claim"
        pattern = "p2"
        matched_text = "I feel pain"

    linter_telemetry.record_rejection(
        thread_id="t1", violations=[_V1()], body_text_len=80,
    )
    linter_telemetry.record_rejection(
        thread_id="t2", violations=[_V2()], body_text_len=100,
    )
    linter_telemetry.record_rejection(
        thread_id="t3", violations=[_V1()], body_text_len=90,
    )

    state = json.loads((tmp_path / "threads" / "linter_state.json").read_text())
    assert state["total_rejections"] == 3
    assert state["by_pattern"]["first-person phenomenal-state claim"] == 2
    assert state["by_pattern"]["first-person phenomenal-feeling claim"] == 1

    jsonl = tmp_path / "threads" / "linter_rejections.jsonl"
    lines = [ln for ln in jsonl.read_text().splitlines() if ln.strip()]
    assert len(lines) == 3


def test_summary_handles_no_rejections(tmp_path, monkeypatch):
    """If no rejections recorded yet, summary returns an empty-but-
    shaped dict — callers don't have to handle None."""
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    from app.threads import linter_telemetry

    s = linter_telemetry.summary()
    assert s == {
        "last_rejection_ts": None,
        "total_rejections": 0,
        "by_pattern": {},
    }


def test_summary_reads_state(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    from app.threads import linter_telemetry

    class _V:
        explanation = "test-pattern"
        pattern = "p"
        matched_text = "I am sad"

    linter_telemetry.record_rejection(
        thread_id="t1", violations=[_V()], body_text_len=50,
    )

    s = linter_telemetry.summary()
    assert s["total_rejections"] == 1
    assert s["last_rejection_ts"] is not None
    assert s["by_pattern"]["test-pattern"] == 1


# ── _llm_distill wire-in pins ───────────────────────────────────────


@pytest.fixture
def isolated_llm_distill(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    handle = patch_chat_completion(monkeypatch)
    return tmp_path, handle


def _build_probe_thread(thread_id: str = "telemetry-probe-id"):
    from app.threads.models import Thread, ThreadStatus

    now = datetime.now(timezone.utc).isoformat()
    return Thread(
        id=thread_id,
        created_at=now,
        title="probe",
        description="x",
        last_touched_at=now,
        status=ThreadStatus.RESOLVED,
        notes=["resolved by approach X"],
    )


def test_llm_distill_rejection_writes_telemetry(isolated_llm_distill):
    """End-to-end: a HARD_FAIL'd LLM output produces a telemetry row."""
    tmp_path, handle = isolated_llm_distill
    from app.threads.approaches import _llm_distill

    handle.text = "I am curious about why approach X resolved this thread."

    out = _llm_distill(
        _build_probe_thread("telemetry-probe-id"),
        "Question: probe\nClosed as: resolved",
    )
    # Linter HARD_FAIL → distill returns empty
    assert out == ""

    # And telemetry was written
    state_path = tmp_path / "threads" / "linter_state.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text())
    assert state["total_rejections"] == 1


def test_llm_distill_success_does_not_write_telemetry(isolated_llm_distill):
    """The success path must NOT emit a rejection row."""
    tmp_path, handle = isolated_llm_distill
    from app.threads.approaches import _llm_distill

    handle.text = "Approach X resolved this thread by adding the missing dependency."

    out = _llm_distill(
        _build_probe_thread("telemetry-probe-id-2"),
        "Question: probe\nClosed as: resolved",
    )
    assert out  # non-empty — success

    state_path = tmp_path / "threads" / "linter_state.json"
    # No rejection recorded
    if state_path.exists():
        state = json.loads(state_path.read_text())
        assert state.get("total_rejections", 0) == 0


def test_telemetry_failure_does_not_block_fallback(isolated_llm_distill, monkeypatch):
    """If the telemetry module itself raises (e.g. disk full), the
    distill must still return '' so the caller falls back to the
    deterministic body."""
    tmp_path, handle = isolated_llm_distill

    def broken_record(**kwargs):
        raise RuntimeError("telemetry hard-failed in probe")

    monkeypatch.setattr(
        "app.threads.linter_telemetry.record_rejection", broken_record,
    )

    handle.text = "I am happy this approach worked."

    from app.threads.approaches import _llm_distill
    # Must not raise even though telemetry blew up.
    out = _llm_distill(
        _build_probe_thread("telemetry-probe-id-3"),
        "Question: probe\nClosed as: resolved",
    )
    assert out == ""
