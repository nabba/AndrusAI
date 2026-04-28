"""Tests for app.transfer_memory.attribution."""

import json
import time
from unittest.mock import MagicMock

import pytest

from app.transfer_memory import attribution as A
from app.transfer_memory.types import NegativeTransferTag


@pytest.fixture
def tmp_queue_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSFER_MEMORY_DIR", str(tmp_path))
    yield tmp_path


def _record(id_, *, source_domain="coding", transfer_scope="global_meta",
            abstraction_score=0.5, content="Some procedural insight."):
    rec = MagicMock()
    rec.id = id_
    rec.content_markdown = content
    rec.provenance = {
        "source_domain": source_domain,
        "transfer_scope": transfer_scope,
        "abstraction_score": abstraction_score,
    }
    rec.status = "active"
    return rec


# ── Heuristic classifier ─────────────────────────────────────────────

def test_classify_domain_mismatched_anchor():
    rec = _record("r1", source_domain="coding")
    tag = A._classify(rec, target_domain="grounding")
    assert tag == NegativeTransferTag.DOMAIN_MISMATCHED_ANCHOR


def test_classify_over_abstraction_when_high_score_and_short():
    rec = _record(
        "r2", source_domain="coding", abstraction_score=0.9,
        content="When in doubt, verify.",  # very short
    )
    tag = A._classify(rec, target_domain="coding")
    assert tag == NegativeTransferTag.OVER_ABSTRACTION


def test_classify_falls_back_to_misapplied_best_practice():
    rec = _record(
        "r3", source_domain="coding", abstraction_score=0.5,
        content=(
            "Always verify external claims through authoritative sources "
            "before finalising. Treat retrieved memory as a cue rather "
            "than evidence. Escalate when no source is reachable. " * 3
        ),
    )
    tag = A._classify(rec, target_domain="coding")
    assert tag == NegativeTransferTag.MISAPPLIED_BEST_PRACTICE


def test_classify_project_scope_leakage():
    """Content that contains a project noun but record was tagged
    global_meta — sanitiser would have caught this if rerun."""
    rec = _record(
        "r4", source_domain="coding", transfer_scope="global_meta",
        content=(
            "When integrating with PLG ticketing systems, verify the "
            "pricing data against the belief store before responding. " * 3
        ),
    )
    tag = A._classify(rec, target_domain="coding")
    assert tag == NegativeTransferTag.PROJECT_SCOPE_LEAKAGE


# ── Audit log + counts ───────────────────────────────────────────────

def test_count_same_tag_aggregates(tmp_queue_dir):
    A._append_neg_transfer_log({
        "skill_record_id": "skill_a",
        "tag": NegativeTransferTag.DOMAIN_MISMATCHED_ANCHOR.value,
        "ts": time.time(),
    })
    A._append_neg_transfer_log({
        "skill_record_id": "skill_a",
        "tag": NegativeTransferTag.DOMAIN_MISMATCHED_ANCHOR.value,
        "ts": time.time(),
    })
    A._append_neg_transfer_log({
        "skill_record_id": "skill_a",
        "tag": NegativeTransferTag.OVER_ABSTRACTION.value,
        "ts": time.time(),
    })
    A._append_neg_transfer_log({
        "skill_record_id": "skill_b",
        "tag": NegativeTransferTag.DOMAIN_MISMATCHED_ANCHOR.value,
        "ts": time.time(),
    })

    assert A._count_same_tag(
        "skill_a", NegativeTransferTag.DOMAIN_MISMATCHED_ANCHOR,
    ) == 2
    assert A._count_same_tag(
        "skill_a", NegativeTransferTag.OVER_ABSTRACTION,
    ) == 1
    assert A._count_same_tag(
        "skill_b", NegativeTransferTag.OVER_ABSTRACTION,
    ) == 0


# ── Blacklist ────────────────────────────────────────────────────────

def test_extend_blacklist_dedups(tmp_queue_dir):
    A._extend_blacklist({"skill_x", "skill_y"})
    A._extend_blacklist({"skill_y", "skill_z"})
    bl = A._read_blacklist()
    assert bl == {"skill_x", "skill_y", "skill_z"}


def test_is_blacklisted(tmp_queue_dir):
    assert not A.is_blacklisted("skill_a")
    A._extend_blacklist({"skill_a"})
    assert A.is_blacklisted("skill_a")
    assert not A.is_blacklisted("skill_b")
    assert not A.is_blacklisted("")


# ── Cursor ───────────────────────────────────────────────────────────

def test_cursor_default_is_cold_start(tmp_queue_dir):
    """When no cursor file exists, the lookback is 7 days."""
    cursor = A._read_cursor()
    assert (time.time() - cursor) > (6 * 86400)
    assert (time.time() - cursor) < (8 * 86400)


def test_cursor_roundtrip(tmp_queue_dir):
    A._write_cursor(1234567890.0)
    assert A._read_cursor() == 1234567890.0


# ── Failure detection ────────────────────────────────────────────────

def test_is_failure_quality_gate_false():
    assert A._is_failure({"outcome_summary": {"quality_gate": False}})


def test_is_failure_verdict_failure():
    assert A._is_failure({"outcome_summary": {"verdict": "failure"}})


def test_is_failure_high_retries():
    assert A._is_failure({"outcome_summary": {"retries": 3}})


def test_is_failure_clean_run():
    assert not A._is_failure({"outcome_summary": {"verdict": "success"}})


# ── End-to-end run_attribution (with mocked trajectory store) ───────

def test_run_attribution_empty_trajectories_dir(tmp_queue_dir, monkeypatch):
    """No trajectories → no work, but cursor is still advanced."""
    monkeypatch.setattr(A, "_TRAJECTORIES_DIR", tmp_queue_dir / "no_such_dir")
    summary = A.run_attribution()
    assert summary["scanned"] == 0
    assert A._read_cursor() > time.time() - 5


def test_run_attribution_skips_non_failure(tmp_queue_dir, monkeypatch):
    """Successful trajectories are scanned but produce no attributions."""
    traj_dir = tmp_queue_dir / "trajectories" / "2026-04-27"
    traj_dir.mkdir(parents=True)
    (traj_dir / "abc.json").write_text(json.dumps({
        "trajectory_id": "abc",
        "crew_name": "coder",
        "outcome_summary": {"verdict": "success"},
        "injected_skill_ids": ["skill_xfer_a"],
    }))
    monkeypatch.setattr(A, "_TRAJECTORIES_DIR", tmp_queue_dir / "trajectories")
    monkeypatch.setattr(A, "_load_skill_record", lambda rid: None)
    summary = A.run_attribution()
    assert summary["scanned"] >= 1
    assert summary["attributed"] == 0


def test_run_attribution_records_failure_attribution(tmp_queue_dir, monkeypatch):
    """Failed trajectory + injected transfer record → attribution row."""
    traj_dir = tmp_queue_dir / "trajectories" / "2026-04-27"
    traj_dir.mkdir(parents=True)
    (traj_dir / "fail.json").write_text(json.dumps({
        "trajectory_id": "fail",
        "crew_name": "coder",
        "outcome_summary": {"verdict": "failure"},
        "injected_skill_ids": ["skill_xfer_a"],
    }))
    monkeypatch.setattr(A, "_TRAJECTORIES_DIR", tmp_queue_dir / "trajectories")

    # Inject a stub record with a transfer_scope so it's recognised as
    # a transfer-memory record.
    rec = _record("skill_xfer_a", source_domain="coding")
    monkeypatch.setattr(A, "_load_skill_record", lambda rid: rec)

    summary = A.run_attribution()
    assert summary["attributed"] >= 1
    rows = A._read_neg_transfer_log()
    assert any(r.get("skill_record_id") == "skill_xfer_a" for r in rows)
