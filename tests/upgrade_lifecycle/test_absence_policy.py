"""Tests for app.upgrade_lifecycle.absence_policy (P1#a).

PROGRAM §63 follow-up. Covers:

  1.  Master switch OFF returns "master_switch_off"
  2.  Operator present (ACTIVE phase) returns "operator_present"
  3.  Operator ABSENT_90D + eligible CR → promoted
  4.  Untrusted requestor refused
  5.  Wrong status (e.g. REJECTED) refused
  6.  Not patch-level refused
  7.  Too young (< 14d) refused
  8.  Notify fires for each promotion
  9.  Ledger event fires for each promotion
  10. State file persists last_run_at + history
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.upgrade_lifecycle import absence_policy as ap


@pytest.fixture
def isolated_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("UPGRADE_LIFECYCLE_DIR", str(tmp_path / "ul"))
    return tmp_path / "ul"


@pytest.fixture
def absent(monkeypatch):
    """Force operator-absent state."""
    monkeypatch.setattr(ap, "_absent_for_at_least_90d", lambda: True)


def _make_cr(*, cr_id="cr-1", requestor="dependency_radar",
            status="pending", days_old=30,
            reason="Dependency: bump starlette patch-level"):
    return {
        "id": cr_id, "requestor": requestor, "status": status,
        "created_at": (
            datetime.now(timezone.utc) - timedelta(days=days_old)
        ).isoformat(),
        "reason": reason,
        "path": "requirements.txt",
    }


# ── 1: Master switch ────────────────────────────────────────────────────


def test_master_switch_off(isolated_dir, monkeypatch):
    monkeypatch.setattr(ap, "_enabled", lambda: False)
    out = ap.evaluate(cr_lister=lambda: [], auto_approve_fn=lambda *_: None)
    assert out.eligible is False
    assert out.reason == "master_switch_off"


# ── 2: Operator present ─────────────────────────────────────────────────


def test_operator_present_blocks(isolated_dir, monkeypatch):
    monkeypatch.setattr(ap, "_enabled", lambda: True)
    monkeypatch.setattr(ap, "_absent_for_at_least_90d", lambda: False)
    out = ap.evaluate(cr_lister=lambda: [_make_cr()],
                     auto_approve_fn=lambda *_: None)
    assert out.eligible is False
    assert out.reason == "operator_present"


# ── A2-P0: phase-trigger semantics ──────────────────────────────────────


def test_phase_trigger_refuses_read_mostly(monkeypatch):
    """READ_MOSTLY means operator IS engaging selectively — auto-apply
    must NOT widen against an active operator's non-action."""
    class _FakePhase:
        ACTIVE = type("P", (), {"value": "active"})()
        ABSENT_30D = type("P", (), {"value": "absent_30d"})()
        ABSENT_90D = type("P", (), {"value": "absent_90d"})()
        READ_MOSTLY = type("P", (), {"value": "read_mostly"})()
        TRANSITIONED = type("P", (), {"value": "transitioned"})()

    monkeypatch.setattr(
        "app.operator_transition.current_phase",
        lambda: {"phase": "read_mostly"},
    )
    monkeypatch.setattr(
        "app.operator_transition.OperatorPhase", _FakePhase,
    )
    assert ap._absent_for_at_least_90d() is False, (
        "READ_MOSTLY must not trigger auto-apply"
    )


def test_phase_trigger_fires_on_absent_90d(monkeypatch):
    class _FakePhase:
        ABSENT_90D = type("P", (), {"value": "absent_90d"})()
        TRANSITIONED = type("P", (), {"value": "transitioned"})()

    monkeypatch.setattr(
        "app.operator_transition.current_phase",
        lambda: {"phase": "absent_90d"},
    )
    monkeypatch.setattr(
        "app.operator_transition.OperatorPhase", _FakePhase,
    )
    assert ap._absent_for_at_least_90d() is True


def test_phase_trigger_fires_on_transitioned(monkeypatch):
    class _FakePhase:
        ABSENT_90D = type("P", (), {"value": "absent_90d"})()
        TRANSITIONED = type("P", (), {"value": "transitioned"})()

    monkeypatch.setattr(
        "app.operator_transition.current_phase",
        lambda: {"phase": "transitioned"},
    )
    monkeypatch.setattr(
        "app.operator_transition.OperatorPhase", _FakePhase,
    )
    assert ap._absent_for_at_least_90d() is True


def test_phase_trigger_refuses_active(monkeypatch):
    class _FakePhase:
        ABSENT_90D = type("P", (), {"value": "absent_90d"})()
        TRANSITIONED = type("P", (), {"value": "transitioned"})()

    monkeypatch.setattr(
        "app.operator_transition.current_phase",
        lambda: {"phase": "active"},
    )
    monkeypatch.setattr(
        "app.operator_transition.OperatorPhase", _FakePhase,
    )
    assert ap._absent_for_at_least_90d() is False


# ── 3: Happy path — eligible CR promoted ────────────────────────────────


def test_eligible_cr_promoted(isolated_dir, monkeypatch, absent):
    monkeypatch.setattr(ap, "_enabled", lambda: True)
    monkeypatch.setattr(ap, "_notify_promoted", lambda cr_id, cr: None)
    monkeypatch.setattr(ap, "_emit_audit", lambda cr_id, cr: None)

    promoted_ids = []
    def _approve(cr_id):
        promoted_ids.append(cr_id)

    out = ap.evaluate(
        cr_lister=lambda: [_make_cr(cr_id="cr-good")],
        auto_approve_fn=_approve,
    )
    assert out.eligible is True
    assert out.auto_applied == ("cr-good",)
    assert promoted_ids == ["cr-good"]


# ── 4: Untrusted requestor refused ──────────────────────────────────────


def test_untrusted_requestor_refused(isolated_dir, monkeypatch, absent):
    monkeypatch.setattr(ap, "_enabled", lambda: True)
    out = ap.evaluate(
        cr_lister=lambda: [_make_cr(requestor="evil_module")],
        auto_approve_fn=lambda *_: None,
    )
    assert out.eligible is True
    assert out.auto_applied == ()


# ── 5: Wrong status refused ─────────────────────────────────────────────


def test_wrong_status_refused(isolated_dir, monkeypatch, absent):
    monkeypatch.setattr(ap, "_enabled", lambda: True)
    out = ap.evaluate(
        cr_lister=lambda: [_make_cr(status="rejected")],
        auto_approve_fn=lambda *_: None,
    )
    assert out.eligible is True
    assert out.auto_applied == ()


# ── 6: Not patch-level refused ──────────────────────────────────────────


def test_minor_bump_refused(isolated_dir, monkeypatch, absent):
    monkeypatch.setattr(ap, "_enabled", lambda: True)
    out = ap.evaluate(
        cr_lister=lambda: [_make_cr(reason="Dependency: bump starlette minor-version")],
        auto_approve_fn=lambda *_: None,
    )
    assert out.eligible is True
    assert out.auto_applied == ()


def test_major_bump_refused(isolated_dir, monkeypatch, absent):
    monkeypatch.setattr(ap, "_enabled", lambda: True)
    out = ap.evaluate(
        cr_lister=lambda: [_make_cr(reason="Dependency: bump starlette major-version")],
        auto_approve_fn=lambda *_: None,
    )
    assert out.eligible is True
    assert out.auto_applied == ()


# ── 7: Too young refused ────────────────────────────────────────────────


def test_too_young_cr_refused(isolated_dir, monkeypatch, absent):
    monkeypatch.setattr(ap, "_enabled", lambda: True)
    out = ap.evaluate(
        cr_lister=lambda: [_make_cr(days_old=5)],   # < 14d
        auto_approve_fn=lambda *_: None,
    )
    assert out.eligible is True
    assert out.auto_applied == ()


# ── A4-P1: license-change defense ─────────────────────────────────────


def test_license_change_cr_refused(isolated_dir, monkeypatch, absent):
    """CR body mentioning a license change → refused even when
    everything else qualifies."""
    monkeypatch.setattr(ap, "_enabled", lambda: True)
    cr = _make_cr(reason=(
        "Dependency: bump pydantic patch-level. ⚠️ License change "
        "from MIT to AGPLv3 — review legal implications."
    ))
    promoted = []
    out = ap.evaluate(
        cr_lister=lambda: [cr],
        auto_approve_fn=lambda cr_id: promoted.append(cr_id),
    )
    assert out.eligible is True
    assert out.auto_applied == ()
    assert promoted == []


def test_license_change_caseless_refused(isolated_dir, monkeypatch, absent):
    """Pattern matches case-insensitively."""
    monkeypatch.setattr(ap, "_enabled", lambda: True)
    cr = _make_cr(reason=(
        "Dependency: bump x patch-level. License_change: MIT -> SSPL."
    ))
    out = ap.evaluate(
        cr_lister=lambda: [cr],
        auto_approve_fn=lambda *_: None,
    )
    assert out.auto_applied == ()


# ── 8 + 9: Notify + ledger fire on promotion ────────────────────────────


def test_notify_and_ledger_fire_on_promotion(isolated_dir, monkeypatch, absent):
    monkeypatch.setattr(ap, "_enabled", lambda: True)
    notified = []
    audited = []
    monkeypatch.setattr(ap, "_notify_promoted",
                       lambda cr_id, cr: notified.append(cr_id))
    monkeypatch.setattr(ap, "_emit_audit",
                       lambda cr_id, cr: audited.append(cr_id))

    ap.evaluate(
        cr_lister=lambda: [_make_cr(cr_id="cr-x")],
        auto_approve_fn=lambda cr_id: None,
    )
    assert notified == ["cr-x"]
    assert audited == ["cr-x"]


# ── 10: State persistence ───────────────────────────────────────────────


def test_state_file_persists_history(isolated_dir, monkeypatch, absent):
    monkeypatch.setattr(ap, "_enabled", lambda: True)
    monkeypatch.setattr(ap, "_notify_promoted", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_emit_audit", lambda *a, **k: None)

    ap.evaluate(
        cr_lister=lambda: [_make_cr(cr_id="cr-1")],
        auto_approve_fn=lambda cr_id: None,
    )
    import json
    state = json.loads(ap._state_path().read_text())
    assert "last_run_at" in state
    assert state["history"][-1]["promoted"] == ["cr-1"]
