"""Round 2 audit follow-up — defense-in-depth re-validation at apply time.

Round 1 added an ``app/subia/`` prefix refusal to
``app/change_requests/validator.validate()``. That fires at
``create_request`` time. But ``apply_change`` (which runs on operator
approval) only checked ``cr.status == Status.APPROVED`` and never
re-ran the validator. So a CR that was PENDING under an older
(more lenient) policy could land via operator 👍.

There's no immediate exposure (we checked: zero PENDING CRs in the
store target ``app/subia/*`` today), but the architectural gap
matters: validator policies get TIGHTER over time, and the apply path
is the last gate.

This test pins the new re-validation step.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_cr_dict(*, path: str, status: str = "approved"):
    return {
        "id": "cr-probe-id",
        "requestor": "test",
        "path": path,
        "new_content": "x = 1\n",
        "old_content": "",
        "reason": "probe",
        "status": status,
        "decided_by": "react-approve",
    }


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """Redirect the change_requests store to tmp so the test never
    touches the live store."""
    from app.change_requests import store
    store_dir = tmp_path / "change_requests"
    store_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "_STORE_DIR", store_dir)
    monkeypatch.setattr(store, "_AUDIT_LOG", store_dir / "audit.jsonl")
    store.reset_for_tests()
    yield store


def test_apply_refuses_subia_path_at_apply_time(isolated_store, monkeypatch):
    """Even if a CR for app/subia/ somehow lands as APPROVED, the
    apply path must refuse the file write and transition to
    APPLY_FAILED rather than land the change."""
    from app.change_requests import apply, lifecycle, models, store

    # Hand-construct the CR record in the store, bypassing
    # create_request (which would refuse). This simulates a CR that
    # was PENDING under an older policy and got APPROVED before the
    # re-validation gate was added.
    cr = models.ChangeRequest(
        id="cr-subia-probe",
        created_at="2026-05-23T00:00:00+00:00",
        requestor="test",
        path="app/subia/scene/global_workspace.py",
        new_content="# malicious edit\n",
        old_content="",
        reason="probe",
        diff="",
        status=models.Status.APPROVED,
    )
    store.save(cr, audit_event="probe-approved")

    # Bridge would be called if the gate failed — fail noisily if it is.
    fake_bridge = MagicMock()
    fake_bridge.write_file = MagicMock(
        side_effect=AssertionError(
            "bridge.write_file MUST NOT be called for refused paths"
        )
    )
    monkeypatch.setattr(apply, "_get_bridge", lambda: fake_bridge)

    result = apply.apply_change("cr-subia-probe")

    assert not result.ok, "apply must refuse subia paths"
    assert "re-validation refused" in (result.error or "")
    # Bridge was never called — file write didn't happen.
    fake_bridge.write_file.assert_not_called()

    # CR status moved to APPLY_FAILED (re-validation refusal is a
    # legitimate apply failure).
    cr_after = store.get("cr-subia-probe")
    assert cr_after.status == models.Status.APPLY_FAILED


def test_apply_refuses_goal_emitter(isolated_store, monkeypatch):
    """app/affect/goal_emitter.py is the Tier-3 anchor — same gate."""
    from app.change_requests import apply, models, store

    cr = models.ChangeRequest(
        id="cr-emitter-probe",
        created_at="2026-05-23T00:00:00+00:00",
        requestor="test",
        path="app/affect/goal_emitter.py",
        new_content="# probe\n",
        old_content="",
        reason="probe",
        diff="",
        status=models.Status.APPROVED,
    )
    store.save(cr, audit_event="probe-approved")

    fake_bridge = MagicMock()
    fake_bridge.write_file = MagicMock(
        side_effect=AssertionError("must not write goal_emitter")
    )
    monkeypatch.setattr(apply, "_get_bridge", lambda: fake_bridge)

    result = apply.apply_change("cr-emitter-probe")
    assert not result.ok
    assert "re-validation refused" in (result.error or "")
    fake_bridge.write_file.assert_not_called()


def test_apply_proceeds_for_ordinary_path(isolated_store, monkeypatch):
    """Sanity: ordinary app/ paths must STILL apply normally — the
    re-validation must not block legitimate CRs."""
    from app.change_requests import apply, models, store

    cr = models.ChangeRequest(
        id="cr-ok-probe",
        created_at="2026-05-23T00:00:00+00:00",
        requestor="test",
        path="app/agents/probe_module.py",
        new_content="x = 1\n",
        old_content="",
        reason="probe",
        diff="",
        status=models.Status.APPROVED,
    )
    store.save(cr, audit_event="probe-approved")

    fake_bridge = MagicMock()
    # Stub bridge.write_file to succeed, but make the git ops fail
    # so the test doesn't try to actually push a PR. The apply path
    # is "re-validate → write_file → git ops". We only need to prove
    # write_file is REACHED (re-validation didn't refuse).
    fake_bridge.write_file = MagicMock(return_value={"ok": True})
    monkeypatch.setattr(apply, "_get_bridge", lambda: fake_bridge)

    # Stub _run_git_auto_pr so the test stays self-contained.
    fake_git_result = MagicMock()
    fake_git_result.ok = False
    fake_git_result.error = "skipped in test"
    monkeypatch.setattr(apply, "_run_git_auto_pr", lambda **kwargs: fake_git_result)

    result = apply.apply_change("cr-ok-probe")

    # The actual outcome is "git ops failed", which is fine — the
    # important assertion is that write_file WAS called (proves
    # re-validation didn't refuse ordinary path).
    fake_bridge.write_file.assert_called_once()


def test_apply_validator_failure_is_failure_isolated(isolated_store, monkeypatch):
    """If the validator module itself raises (e.g. corruption,
    bad import), the apply must NOT fail-closed — deferring to
    write-time errors keeps the existing behaviour. Pinned so a
    future refactor that hard-fails on validator-raise doesn't
    silently break every apply."""
    from app.change_requests import apply, models, store

    cr = models.ChangeRequest(
        id="cr-ok-probe-2",
        created_at="2026-05-23T00:00:00+00:00",
        requestor="test",
        path="app/agents/probe_module.py",
        new_content="x = 1\n",
        old_content="",
        reason="probe",
        diff="",
        status=models.Status.APPROVED,
    )
    store.save(cr, audit_event="probe-approved")

    # Make validate raise to simulate a broken validator import.
    def broken_validate(**kwargs):
        raise RuntimeError("validator hard-failed in probe")

    monkeypatch.setattr(
        "app.change_requests.validator.validate", broken_validate,
    )

    fake_bridge = MagicMock()
    fake_bridge.write_file = MagicMock(return_value={"ok": True})
    monkeypatch.setattr(apply, "_get_bridge", lambda: fake_bridge)

    fake_git_result = MagicMock()
    fake_git_result.ok = False
    fake_git_result.error = "skipped in test"
    monkeypatch.setattr(apply, "_run_git_auto_pr", lambda **kwargs: fake_git_result)

    # The call must not raise — broken validator means we degrade to
    # the pre-Round-2 behaviour (write-time errors are the safety net).
    result = apply.apply_change("cr-ok-probe-2")
    # write_file was reached — proves we didn't fail-closed on broken validator.
    fake_bridge.write_file.assert_called_once()
