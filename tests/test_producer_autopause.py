"""Tests for Gate C — per-producer approval-rate auto-pause (2026-05-30).

The backstop behind Gate A/B: an observational producer that floods the
operator with DISTINCT low-value proposals (each evading the semantic gate)
gets auto-paused once its rolling explicit-operator-approval rate craters.
Self-releasing cooldown; observational producers only; system suppressions
excluded from the rate so the pause can't latch.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    from app.change_requests import store
    monkeypatch.setattr(store, "_STORE_DIR", tmp_path / "change_requests")
    monkeypatch.setattr(store, "_AUDIT_LOG",
                         tmp_path / "change_requests" / "audit.jsonl")
    store.reset_for_tests()
    yield
    store.reset_for_tests()


def _save_cr(requestor, *, decided_by, age_days=1, status=None, idx=0):
    """Persist a decided CR directly into the store."""
    from app.change_requests import store
    from app.change_requests.models import ChangeRequest, Status, DecisionSource
    created = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    st = status or (
        Status.APPLIED
        if decided_by in (DecisionSource.REACT_APPROVE, DecisionSource.SIGNAL_THUMBS_UP)
        else Status.REJECTED
    )
    cr = ChangeRequest(
        id=f"{requestor[:4]}{idx:04d}"[:12].ljust(8, "0"),
        created_at=created, requestor=requestor,
        path=f"docs/proposed_libraries/x{idx}.md",
        new_content="x", old_content="", reason="r", diff="d",
    )
    cr.status = st
    cr.decided_by = decided_by
    store.save(cr, audit_event="seed")
    return cr


def _seed(requestor, *, approved, rejected, age_days=2):
    from app.change_requests.models import DecisionSource
    i = 0
    for _ in range(approved):
        _save_cr(requestor, decided_by=DecisionSource.REACT_APPROVE, age_days=age_days, idx=i); i += 1
    for _ in range(rejected):
        _save_cr(requestor, decided_by=DecisionSource.REACT_REJECT, age_days=age_days, idx=i); i += 1


# ── approval_stats ───────────────────────────────────────────────────


def test_approval_stats_counts_only_operator_decisions():
    from app.change_requests import producer_health as p
    from app.change_requests.models import DecisionSource, Status
    r = "proposal_bridge:library_radar"
    _seed(r, approved=2, rejected=8)
    # Noise that must be EXCLUDED: a system suppression (decided_by None) and
    # an auto-apply — neither is an explicit operator decision.
    _save_cr(r, decided_by=None, status=Status.REJECTED, idx=50)
    _save_cr(r, decided_by=DecisionSource.SELF_HEAL_AUTO_APPLY, status=Status.APPLIED, idx=51)
    stats = p.approval_stats(r, window_days=30)
    assert (stats.approved, stats.rejected, stats.n) == (2, 8, 10)
    assert stats.rate == pytest.approx(0.2)


def test_approval_stats_respects_window():
    from app.change_requests import producer_health as p
    r = "proposal_bridge:library_radar"
    _seed(r, approved=0, rejected=8, age_days=90)  # outside 30d window
    stats = p.approval_stats(r, window_days=30)
    assert stats.n == 0 and stats.rate is None


# ── evaluate (pause decision) ────────────────────────────────────────


def test_chronically_rejected_producer_is_paused():
    from app.change_requests import producer_health as p
    r = "proposal_bridge:library_radar"
    _seed(r, approved=1, rejected=11)  # 8% over 12 samples
    v = p.evaluate(r)
    assert v.paused is True
    assert "auto-paused" in v.reason


def test_insufficient_samples_never_pauses():
    from app.change_requests import producer_health as p
    r = "proposal_bridge:library_radar"
    _seed(r, approved=0, rejected=5)  # below min_samples=10
    v = p.evaluate(r)
    assert v.paused is False
    assert "insufficient data" in v.reason


def test_healthy_rate_not_paused():
    from app.change_requests import producer_health as p
    r = "proposal_bridge:library_radar"
    _seed(r, approved=8, rejected=4)  # 67%
    assert p.evaluate(r).paused is False


def test_non_observational_producer_never_paused():
    """A human or bug-fix producer is never auto-paused, even with a
    terrible approval rate."""
    from app.change_requests import producer_health as p
    _seed("coder", approved=0, rejected=20)
    assert p.evaluate("coder").paused is False


def test_disabled_switch_never_pauses(monkeypatch):
    from app.change_requests import producer_health as p
    monkeypatch.setattr(p, "config", lambda: (False, 0.15, 10, 30))
    _seed("proposal_bridge:library_radar", approved=0, rejected=20)
    assert p.evaluate("proposal_bridge:library_radar").paused is False


# ── create_request enforcement ───────────────────────────────────────


@pytest.fixture
def ok_validator(monkeypatch):
    from app.change_requests import lifecycle
    from app.change_requests.validator import ValidationResult
    ok = lambda **kw: ValidationResult(ok=True, reason="", is_tier_immutable=False)
    monkeypatch.setattr(lifecycle, "validate", ok)
    monkeypatch.setattr(lifecycle, "validate_auto_apply", ok)
    # Keep Gate A out of the way so we isolate Gate C.
    monkeypatch.setattr(lifecycle, "_maybe_suppress_by_lesson", lambda **kw: None)
    yield


def test_create_request_suppresses_paused_producer(ok_validator):
    from app.change_requests import lifecycle, store
    from app.change_requests.models import Status
    r = "proposal_bridge:library_radar"
    _seed(r, approved=1, rejected=11)
    cr = lifecycle.create_request(
        requestor=r, path="docs/proposed_libraries/new.md",
        new_content="# x", old_content="", reason="another idea",
    )
    assert cr.status == Status.REJECTED
    assert "auto-paused" in (cr.decision_reason or "")
    # Recorded with decided_by=None so it's EXCLUDED from future rate calc
    # (pause can't latch on its own suppressions).
    assert cr.decided_by is None


def test_paused_suppression_excluded_from_rate(ok_validator):
    """The Gate-C suppression itself must not count toward the rate — else
    the pause would never release."""
    from app.change_requests import lifecycle, producer_health as p
    r = "proposal_bridge:library_radar"
    _seed(r, approved=1, rejected=11)
    before = p.approval_stats(r, window_days=30).n
    lifecycle.create_request(
        requestor=r, path="docs/proposed_libraries/new.md",
        new_content="# x", old_content="", reason="idea",
    )
    after = p.approval_stats(r, window_days=30).n
    assert after == before  # the suppressed CR didn't move the denominator


def test_healthy_producer_not_suppressed(ok_validator):
    from app.change_requests import lifecycle
    from app.change_requests.models import Status
    r = "proposal_bridge:library_radar"
    _seed(r, approved=9, rejected=3)
    cr = lifecycle.create_request(
        requestor=r, path="docs/proposed_libraries/new.md",
        new_content="# x", old_content="", reason="idea",
    )
    assert cr.status == Status.PENDING
