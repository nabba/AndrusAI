"""Tests for the Stage A structural producer.

Pins the contract:
  * Off by default → no claims emitted, no DB hit
  * On → one claim per passage, ASSUMED status, INTERNAL register
  * Dedup: same doc_id within a task → one claim
  * Failure-isolated: producer faults don't propagate
  * Score normalization: rerank > blended > score, clamped [0,1]
"""
from __future__ import annotations

import pytest

# Skip cleanly on host where pydantic/pydantic_settings aren't installed.
pytest.importorskip("pydantic")
pytest.importorskip("pydantic_settings")

from app.epistemic.ledger import Register, VerificationStatus  # noqa: E402
from app.epistemic import retrieval_producer as rp  # noqa: E402


@pytest.fixture
def clean_dedup():
    rp.reset_dedup_cache()
    yield
    rp.reset_dedup_cache()


@pytest.fixture
def emits_capture(monkeypatch, clean_dedup):
    """Capture every ledger.emit call without hitting Postgres."""
    captured: list = []

    class FakeLedger:
        def __init__(self, *, task_id):
            self.task_id = task_id

        def emit(self, claim):
            captured.append(claim)
            return claim

    monkeypatch.setattr(rp, "Ledger", FakeLedger)
    return captured


def test_off_by_default_emits_nothing(emits_capture, monkeypatch):
    """Master switch off → no claims emitted regardless of input shape."""
    monkeypatch.setattr(rp, "_enabled", lambda: False)
    n = rp.emit_retrieval_claims(
        task_id="t1", kb_name="episteme", query="x",
        passages=[{"text": "abc", "rerank_score": 0.9}],
    )
    assert n == 0
    assert emits_capture == []


def test_empty_task_id_emits_nothing(emits_capture, monkeypatch):
    """Empty task_id is a hard skip — the claim ledger keys on task_id."""
    monkeypatch.setattr(rp, "_enabled", lambda: True)
    n = rp.emit_retrieval_claims(
        task_id="", kb_name="episteme", query="x",
        passages=[{"text": "abc", "rerank_score": 0.9}],
    )
    assert n == 0


def test_passage_to_claim_shape(emits_capture, monkeypatch):
    """Each passage → one Claim with the documented shape."""
    monkeypatch.setattr(rp, "_enabled", lambda: True)
    n = rp.emit_retrieval_claims(
        task_id="task-abc",
        kb_name="episteme",
        query="what is X",
        passages=[{
            "text": "X is defined as the answer to the question of meaning.",
            "metadata": {"title": "philosophy.pdf", "doc_id": "phil_42"},
            "rerank_score": 0.82,
        }],
    )
    assert n == 1
    [claim] = emits_capture
    assert claim.task_id == "task-abc"
    assert claim.agent_role == "retrieval"
    assert claim.status == VerificationStatus.ASSUMED
    assert claim.register == Register.INTERNAL
    assert claim.load_bearing is False
    assert "retrieval" in claim.tags
    assert "episteme" in claim.tags
    assert any(t.startswith("q:") for t in claim.tags)
    assert len(claim.evidence) == 1
    ev = claim.evidence[0]
    assert ev.kind == "memory_lookup"
    assert ev.source_ref.startswith("episteme:")
    assert 0.0 <= ev.confidence <= 1.0
    assert ev.confidence == pytest.approx(0.82)


def test_dedup_within_task(emits_capture, monkeypatch):
    """Same doc_id within the same task_id → one claim."""
    monkeypatch.setattr(rp, "_enabled", lambda: True)
    passage = {
        "text": "stable text",
        "metadata": {"doc_id": "same"},
        "rerank_score": 0.7,
    }
    n1 = rp.emit_retrieval_claims(
        task_id="t1", kb_name="episteme", query="q", passages=[passage],
    )
    n2 = rp.emit_retrieval_claims(
        task_id="t1", kb_name="episteme", query="q", passages=[passage],
    )
    assert n1 == 1
    assert n2 == 0
    assert len(emits_capture) == 1


def test_dedup_does_not_cross_tasks(emits_capture, monkeypatch):
    """Different task_id → both emit, even with same doc_id."""
    monkeypatch.setattr(rp, "_enabled", lambda: True)
    passage = {
        "text": "stable text",
        "metadata": {"doc_id": "same"},
        "rerank_score": 0.7,
    }
    rp.emit_retrieval_claims(
        task_id="t1", kb_name="episteme", query="q", passages=[passage],
    )
    rp.emit_retrieval_claims(
        task_id="t2", kb_name="episteme", query="q", passages=[passage],
    )
    assert len(emits_capture) == 2


def test_score_normalization_clamps_and_falls_back(emits_capture, monkeypatch):
    """rerank > blended > score; values outside [0,1] clamp; missing → 0.0."""
    monkeypatch.setattr(rp, "_enabled", lambda: True)
    rp.emit_retrieval_claims(
        task_id="t1", kb_name="kb", query="q",
        passages=[
            {"text": "a", "metadata": {"doc_id": "1"}, "score": 1.5},      # clamp
            {"text": "b", "metadata": {"doc_id": "2"}, "blended_score": -0.3},  # clamp
            {"text": "c", "metadata": {"doc_id": "3"}, "rerank_score": 0.5, "score": 0.1},
            {"text": "d", "metadata": {"doc_id": "4"}},   # no score
        ],
    )
    confidences = [c.evidence[0].confidence for c in emits_capture]
    assert confidences == [1.0, 0.0, 0.5, 0.0]


def test_failure_in_one_passage_does_not_break_batch(emits_capture, monkeypatch):
    """One pathological passage → the rest still emit."""
    monkeypatch.setattr(rp, "_enabled", lambda: True)
    rp.emit_retrieval_claims(
        task_id="t1", kb_name="kb", query="q",
        passages=[
            {"text": "good"},
            "not a dict at all",                         # malformed
            {"text": ""},                                 # empty text — skip
            {"text": "also good", "metadata": {"doc_id": "x"}},
        ],
    )
    assert len(emits_capture) == 2


def test_missing_text_skips(emits_capture, monkeypatch):
    """Passages without text → skipped, no claim."""
    monkeypatch.setattr(rp, "_enabled", lambda: True)
    n = rp.emit_retrieval_claims(
        task_id="t1", kb_name="kb", query="q",
        passages=[{"text": ""}, {"metadata": {"doc_id": "x"}}],
    )
    assert n == 0


def test_doc_id_falls_back_to_content_hash(emits_capture, monkeypatch):
    """No metadata id → stable hash-based doc_id."""
    monkeypatch.setattr(rp, "_enabled", lambda: True)
    rp.emit_retrieval_claims(
        task_id="t1", kb_name="kb", query="q",
        passages=[{"text": "hashable content"}],
    )
    [claim] = emits_capture
    assert claim.evidence[0].source_ref.startswith("kb:hash:")


def test_query_included_in_tags_truncated(emits_capture, monkeypatch):
    """Long query → tag truncated to 60 chars under the q: prefix."""
    monkeypatch.setattr(rp, "_enabled", lambda: True)
    rp.emit_retrieval_claims(
        task_id="t1", kb_name="kb", query="x" * 200,
        passages=[{"text": "p", "metadata": {"doc_id": "1"}}],
    )
    [claim] = emits_capture
    q_tag = [t for t in claim.tags if t.startswith("q:")][0]
    assert len(q_tag) <= 62  # "q:" + 60 chars
