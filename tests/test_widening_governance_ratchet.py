"""Tests for Phase A.4 — governance_ratchet ledger emission on
widening approval (2026-05-22).

The verified plan promised: when zones widen (auto-apply allowlists
expand via the widening proposer + operator approval), emit a
governance_ratchet event to the identity continuity ledger so the
audit trail captures the loosening of an automation gate.

The ``governance_ratchet`` event kind already exists (PROGRAM §25.2
introduced it for governance.py threshold ratcheting). Risk-classifier
widening is the same shape of event — this just wires the existing
kind into the existing approval path.

Covers:
  * mark_approved fires record_event with the expected payload
  * Idempotent re-approval does NOT re-fire the event
  * Ledger failure-isolated (broken ledger doesn't block approval)
  * mark_rejected does NOT fire the event (only widening loosens trust)
"""
from __future__ import annotations

import sys
import types
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


def _import_modules():
    try:
        from app.risk_classifier import widening_decisions
        from app.risk_classifier.widening import WideningProposal
        return widening_decisions, WideningProposal
    except Exception as exc:
        pytest.skip(f"risk_classifier modules unavailable: {exc}")


@pytest.fixture
def isolated_decisions(tmp_path, monkeypatch):
    """Isolate the decisions store + stub the proposer + runtime_settings
    setters so we don't pollute real state."""
    widening_decisions, _ = _import_modules()
    widening_decisions.reset_for_tests(base_dir=tmp_path)

    # Stub list_proposals to return a fake one matching our test id
    _, WideningProposal = _import_modules()
    fake_proposal = WideningProposal(
        proposal_id="prop-test-001",
        list_name="auto_apply_allowed_requestors",
        new_entry="trusted-bot",
        evidence={"approvals": 50, "rollback_rate": 0.0},
        proposed_at="2026-05-22T10:00:00+00:00",
    )
    # list_proposals is imported lazily inside mark_approved/mark_rejected —
    # patch at the source module so the lazy import picks up the stub.
    from app.risk_classifier import widening
    monkeypatch.setattr(
        widening, "list_proposals", lambda **kw: [fake_proposal],
    )
    # Stub runtime_settings getters/setters
    try:
        from app import runtime_settings as rs
    except Exception as exc:
        pytest.skip(f"runtime_settings unavailable: {exc}")
    monkeypatch.setattr(rs, "get_auto_apply_allowed_requestors", lambda: [])
    monkeypatch.setattr(rs, "get_auto_apply_allowed_paths", lambda: [])
    monkeypatch.setattr(
        rs, "set_auto_apply_allowed_requestors", lambda v: None,
    )
    monkeypatch.setattr(
        rs, "set_auto_apply_allowed_paths", lambda v: None,
    )
    yield tmp_path
    widening_decisions.reset_for_tests(base_dir=None)


class TestApprovalEmitsEvent:
    def test_event_emitted_with_correct_payload(
        self, isolated_decisions, monkeypatch,
    ):
        widening_decisions, _ = _import_modules()
        captured = []

        def _capture(*, kind, actor, summary, detail=None, **kw):
            captured.append({
                "kind": kind,
                "actor": actor,
                "summary": summary,
                "detail": detail or {},
            })
            return True

        from app.identity import continuity_ledger
        monkeypatch.setattr(continuity_ledger, "record_event", _capture)

        widening_decisions.mark_approved(
            "prop-test-001",
            operator="alice@team",
            reason="50 successful approvals, zero rollbacks",
        )

        assert len(captured) == 1
        event = captured[0]
        assert event["kind"] == "governance_ratchet"
        assert event["actor"] == "alice@team"
        assert "widening approved" in event["summary"]
        assert "trusted-bot" in event["summary"]
        # Detail includes the subsystem discriminator
        assert event["detail"]["subsystem"] == "risk_classifier_widening"
        assert event["detail"]["proposal_id"] == "prop-test-001"
        assert event["detail"]["list_name"] == "auto_apply_allowed_requestors"
        assert event["detail"]["new_entry"] == "trusted-bot"

    def test_idempotent_reapproval_does_not_double_emit(
        self, isolated_decisions, monkeypatch,
    ):
        widening_decisions, _ = _import_modules()
        captured = []

        def _capture(**kw):
            captured.append(kw)
            return True

        from app.identity import continuity_ledger
        monkeypatch.setattr(continuity_ledger, "record_event", _capture)

        # First approval fires the event
        widening_decisions.mark_approved(
            "prop-test-001", operator="alice", reason="approved",
        )
        # Second approval (idempotent path) should NOT fire again —
        # the function returns early with the existing decision
        widening_decisions.mark_approved(
            "prop-test-001", operator="alice", reason="approved",
        )

        assert len(captured) == 1, (
            "Idempotent re-approval should NOT fire a second ledger "
            f"event, got {len(captured)}"
        )

    def test_ledger_failure_isolated(self, isolated_decisions, monkeypatch):
        """A sick continuity-ledger must not block the approval — the
        widening was already applied to runtime_settings before the
        emit attempt, so failing here would leave inconsistent state."""
        widening_decisions, _ = _import_modules()

        def _broken_emit(**kw):
            raise RuntimeError("ledger sick")

        from app.identity import continuity_ledger
        monkeypatch.setattr(continuity_ledger, "record_event", _broken_emit)

        # Should not raise
        result = widening_decisions.mark_approved(
            "prop-test-001", operator="alice", reason="ok",
        )
        from app.risk_classifier.widening_decisions import DecisionStatus
        assert result.status == DecisionStatus.APPROVED


class TestRejectionDoesNotEmit:
    def test_rejection_no_governance_ratchet(
        self, isolated_decisions, monkeypatch,
    ):
        """``mark_rejected`` is the operator saying NO — it does NOT
        loosen an automation gate, so no governance_ratchet event
        should fire."""
        widening_decisions, _ = _import_modules()
        captured = []

        def _capture(**kw):
            captured.append(kw)
            return True

        from app.identity import continuity_ledger
        monkeypatch.setattr(continuity_ledger, "record_event", _capture)

        widening_decisions.mark_rejected(
            "prop-test-001", operator="alice", reason="not yet",
        )

        # No event should have been fired by rejection
        ratchet_events = [
            e for e in captured if e.get("kind") == "governance_ratchet"
        ]
        assert ratchet_events == [], (
            f"Rejection should not emit governance_ratchet; got "
            f"{ratchet_events}"
        )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
