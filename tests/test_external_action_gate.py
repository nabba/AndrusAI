"""Tests for app.external_action_gate — operator gate over external
blast-radius tools.

Closes the alignment-audit findings of 2026-05-23 that DevOps,
Desktop, and PIM agent tools (deploy, github push, SMTP send,
AppleScript, JXA, Shortcuts) previously executed without passing
through the operator-approval rule the constitution mandates.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.action_requests import (
    ActionStatus,
    ActionType,
    list_action_types,
    reset_for_tests,
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch):
    # Isolate the action_requests JSON store + the runtime_settings
    # state so tests don't see each other's leftovers.
    reset_for_tests(tmp_path)
    import app.runtime_settings as rs
    monkeypatch.setattr(rs, "_STATE_PATH", tmp_path / "runtime_settings.json")
    monkeypatch.setattr(rs, "_cache", None, raising=False)

    # Point the allowlist at a temp file; default-empty (everything gated).
    import app.external_action_gate as gate
    allow_path = tmp_path / "external_action_allowlist.json"
    gate.reset_allowlist_path_for_tests(allow_path)

    yield
    gate.reset_allowlist_path_for_tests(None)
    reset_for_tests(None)
    monkeypatch.setattr(rs, "_cache", None, raising=False)


# ── Handler registration ─────────────────────────────────────────────────


def test_six_new_action_types_have_handlers() -> None:
    """Every new ActionType this PR added must have a registered handler.
    Without this, validation refuses the request before it can ever be
    gated."""
    registered = set(list_action_types())
    for at in (
        ActionType.SMTP_SEND,
        ActionType.DEPLOY,
        ActionType.GITHUB_REPO_PUSH,
        ActionType.APPLESCRIPT_EXEC,
        ActionType.JXA_EXEC,
        ActionType.SHORTCUT_RUN,
    ):
        assert at in registered, f"{at.value} has no registered handler"


# ── Gate path: creates a PENDING ActionRequest, does NOT execute ─────────


def test_gate_creates_pending_request_for_smtp_send(monkeypatch):
    """With the gate ON and no allowlist match, request_external_action
    must persist a PENDING ActionRequest and refuse to execute."""
    from app.external_action_gate import request_external_action

    # If the handler's apply() runs, we fail the test — the gate's whole
    # point is that approved-handling happens later, not now.
    from app.action_requests.handlers import smtp_send as smtp_send_mod
    monkeypatch.setattr(
        smtp_send_mod.SmtpSendHandler,
        "apply",
        lambda self, data: pytest.fail("apply must NOT run during gating"),
    )

    msg = request_external_action(
        requestor="test:pim",
        action_type=ActionType.SMTP_SEND,
        summary="📧 SMTP to ops@example.com — “heads up”",
        data={
            "to": "ops@example.com",
            "subject": "heads up",
            "body": "operator status digest",
        },
        reason="test path",
    )
    assert "Queued for operator approval" in msg
    assert "action_request" in msg.lower()

    # Verify a PENDING request exists in the store.
    from app.action_requests import list_all
    pending = [r for r in list_all() if r.status is ActionStatus.PENDING]
    assert len(pending) == 1
    assert pending[0].action_type is ActionType.SMTP_SEND


def test_gate_creates_pending_request_for_deploy():
    from app.external_action_gate import request_external_action
    msg = request_external_action(
        requestor="test:devops",
        action_type=ActionType.DEPLOY,
        summary="🚀 deploy fly: /tmp/myapp",
        data={"project_path": "/tmp/myapp", "target": "fly", "host": "", "deploy_command": ""},
        reason="test path",
    )
    assert "Queued for operator approval" in msg

    from app.action_requests import list_all
    assert any(r.action_type is ActionType.DEPLOY for r in list_all())


def test_gate_creates_pending_request_for_applescript_exec():
    from app.external_action_gate import request_external_action
    msg = request_external_action(
        requestor="test:desktop",
        action_type=ActionType.APPLESCRIPT_EXEC,
        summary="🍎 AppleScript: tell application Mail to send",
        data={"script": "tell application \"Mail\" to send"},
        reason="test path",
    )
    assert "Queued for operator approval" in msg


# ── Allowlist path: pre-approved → dispatch synchronously ────────────────


def test_allowlist_bypasses_gate_for_matching_data(monkeypatch, tmp_path):
    """Operator-pre-approved (action_type, data) combinations skip the
    PENDING queue and execute synchronously."""
    import app.external_action_gate as gate

    # Write an allowlist that pre-approves SHORTCUT_RUN of "MyMorningRoutine".
    allow_path = tmp_path / "external_action_allowlist.json"
    allow_path.write_text(json.dumps({
        "shortcut_run": [{"shortcut_name": "MyMorningRoutine"}],
    }))
    gate.reset_allowlist_path_for_tests(allow_path)

    # Make the shortcut_run handler succeed deterministically.
    from app.action_requests.handlers import shortcut_run as sr_mod
    from app.action_requests.handlers.base import ApplyResult
    monkeypatch.setattr(
        sr_mod.ShortcutRunHandler,
        "apply",
        lambda self, data: ApplyResult(ok=True, artifact={"stdout": "ran"}),
    )

    msg = gate.request_external_action(
        requestor="test:desktop",
        action_type=ActionType.SHORTCUT_RUN,
        summary="⚡ Shortcut: MyMorningRoutine",
        data={"shortcut_name": "MyMorningRoutine", "input": ""},
        reason="test path",
    )
    assert msg.startswith("✓ Executed")
    assert "pre-approved" in msg

    # No PENDING request should have been created on the synchronous path.
    from app.action_requests import list_all
    assert all(r.status is not ActionStatus.PENDING for r in list_all())


def test_allowlist_does_not_match_when_data_differs(monkeypatch, tmp_path):
    """Allowlist match is data-keyed — a different shortcut still goes
    through the gate."""
    import app.external_action_gate as gate
    allow_path = tmp_path / "external_action_allowlist.json"
    allow_path.write_text(json.dumps({
        "shortcut_run": [{"shortcut_name": "MyMorningRoutine"}],
    }))
    gate.reset_allowlist_path_for_tests(allow_path)

    msg = gate.request_external_action(
        requestor="test:desktop",
        action_type=ActionType.SHORTCUT_RUN,
        summary="⚡ Shortcut: SomethingElse",
        data={"shortcut_name": "SomethingElse", "input": ""},
        reason="test path",
    )
    assert "Queued for operator approval" in msg


# ── Master switch OFF: bypasses gate entirely (legacy behavior) ──────────


def test_master_switch_off_bypasses_gate(monkeypatch):
    """When EXTERNAL_ACTION_GATE_ENABLED is False, the gate dispatches
    synchronously without creating an ActionRequest — useful only for
    sandboxed dev where Signal isn't reachable."""
    import app.runtime_settings as rs
    rs.set_external_action_gate_enabled(False)

    from app.action_requests.handlers import smtp_send as smtp_send_mod
    from app.action_requests.handlers.base import ApplyResult
    monkeypatch.setattr(
        smtp_send_mod.SmtpSendHandler,
        "apply",
        lambda self, data: ApplyResult(ok=True, artifact={"recipient": "x@y"}),
    )

    from app.external_action_gate import request_external_action
    msg = request_external_action(
        requestor="test:pim",
        action_type=ActionType.SMTP_SEND,
        summary="📧 SMTP",
        data={"to": "x@y.io", "subject": "s", "body": "b"},
        reason="test path",
    )
    assert msg.startswith("✓ Executed")
    assert "gate-disabled" in msg

    from app.action_requests import list_all
    assert all(r.status is not ActionStatus.PENDING for r in list_all())


# ── Validator integration: bad payload becomes INVALID ───────────────────


def test_invalid_payload_yields_refused_message():
    """A payload that fails the handler's validate() returns an INVALID
    request and a friendly refusal message — never a PENDING request."""
    from app.external_action_gate import request_external_action

    msg = request_external_action(
        requestor="test:pim",
        action_type=ActionType.SMTP_SEND,
        summary="bad SMTP",
        data={"to": "not-an-email", "subject": "s", "body": "b"},
        reason="test path",
    )
    assert msg.startswith("❌ Refused")

    from app.action_requests import list_all
    assert all(r.status is not ActionStatus.PENDING for r in list_all())


# ── Tool integration: send_email goes through gate ───────────────────────


def test_pim_send_email_routes_through_gate(monkeypatch, tmp_path):
    """End-to-end: invoking PIM's SendEmailTool must create a PENDING
    action_request — never call smtplib directly."""
    import smtplib

    def _refuse_smtp(*args, **kwargs):
        pytest.fail("smtplib must NOT be called directly — go through gate")

    monkeypatch.setattr(smtplib, "SMTP", _refuse_smtp)

    # Stub get_settings to look like email is configured (otherwise
    # create_email_tools returns []).
    fake_cfg = type("S", (), {
        "email_enabled": True,
        "email_imap_host": "imap.example.com",
        "email_imap_port": 993,
        "email_smtp_host": "smtp.example.com",
        "email_smtp_port": 587,
        "email_address": "me@example.com",
        "email_password": type("P", (), {"get_secret_value": lambda self: "pw"})(),
    })()
    monkeypatch.setattr("app.config.get_settings", lambda: fake_cfg)

    from app.tools.email_tools import create_email_tools
    tools = create_email_tools("pim-test")
    send_tool = next((t for t in tools if t.name == "send_email"), None)
    assert send_tool is not None, "send_email tool should be created"

    result = send_tool._run(
        to="ops@example.com",
        subject="weekly digest",
        body="all systems green",
    )
    assert "Queued for operator approval" in result

    from app.action_requests import list_all
    pending = [r for r in list_all() if r.status is ActionStatus.PENDING]
    assert len(pending) == 1
    assert pending[0].action_type is ActionType.SMTP_SEND
    assert pending[0].data["to"] == "ops@example.com"


# ── Allowlist resilience ─────────────────────────────────────────────────


def test_corrupted_allowlist_fails_closed(monkeypatch, tmp_path):
    """A malformed allowlist file MUST NOT silently approve everything —
    it falls back to empty (everything gated)."""
    import app.external_action_gate as gate
    allow_path = tmp_path / "external_action_allowlist.json"
    allow_path.write_text("{ this is not valid json")
    gate.reset_allowlist_path_for_tests(allow_path)

    msg = gate.request_external_action(
        requestor="test:devops",
        action_type=ActionType.DEPLOY,
        summary="🚀 deploy fly",
        data={"project_path": "/x", "target": "fly", "host": "", "deploy_command": ""},
        reason="test path",
    )
    assert "Queued for operator approval" in msg


def test_allowlist_requires_exact_match_for_extra_keys(tmp_path):
    """Allowlist entries are treated as required-key subsets: data must
    contain every key in the entry with matching values. Extra data
    keys are fine; extra entry keys are not."""
    import app.external_action_gate as gate
    allow_path = tmp_path / "external_action_allowlist.json"
    allow_path.write_text(json.dumps({
        "deploy": [{"target": "ghpages"}],
    }))
    gate.reset_allowlist_path_for_tests(allow_path)

    # Matches: target=ghpages required, project_path is extra (OK).
    assert gate.is_preapproved(
        ActionType.DEPLOY,
        {"target": "ghpages", "project_path": "/x"},
    )
    # Does NOT match: target=fly differs.
    assert not gate.is_preapproved(
        ActionType.DEPLOY,
        {"target": "fly", "project_path": "/x"},
    )
