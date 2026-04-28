"""Tests for app.transfer_memory.promotion."""

import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.transfer_memory import promotion as P


@pytest.fixture
def tmp_queue_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSFER_MEMORY_DIR", str(tmp_path))
    yield tmp_path


def _record(id_, *, status="shadow", created_iso=None, kb="experiential",
            source_domain="coding", transfer_scope="global_meta",
            abstraction_score=0.5, leakage_risk=0.0, source_kind="healing"):
    if created_iso is None:
        created_iso = (
            datetime.now(timezone.utc) - timedelta(days=10)
        ).isoformat(timespec="seconds")
    rec = MagicMock()
    rec.id = id_
    rec.status = status
    rec.kb = kb
    rec.created_at = created_iso
    rec.topic = "Test topic"
    rec.provenance = {
        "source_domain": source_domain,
        "transfer_scope": transfer_scope,
        "abstraction_score": abstraction_score,
        "leakage_risk": leakage_risk,
        "source_kind": source_kind,
    }
    return rec


# ── Eligibility ──────────────────────────────────────────────────────

def test_evaluate_too_young(tmp_queue_dir):
    fresh = _record(
        "r1",
        created_iso=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    v = P._evaluate(fresh, surface_counts={"r1": 10}, blacklist=set())
    assert not v.eligible
    assert "too_young" in v.reasons


def test_evaluate_low_surface_count(tmp_queue_dir):
    rec = _record("r2")  # 10 days old by default
    v = P._evaluate(rec, surface_counts={"r2": 1}, blacklist=set())
    assert not v.eligible
    assert any(reason.startswith("low_surface_count=") for reason in v.reasons)


def test_evaluate_blacklisted(tmp_queue_dir):
    rec = _record("r3")
    v = P._evaluate(rec, surface_counts={"r3": 5}, blacklist={"r3"})
    assert not v.eligible
    assert "blacklisted" in v.reasons


def test_evaluate_with_negative_transfer_log(tmp_queue_dir):
    # Write a negative-transfer entry for record id.
    p = tmp_queue_dir / "negative_transfer.jsonl"
    p.write_text(json.dumps({
        "skill_record_id": "r4", "tag": "domain_mismatched_anchor",
        "ts": time.time(),
    }) + "\n")

    rec = _record("r4")
    v = P._evaluate(rec, surface_counts={"r4": 5}, blacklist=set())
    assert not v.eligible
    assert "negative_transfer_logged" in v.reasons


def test_evaluate_eligible_record(tmp_queue_dir):
    rec = _record("r5")  # 10 days old, status="shadow"
    v = P._evaluate(rec, surface_counts={"r5": 5}, blacklist=set())
    assert v.eligible
    assert v.reasons == []


def test_evaluate_unexpected_status(tmp_queue_dir):
    rec = _record("r6", status="active")
    v = P._evaluate(rec, surface_counts={"r6": 5}, blacklist=set())
    assert not v.eligible
    assert any("unexpected_status" in reason for reason in v.reasons)


# ── Surface counts ───────────────────────────────────────────────────

def test_surface_counts_aggregates_from_log(tmp_queue_dir):
    p = tmp_queue_dir / "shadow_retrievals.jsonl"
    p.write_text(
        json.dumps({"surfaced": [{"skill_record_id": "a"}, {"skill_record_id": "b"}]}) + "\n"
        + json.dumps({"surfaced": [{"skill_record_id": "a"}]}) + "\n"
    )
    counts = P._surface_counts_from_shadow_log()
    assert counts["a"] == 2
    assert counts["b"] == 1


def test_surface_counts_empty_when_log_missing(tmp_queue_dir):
    counts = P._surface_counts_from_shadow_log()
    assert counts == {}


# ── Cadence guard ────────────────────────────────────────────────────

def test_cadence_guard_skips_recent_run(tmp_queue_dir):
    P._write_last_run(time.time())
    summary = P.run_promotion()
    assert summary["skipped_cadence"] is True
    assert summary["promoted"] == 0


# ── End-to-end (audit-only mode) ─────────────────────────────────────

def test_run_promotion_audit_only_lists_candidates(tmp_queue_dir, monkeypatch):
    """When auto-promote is OFF, eligible records become candidates
    but are not actually promoted."""
    monkeypatch.setattr(P, "_auto_promote_enabled", lambda: False)

    eligible_rec = _record("good")
    monkeypatch.setattr(P, "_list_shadow_records", lambda: [eligible_rec])

    # Make it eligible: surface count high, no blacklist, no neg log.
    p = tmp_queue_dir / "shadow_retrievals.jsonl"
    p.write_text(json.dumps({"surfaced": [
        {"skill_record_id": "good"},
        {"skill_record_id": "good"},
        {"skill_record_id": "good"},
        {"skill_record_id": "good"},
    ]}) + "\n")

    summary = P.run_promotion()
    assert summary["ran"] is True
    assert summary["candidates"] == 1
    assert summary["promoted"] == 0
    assert summary["auto_promote"] is False

    # The candidates file should contain one entry.
    cand_p = tmp_queue_dir / "promotion_candidates.jsonl"
    assert cand_p.exists()
    rows = [json.loads(ln) for ln in cand_p.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["skill_record_id"] == "good"


def test_run_promotion_auto_promote_attempts_mutation(
    tmp_queue_dir, monkeypatch,
):
    """When auto-promote is ON, eligible records go through _promote().

    We mock _promote to return True without touching real KB stores."""
    monkeypatch.setattr(P, "_auto_promote_enabled", lambda: True)

    eligible_rec = _record("good")
    monkeypatch.setattr(P, "_list_shadow_records", lambda: [eligible_rec])
    p = tmp_queue_dir / "shadow_retrievals.jsonl"
    p.write_text(json.dumps({"surfaced": [
        {"skill_record_id": "good"},
        {"skill_record_id": "good"},
        {"skill_record_id": "good"},
    ]}) + "\n")

    monkeypatch.setattr(P, "_promote", lambda rec: True)

    summary = P.run_promotion()
    assert summary["promoted"] == 1
    assert summary["candidates"] == 1


def test_manual_promote_rejects_ineligible(tmp_queue_dir, monkeypatch):
    fresh = _record(
        "r9",
        created_iso=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    monkeypatch.setattr(P, "_load_skill_record", lambda rid: fresh)
    assert P.manual_promote("r9") is False


def test_manual_promote_unknown_id_returns_false(tmp_queue_dir, monkeypatch):
    monkeypatch.setattr(P, "_load_skill_record", lambda rid: None)
    assert P.manual_promote("nonexistent") is False


def test_manual_promote_eligible_calls_promote(tmp_queue_dir, monkeypatch):
    rec = _record("r10")
    monkeypatch.setattr(P, "_load_skill_record", lambda rid: rec)

    p = tmp_queue_dir / "shadow_retrievals.jsonl"
    p.write_text(json.dumps({"surfaced": [
        {"skill_record_id": "r10"},
        {"skill_record_id": "r10"},
        {"skill_record_id": "r10"},
    ]}) + "\n")

    monkeypatch.setattr(P, "_promote", lambda rec: True)
    assert P.manual_promote("r10") is True
