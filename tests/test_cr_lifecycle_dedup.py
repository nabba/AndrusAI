"""Tests for the Q18 CR lifecycle dedup (PROGRAM §57).

The core regression test: filing 1163 identical CRs from
``local_only`` should produce 1 CR with recurrence_count=1163, not
1163 separate JSON files cluttering the operator's review queue.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    """Point the change-request store at a temp dir so tests don't
    touch the live workspace."""
    from app.change_requests import store
    monkeypatch.setattr(store, "_STORE_DIR", tmp_path / "change_requests")
    monkeypatch.setattr(store, "_AUDIT_LOG",
                         tmp_path / "change_requests" / "audit.jsonl")
    store.reset_for_tests()
    yield
    store.reset_for_tests()


@pytest.fixture
def fake_validator(monkeypatch):
    """Force every validate() call to return OK so we test dedup
    semantics, not validator behavior."""
    from app.change_requests import lifecycle
    from app.change_requests.validator import ValidationResult
    def _ok(**kwargs):
        return ValidationResult(ok=True, reason="", is_tier_immutable=False)
    monkeypatch.setattr(lifecycle, "validate", _ok)
    monkeypatch.setattr(lifecycle, "validate_auto_apply", _ok)
    yield


@pytest.fixture
def stub_side_effects(monkeypatch):
    """The lifecycle integrates with lessons_learned, relevant_history,
    RPT-1, etc. Stub them so tests focus on dedup logic."""
    import app.change_requests.lifecycle as lc
    # The lessons_learned + history + RPT-1 calls are already inside
    # try/except so they degrade silently. We only need to silence
    # logger output and avoid network/DB. Nothing to monkeypatch.
    yield


def _create(requestor, path, content, reason="initial"):
    from app.change_requests import lifecycle
    return lifecycle.create_request(
        requestor=requestor, path=path,
        new_content=content, old_content="",
        reason=reason,
    )


def test_duplicate_create_bumps_recurrence_instead_of_new_cr(fake_validator, stub_side_effects):
    """The 2026-05-16 incident regression: identical CRs collapse."""
    from app.change_requests import lifecycle, store
    cr1 = _create("local_only_drill", "wiki/whatever.md", "x", "fail #1")
    cr2 = _create("local_only_drill", "wiki/whatever.md", "x", "fail #2")
    cr3 = _create("local_only_drill", "wiki/whatever.md", "x", "fail #3")
    # All three return the same record id
    assert cr1.id == cr2.id == cr3.id
    # Recurrence counter on the canonical record reflects all 3.
    canonical = store.get(cr1.id)
    assert canonical.recurrence_count == 2  # 2 recurrences after original
    # Only one CR file on disk
    assert len(store.list_all()) == 1


def test_recurrence_reasons_accumulate_in_canonical_record(fake_validator, stub_side_effects):
    from app.change_requests import lifecycle, store
    cr1 = _create("agent", "p.md", "x", "first reason")
    _create("agent", "p.md", "x", "second reason")
    canonical = store.get(cr1.id)
    assert "first reason" in canonical.reason
    assert "second reason" in canonical.reason


def test_applied_terminal_releases_dedup_hold(fake_validator, stub_side_effects):
    """An APPLIED CR is NOT a rejection — re-filing the same proposal
    later is a new event (the prior change already landed). The
    durable-rejection cooldown is scoped to REJECTED/TIMEOUT only, so
    APPLIED still releases the dedup hold."""
    from app.change_requests import lifecycle, store
    from app.change_requests.models import DecisionSource, Status
    cr1 = _create("agent", "p.md", "x", "first")
    lifecycle.approve(cr1.id, source=DecisionSource.REACT_APPROVE)
    # Simulate a successful apply (terminal, non-rejection).
    lifecycle.mark_applied(
        cr1.id, git_branch="b", git_commit_sha="deadbeef", pr_url=None,
    )
    assert store.get(cr1.id).status == Status.APPLIED
    cr2 = _create("agent", "p.md", "x", "first")
    assert cr2.id != cr1.id  # new CR — applied is not a suppression signal
    assert store.get(cr2.id).recurrence_count == 0


def test_rejected_refile_is_suppressed_within_cooldown(fake_validator, stub_side_effects):
    """Fix 1 (2026-05-29): the core loop-closer. A producer that
    re-files an identical CR after rejection does NOT create a second
    PENDING record within the cooldown window — it bumps the recurrence
    counter on the rejected record so the operator sees how persistently
    it keeps coming back."""
    from app.change_requests import lifecycle, store
    from app.change_requests.models import DecisionSource, Status
    cr1 = _create("vendor_sunset_monitor", "app/x.py", "x", "first")
    lifecycle.reject(cr1.id, source=DecisionSource.REACT_REJECT,
                     decision_reason="not now")
    assert store.get(cr1.id).status == Status.REJECTED

    # Re-file the identical proposal: must NOT mint a new record.
    cr2 = _create("vendor_sunset_monitor", "app/x.py", "x", "first")
    assert cr2.id == cr1.id
    # Still exactly one record, still REJECTED (never re-enters queue).
    assert len(store.list_all()) == 1
    canonical = store.get(cr1.id)
    assert canonical.status == Status.REJECTED
    assert canonical.recurrence_count == 1

    # Re-file a few more times — recurrence keeps climbing, no new CRs.
    for _ in range(5):
        _create("vendor_sunset_monitor", "app/x.py", "x", "again")
    assert len(store.list_all()) == 1
    assert store.get(cr1.id).recurrence_count == 6


def test_no_pending_cr_created_by_suppressed_refile(fake_validator, stub_side_effects):
    """The suppressed re-file must not surface anything in the PENDING
    queue — that is the whole point (operator review surface stays
    clean)."""
    from app.change_requests import lifecycle, store
    from app.change_requests.models import DecisionSource, Status
    cr1 = _create("local_only_drill", "wiki/topic.md", "x", "fail")
    lifecycle.reject(cr1.id, source=DecisionSource.SIGNAL_THUMBS_DOWN)
    _create("local_only_drill", "wiki/topic.md", "x", "fail again")
    assert store.list_all(status=Status.PENDING) == []


def test_refile_allowed_after_cooldown_expires(fake_validator, stub_side_effects):
    """The suppression is a cooldown, not a permanent block. Once the
    rejection is older than the cooldown window, an identical proposal
    is allowed to file a fresh CR for reconsideration."""
    from datetime import datetime, timedelta, timezone
    from app.change_requests import lifecycle, store
    from app.change_requests.models import DecisionSource, Status
    cr1 = _create("agent", "app/y.py", "x", "first")
    lifecycle.reject(cr1.id, source=DecisionSource.REACT_REJECT)
    # Backdate the decision beyond the cooldown window.
    rejected = store.get(cr1.id)
    old = (
        datetime.now(timezone.utc)
        - timedelta(days=lifecycle._REJECTED_SUPPRESSION_DAYS + 1)
    )
    rejected.decided_at = old.isoformat()
    store.save(rejected)
    # Now a re-file should mint a fresh PENDING CR.
    cr2 = _create("agent", "app/y.py", "x", "first")
    assert cr2.id != cr1.id
    assert store.get(cr2.id).status == Status.PENDING
    assert store.get(cr2.id).recurrence_count == 0


def test_timeout_also_suppresses_refile_within_cooldown(fake_validator, stub_side_effects):
    """A timed-out CR is the same 'operator did not approve' signal as a
    rejection; re-filing within the cooldown is suppressed too."""
    from app.change_requests import lifecycle, store
    from app.change_requests.models import Status
    cr1 = _create("wiki_index_reconciler", "wiki/index.md", "x", "drift")
    lifecycle.mark_timeout(cr1.id)
    assert store.get(cr1.id).status == Status.TIMEOUT
    cr2 = _create("wiki_index_reconciler", "wiki/index.md", "x", "drift")
    assert cr2.id == cr1.id
    assert len(store.list_all()) == 1


def test_suppression_is_scoped_to_same_requestor_and_content(fake_validator, stub_side_effects):
    """A rejection of one producer's proposal must not suppress a
    different producer, nor a different proposal from the same producer."""
    from app.change_requests import lifecycle, store
    from app.change_requests.models import DecisionSource
    cr1 = _create("agent_a", "app/z.py", "x", "r")
    lifecycle.reject(cr1.id, source=DecisionSource.REACT_REJECT)
    # Different requestor, same content → fresh CR (independent suggestion).
    cr2 = _create("agent_b", "app/z.py", "x", "r")
    assert cr2.id != cr1.id
    # Same requestor, different content → fresh CR.
    cr3 = _create("agent_a", "app/z.py", "y", "r")
    assert cr3.id != cr1.id


def test_different_requestors_dont_dedup(fake_validator, stub_side_effects):
    """Two different agents proposing the same change get separate
    CRs — they're independent suggestions."""
    cr1 = _create("agent_a", "p.md", "x", "reason")
    cr2 = _create("agent_b", "p.md", "x", "reason")
    assert cr1.id != cr2.id


def test_different_paths_dont_dedup(fake_validator, stub_side_effects):
    cr1 = _create("a", "p1.md", "x", "r")
    cr2 = _create("a", "p2.md", "x", "r")
    assert cr1.id != cr2.id


def test_different_content_dont_dedup(fake_validator, stub_side_effects):
    cr1 = _create("a", "p.md", "x", "r")
    cr2 = _create("a", "p.md", "y", "r")  # different new_content → different diff
    assert cr1.id != cr2.id


def test_approved_cr_still_dedupes(fake_validator, stub_side_effects):
    """An APPROVED-but-not-yet-applied CR is still the dedup target
    — a duplicate while it's mid-apply shouldn't create a parallel CR."""
    from app.change_requests import lifecycle, store
    from app.change_requests.models import DecisionSource
    cr1 = _create("agent", "p.md", "x", "r")
    lifecycle.approve(cr1.id, source=DecisionSource.REACT_APPROVE)
    cr2 = _create("agent", "p.md", "x", "r")
    assert cr2.id == cr1.id
    assert store.get(cr1.id).recurrence_count == 1


def test_apply_failed_cr_still_dedupes(fake_validator, stub_side_effects):
    """An APPLY_FAILED CR (retry path is active) is still a dedup
    target. The operator may retry; a duplicate would just be noise."""
    from app.change_requests import lifecycle, store
    from app.change_requests.models import DecisionSource, Status
    cr1 = _create("agent", "p.md", "x", "r")
    # Approve then mark apply failed (simulated transition)
    lifecycle.approve(cr1.id, source=DecisionSource.REACT_APPROVE)
    lifecycle.mark_apply_failed(cr1.id, error="git push failed")
    assert store.get(cr1.id).status == Status.APPLY_FAILED
    cr2 = _create("agent", "p.md", "x", "r")
    assert cr2.id == cr1.id


def test_first_seen_at_set_on_creation(fake_validator, stub_side_effects):
    from app.change_requests import store
    cr = _create("a", "p", "x")
    saved = store.get(cr.id)
    assert saved.first_seen_at is not None
    assert saved.first_seen_at == saved.created_at


def test_recurrence_count_field_persists_round_trip(fake_validator, stub_side_effects):
    """Serialization round-trip preserves recurrence_count."""
    from app.change_requests import store
    cr = _create("a", "p", "x", "first")
    _create("a", "p", "x", "second")
    _create("a", "p", "x", "third")
    saved = store.get(cr.id)
    # Force reload from disk
    store.reset_for_tests()
    reloaded = store.get(cr.id)
    assert reloaded.recurrence_count == saved.recurrence_count
    assert reloaded.content_hash == saved.content_hash
    assert reloaded.first_seen_at == saved.first_seen_at


def test_content_hash_stable_across_calls(fake_validator, stub_side_effects):
    """Same inputs → same content_hash. (Pins the dedup key
    computation against accidental changes.)"""
    from app.change_requests.lifecycle import (
        _compute_content_hash, _compute_diff,
    )
    diff = _compute_diff("p.md", "old", "new")
    h1 = _compute_content_hash(requestor="a", path="p.md", diff=diff, reason="r")
    h2 = _compute_content_hash(requestor="a", path="p.md", diff=diff, reason="r")
    assert h1 == h2


def test_thousand_duplicates_collapse_to_one_record(fake_validator, stub_side_effects):
    """The exact 2026-05-16 incident pattern. local_only filed 1163
    identical CRs in 48h. With Q18 dedup, all of them collapse to
    one record with recurrence_count = 1162."""
    from app.change_requests import store
    first = _create("local_only_drill", "wiki/topic.md", "x", "vendor failure")
    for _ in range(999):
        _create("local_only_drill", "wiki/topic.md", "x", "vendor failure")
    # 1 record on disk
    assert len(store.list_all()) == 1
    # And the recurrence counter reflects every additional call
    final = store.get(first.id)
    assert final.recurrence_count == 999
