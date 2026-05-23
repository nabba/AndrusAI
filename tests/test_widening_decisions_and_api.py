"""Tests for the widening decision flow + REST surface (2026-05-20).

Covers Phase 4 piece 1b:
  * widening_decisions.mark_approved / mark_rejected lifecycle
  * Approval idempotency + cross-state refusal
  * pending_proposals filters out decided ones
  * Approval actually calls runtime_settings setters
  * Rejection doesn't change settings
  * Scheduler tuple registered with HEAVY weight + master-switch-gated
  * REST: GET list / detail / 404, POST approve / reject / idempotent
  * REST: 404 unknown, 409 cross-state, 200 idempotent
"""
from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())

try:
    import crewai as _real_crewai  # noqa: F401
    _crewai_available = True
except Exception:
    _crewai_available = False

if not _crewai_available:
    for _mod in ("crewai", "crewai.tools"):
        if _mod not in sys.modules:
            m = types.ModuleType(_mod)
            if _mod == "crewai.tools":
                m.tool = lambda name: (lambda fn: fn)
                m.BaseTool = type("BaseTool", (), {})
            sys.modules[_mod] = m


from app import runtime_settings  # noqa: E402
from app.risk_classifier import widening, widening_decisions  # noqa: E402
from app.risk_classifier.widening import (  # noqa: E402
    WideningEvidence,
    WideningProposal,
    append_proposal,
)
from app.risk_classifier.widening_decisions import (  # noqa: E402
    DecisionStatus,
    decision_for,
    mark_approved,
    mark_rejected,
    pending_proposals,
)


def _make_proposal(
    *,
    proposal_id: str,
    list_name: str = "auto_apply_allowed_requestors",
    new_entry: str = "self_heal_router",
) -> WideningProposal:
    return WideningProposal(
        proposal_id=proposal_id,
        proposed_at="2026-05-20T12:00:00+00:00",
        list_name=list_name,
        new_entry=new_entry,
        evidence=WideningEvidence(
            requestor="self_heal_router",
            path_prefix="workspace/notes/",
            approvals=12,
            rollbacks=0,
        ),
        rationale="strong track record",
    )


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path):
    """Redirect both audit + decisions to tmp_path and clear caches."""
    widening.reset_for_tests(tmp_path)
    widening_decisions.reset_for_tests(tmp_path)
    runtime_settings._cache = None  # type: ignore[attr-defined]
    yield
    runtime_settings._cache = None  # type: ignore[attr-defined]
    widening.reset_for_tests(None)
    widening_decisions.reset_for_tests(None)


def _patch_settings(**overrides):
    base = runtime_settings._defaults()
    base.update(overrides)
    return patch.object(runtime_settings, "_cache", base)


# ============================================================================
# Decision lifecycle
# ============================================================================


class TestDecisionLifecycle:
    def test_unknown_proposal_raises_key_error(self):
        with pytest.raises(KeyError, match="not found"):
            mark_approved("nonexistent")
        with pytest.raises(KeyError, match="not found"):
            mark_rejected("nonexistent")

    def test_approve_records_decision(self):
        append_proposal(_make_proposal(proposal_id="p1"))
        with _patch_settings(), patch.object(runtime_settings, "_save"):
            decision = mark_approved("p1", operator="op1", reason="looks safe")
        assert decision.status is DecisionStatus.APPROVED
        assert decision.operator == "op1"
        assert decision.reason == "looks safe"

    def test_reject_records_decision(self):
        append_proposal(_make_proposal(proposal_id="p1"))
        decision = mark_rejected("p1", operator="op1", reason="too soon")
        assert decision.status is DecisionStatus.REJECTED
        assert decision.reason == "too soon"

    def test_approve_then_approve_idempotent(self):
        append_proposal(_make_proposal(proposal_id="p1"))
        with _patch_settings(), patch.object(runtime_settings, "_save"):
            d1 = mark_approved("p1")
            d2 = mark_approved("p1")
        # Same decision returned both times — no new audit row
        assert d1.decided_at == d2.decided_at

    def test_reject_then_reject_idempotent(self):
        append_proposal(_make_proposal(proposal_id="p1"))
        d1 = mark_rejected("p1")
        d2 = mark_rejected("p1")
        assert d1.decided_at == d2.decided_at

    def test_approve_then_reject_refused(self):
        """Approved is terminal-positive; trying to reject after approve
        is a state-machine violation (operator's prior intent honored)."""
        append_proposal(_make_proposal(proposal_id="p1"))
        with _patch_settings(), patch.object(runtime_settings, "_save"):
            mark_approved("p1")
        with pytest.raises(ValueError, match="already approved"):
            mark_rejected("p1")

    def test_reject_then_approve_refused(self):
        append_proposal(_make_proposal(proposal_id="p1"))
        mark_rejected("p1")
        with _patch_settings(), patch.object(runtime_settings, "_save"):
            with pytest.raises(ValueError, match="already rejected"):
                mark_approved("p1")

    def test_decision_for_returns_none_when_pending(self):
        append_proposal(_make_proposal(proposal_id="p1"))
        assert decision_for("p1") is None

    def test_decision_for_returns_record_after_decision(self):
        append_proposal(_make_proposal(proposal_id="p1"))
        mark_rejected("p1")
        d = decision_for("p1")
        assert d is not None
        assert d.status is DecisionStatus.REJECTED

    def test_empty_proposal_id_rejected(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            mark_approved("")
        with pytest.raises(ValueError, match="cannot be empty"):
            mark_rejected("")


# ============================================================================
# Approval actually widens the allowlist
# ============================================================================


class TestApprovalAppliesWidening:
    def test_approve_requestor_calls_setter(self):
        prop = _make_proposal(
            proposal_id="p1",
            list_name="auto_apply_allowed_requestors",
            new_entry="self_heal_router",
        )
        append_proposal(prop)
        with _patch_settings(), patch.object(runtime_settings, "_save"):
            assert runtime_settings.get_auto_apply_allowed_requestors() == []
            mark_approved("p1")
            assert (
                "self_heal_router"
                in runtime_settings.get_auto_apply_allowed_requestors()
            )

    def test_approve_path_calls_setter(self):
        prop = _make_proposal(
            proposal_id="p2",
            list_name="auto_apply_allowed_paths",
            new_entry="workspace/notes/",
        )
        append_proposal(prop)
        with _patch_settings(), patch.object(runtime_settings, "_save"):
            assert runtime_settings.get_auto_apply_allowed_paths() == []
            mark_approved("p2")
            assert (
                "workspace/notes/"
                in runtime_settings.get_auto_apply_allowed_paths()
            )

    def test_reject_does_not_change_setting(self):
        prop = _make_proposal(
            proposal_id="p1",
            list_name="auto_apply_allowed_requestors",
            new_entry="self_heal_router",
        )
        append_proposal(prop)
        with _patch_settings(), patch.object(runtime_settings, "_save"):
            mark_rejected("p1")
            assert runtime_settings.get_auto_apply_allowed_requestors() == []

    def test_approve_preserves_existing_entries(self):
        prop = _make_proposal(
            proposal_id="p1",
            list_name="auto_apply_allowed_requestors",
            new_entry="new_agent",
        )
        append_proposal(prop)
        with _patch_settings(
            auto_apply_allowed_requestors=["existing_agent"],
        ), patch.object(runtime_settings, "_save"):
            mark_approved("p1")
            result = runtime_settings.get_auto_apply_allowed_requestors()
            assert "existing_agent" in result
            assert "new_agent" in result

    def test_approve_idempotent_does_not_duplicate_entry(self):
        prop = _make_proposal(
            proposal_id="p1",
            new_entry="self_heal_router",
        )
        append_proposal(prop)
        with _patch_settings(
            auto_apply_allowed_requestors=["self_heal_router"],
        ), patch.object(runtime_settings, "_save"):
            mark_approved("p1")
            result = runtime_settings.get_auto_apply_allowed_requestors()
            assert result.count("self_heal_router") == 1


# ============================================================================
# pending_proposals filter
# ============================================================================


class TestPendingProposals:
    def test_empty_returns_empty(self):
        assert pending_proposals() == []

    def test_filters_out_approved(self):
        for i in range(3):
            append_proposal(_make_proposal(proposal_id=f"p{i}"))
        with _patch_settings(), patch.object(runtime_settings, "_save"):
            mark_approved("p1")
        pending = pending_proposals()
        ids = {p.proposal_id for p in pending}
        assert ids == {"p0", "p2"}

    def test_filters_out_rejected(self):
        for i in range(3):
            append_proposal(_make_proposal(proposal_id=f"p{i}"))
        mark_rejected("p2")
        pending = pending_proposals()
        ids = {p.proposal_id for p in pending}
        assert ids == {"p0", "p1"}

    def test_returns_all_when_none_decided(self):
        for i in range(3):
            append_proposal(_make_proposal(proposal_id=f"p{i}"))
        pending = pending_proposals()
        assert len(pending) == 3

    def test_limit_honored(self):
        for i in range(20):
            append_proposal(_make_proposal(proposal_id=f"p{i:02d}"))
        pending = pending_proposals(limit=5)
        assert len(pending) == 5


# ============================================================================
# Scheduler tuple registration
# ============================================================================


class TestSchedulerTuple:
    def test_tuple_registered(self):
        from app.idle_scheduler import JobWeight, _default_jobs
        jobs = _default_jobs()
        matching = [j for j in jobs if j[0] == "widening-proposer-scan"]
        assert len(matching) == 1
        name, fn, weight = matching[0]
        assert weight == JobWeight.HEAVY
        assert callable(fn)

    def test_tuple_function_master_switch_gated(self):
        from app.idle_scheduler import _default_jobs
        jobs = _default_jobs()
        matching = [j for j in jobs if j[0] == "widening-proposer-scan"]
        _, fn, _ = matching[0]
        # Default settings have widening_proposer_enabled=False.
        with _patch_settings(widening_proposer_enabled=False):
            fn()  # should not raise


# ============================================================================
# REST surface
# ============================================================================


class TestRestEndpoints:
    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.control_plane.widening_api import router
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_list_empty(self):
        c = self._client()
        resp = c.get("/api/cp/widening")
        assert resp.status_code == 200
        assert resp.json() == {"count": 0, "proposals": []}

    def test_list_pending_includes_proposal(self):
        append_proposal(_make_proposal(proposal_id="p1"))
        c = self._client()
        resp = c.get("/api/cp/widening")
        data = resp.json()
        assert data["count"] == 1
        assert data["proposals"][0]["proposal_id"] == "p1"
        assert data["proposals"][0]["decision_status"] == "pending"

    def test_list_pending_excludes_decided(self):
        append_proposal(_make_proposal(proposal_id="p1"))
        append_proposal(_make_proposal(proposal_id="p2"))
        mark_rejected("p2")
        c = self._client()
        resp = c.get("/api/cp/widening")
        data = resp.json()
        assert data["count"] == 1
        assert data["proposals"][0]["proposal_id"] == "p1"

    def test_list_all_includes_decided(self):
        append_proposal(_make_proposal(proposal_id="p1"))
        append_proposal(_make_proposal(proposal_id="p2"))
        mark_rejected("p2")
        c = self._client()
        resp = c.get("/api/cp/widening/all")
        data = resp.json()
        assert data["count"] == 2
        statuses = {
            p["proposal_id"]: p["decision_status"]
            for p in data["proposals"]
        }
        assert statuses == {"p1": "pending", "p2": "rejected"}

    def test_get_unknown_returns_404(self):
        c = self._client()
        resp = c.get("/api/cp/widening/nope")
        assert resp.status_code == 404

    def test_get_returns_full_detail(self):
        append_proposal(_make_proposal(proposal_id="p1"))
        c = self._client()
        resp = c.get("/api/cp/widening/p1")
        data = resp.json()
        assert data["proposal_id"] == "p1"
        assert data["new_entry"] == "self_heal_router"
        assert data["decision_status"] == "pending"
        assert data["decision"] is None

    def test_post_approve_applies_widening(self):
        append_proposal(_make_proposal(proposal_id="p1"))
        c = self._client()
        with _patch_settings(), patch.object(runtime_settings, "_save"):
            resp = c.post(
                "/api/cp/widening/p1/approve",
                json={"operator": "op-a", "reason": "looks safe"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["decision_status"] == "approved"
            # Setting actually changed
            assert (
                "self_heal_router"
                in runtime_settings.get_auto_apply_allowed_requestors()
            )

    def test_post_approve_unknown_returns_404(self):
        c = self._client()
        resp = c.post(
            "/api/cp/widening/nope/approve",
            json={"operator": "x"},
        )
        assert resp.status_code == 404

    def test_post_approve_idempotent(self):
        append_proposal(_make_proposal(proposal_id="p1"))
        c = self._client()
        with _patch_settings(), patch.object(runtime_settings, "_save"):
            r1 = c.post(
                "/api/cp/widening/p1/approve", json={},
            )
            r2 = c.post(
                "/api/cp/widening/p1/approve", json={},
            )
            assert r1.status_code == 200
            assert r2.status_code == 200
            assert r1.json()["decision"]["decided_at"] == \
                r2.json()["decision"]["decided_at"]

    def test_post_approve_after_reject_409(self):
        append_proposal(_make_proposal(proposal_id="p1"))
        c = self._client()
        c.post("/api/cp/widening/p1/reject", json={"reason": "no"})
        with _patch_settings(), patch.object(runtime_settings, "_save"):
            resp = c.post("/api/cp/widening/p1/approve", json={})
            assert resp.status_code == 409

    def test_post_reject_records_decision(self):
        append_proposal(_make_proposal(proposal_id="p1"))
        c = self._client()
        resp = c.post(
            "/api/cp/widening/p1/reject",
            json={"operator": "op-b", "reason": "premature"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["decision_status"] == "rejected"
        # Setting was NOT changed (no patch_settings here means the
        # default runtime_settings see no widening).
        # The reject path explicitly doesn't call any setter.
        assert data["decision"]["reason"] == "premature"

    def test_post_reject_unknown_returns_404(self):
        c = self._client()
        resp = c.post("/api/cp/widening/nope/reject", json={})
        assert resp.status_code == 404

    def test_post_reject_after_approve_409(self):
        append_proposal(_make_proposal(proposal_id="p1"))
        c = self._client()
        with _patch_settings(), patch.object(runtime_settings, "_save"):
            c.post("/api/cp/widening/p1/approve", json={})
        resp = c.post("/api/cp/widening/p1/reject", json={})
        assert resp.status_code == 409


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
