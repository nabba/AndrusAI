"""Regression pins for the 2026-05-23 alignment-audit findings.

The weekly ``alignment_audit.py`` LLM-driven scan produced a drift
score of 0.40 (right at the critical threshold) with three concrete
findings:

  1. DevOps and Desktop agents operate tools (SSH, cloud deployments,
     GitHub pushes, AppleScript system events) that bypass the
     constitutional rule against executing code outside the
     designated sandbox.

  2. The strict escalation rule requiring human approval for "any
     output that will be sent externally" is not programmatically
     enforced for DevOps deployment tools or PIM email-send tools.

  3. The Concierge agent's mandate to smooth/reword outputs for a
     natural conversational feel risks stripping or diluting the
     mandatory raw epistemic labels (``[Inference]``,
     ``[Unverified]``, ``[Speculation]``) required by the labeling
     protocol.

Each finding is closed by code that is easy to undo by accident in a
later refactor. The tests in this file pin the load-bearing behavior
so that an unrelated change that brings back any of these gaps fails
CI immediately with a message that points at this audit.

If a test here fails:
  - Read app/souls/constitution.md and the audit context above.
  - Do NOT delete the pin. Either restore the gate or document
    explicit operator approval and refactor the pin to match the
    new architecture (with the same failure-loudness).
"""
from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.action_requests import (
    ActionStatus,
    ActionType,
    list_action_types,
    list_all,
    reset_for_tests,
)


# ── Shared isolation ───────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch):
    reset_for_tests(tmp_path)
    import app.runtime_settings as rs
    monkeypatch.setattr(rs, "_STATE_PATH", tmp_path / "runtime_settings.json")
    monkeypatch.setattr(rs, "_cache", None, raising=False)

    import app.external_action_gate as gate
    gate.reset_allowlist_path_for_tests(tmp_path / "external_action_allowlist.json")

    yield

    gate.reset_allowlist_path_for_tests(None)
    reset_for_tests(None)
    monkeypatch.setattr(rs, "_cache", None, raising=False)


def _stub_email_config(monkeypatch) -> None:
    """Pretend email is configured so the PIM tool factory returns its tools."""
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


# ────────────────────────────────────────────────────────────────────────
# FINDING 1 + 2 — External-blast-radius tools must route through the
# operator gate. Each tool's _run() must NOT execute directly; it must
# produce a PENDING action_request.
# ────────────────────────────────────────────────────────────────────────


def _refuse_with_audit(*_args, **_kwargs):  # noqa: ANN001
    pytest.fail(
        "ALIGNMENT AUDIT 2026-05-23 REGRESSION: a gated tool called the "
        "underlying transport directly instead of routing through "
        "app.external_action_gate.request_external_action(). Findings 1+2 "
        "are open again — see app/souls/constitution.md and the "
        "docstring of this test file."
    )


def test_finding_2_pim_send_email_does_not_call_smtplib_directly(monkeypatch):
    """PIM SendEmailTool MUST NOT call smtplib.SMTP() directly; the
    actual SMTP transmission only happens inside the SmtpSendHandler
    after the operator approves the queued action_request."""
    import smtplib
    monkeypatch.setattr(smtplib, "SMTP", _refuse_with_audit)
    _stub_email_config(monkeypatch)

    from app.tools.email_tools import create_email_tools
    tools = create_email_tools("pim-test")
    send_tool = next(t for t in tools if t.name == "send_email")

    result = send_tool._run(
        to="audit@example.com",
        subject="regression pin",
        body="this must NOT hit SMTP directly",
    )
    assert "Queued for operator approval" in result, (
        "PIM send_email did NOT route through the gate — audit finding 2 regressed."
    )


def test_finding_1_devops_github_push_does_not_execute_bridge_directly(monkeypatch):
    """DevOps github_create_and_push must queue an action_request, not
    invoke `gh repo create` via the host bridge directly."""
    fake_bridge = MagicMock()
    fake_bridge.is_available.return_value = True
    fake_bridge.execute = _refuse_with_audit
    monkeypatch.setattr("app.bridge_client.get_bridge", lambda agent_id: fake_bridge)

    from app.tools.deployment_tools import create_deployment_tools
    tools = create_deployment_tools("devops-test")
    push_tool = next(t for t in tools if t.name == "github_create_and_push")

    result = push_tool._run(
        name="myorg/regression-pin",
        description="audit regression check",
        private=True,
        project_path="/tmp/whatever",
    )
    assert "Queued for operator approval" in result, (
        "DevOps github_create_and_push did NOT route through the gate "
        "— audit finding 1 regressed."
    )


def test_finding_1_devops_deploy_does_not_execute_bridge_directly(monkeypatch):
    """DevOps deploy must queue an action_request, not run fly/rsync/ssh
    via bridge.execute directly."""
    fake_bridge = MagicMock()
    fake_bridge.is_available.return_value = True
    fake_bridge.execute = _refuse_with_audit
    monkeypatch.setattr("app.bridge_client.get_bridge", lambda agent_id: fake_bridge)

    from app.tools.deployment_tools import create_deployment_tools
    tools = create_deployment_tools("devops-test")
    deploy_tool = next(t for t in tools if t.name == "deploy")

    result = deploy_tool._run(
        project_path="/tmp/project",
        target="fly",
    )
    assert "Queued for operator approval" in result, (
        "DevOps deploy did NOT route through the gate — audit finding 1 regressed."
    )


def test_finding_1_desktop_applescript_does_not_execute_bridge_directly(monkeypatch):
    """Desktop run_applescript must queue an action_request. AppleScript
    can drive Mail, System Events, etc. — all external blast radius."""
    fake_bridge = MagicMock()
    fake_bridge.is_available.return_value = True
    fake_bridge.execute = _refuse_with_audit
    monkeypatch.setattr("app.bridge_client.get_bridge", lambda agent_id: fake_bridge)

    from app.tools.desktop_tools import create_desktop_tools
    tools = create_desktop_tools("desktop-test")
    applescript_tool = next(t for t in tools if t.name == "run_applescript")

    result = applescript_tool._run(
        script='tell application "Mail" to send the first outgoing message',
    )
    assert "Queued for operator approval" in result, (
        "Desktop run_applescript did NOT route through the gate — "
        "audit finding 1 regressed."
    )


def test_finding_1_desktop_jxa_does_not_execute_bridge_directly(monkeypatch):
    """Desktop run_jxa must queue an action_request (same blast radius as
    AppleScript)."""
    fake_bridge = MagicMock()
    fake_bridge.is_available.return_value = True
    fake_bridge.execute = _refuse_with_audit
    monkeypatch.setattr("app.bridge_client.get_bridge", lambda agent_id: fake_bridge)

    from app.tools.desktop_tools import create_desktop_tools
    tools = create_desktop_tools("desktop-test")
    jxa_tool = next(t for t in tools if t.name == "run_jxa")

    result = jxa_tool._run(script='Application("Mail").send()')
    assert "Queued for operator approval" in result, (
        "Desktop run_jxa did NOT route through the gate — audit finding 1 regressed."
    )


def test_finding_1_desktop_shortcut_does_not_execute_bridge_directly(monkeypatch):
    """Desktop run_shortcut must queue an action_request. Shortcuts can
    chain external actions (send messages, post to socials, run scripts)."""
    fake_bridge = MagicMock()
    fake_bridge.is_available.return_value = True
    fake_bridge.execute = _refuse_with_audit
    monkeypatch.setattr("app.bridge_client.get_bridge", lambda agent_id: fake_bridge)

    from app.tools.desktop_tools import create_desktop_tools
    tools = create_desktop_tools("desktop-test")
    shortcut_tool = next(t for t in tools if t.name == "run_shortcut")

    result = shortcut_tool._run(name="SendImportantEmail")
    assert "Queued for operator approval" in result, (
        "Desktop run_shortcut did NOT route through the gate — "
        "audit finding 1 regressed."
    )


# Coverage pins — if someone adds a new ActionType but forgets the
# handler (or vice versa), this catches it before runtime does.

_REQUIRED_GATED_TYPES = (
    ActionType.SMTP_SEND,
    ActionType.DEPLOY,
    ActionType.GITHUB_REPO_PUSH,
    ActionType.APPLESCRIPT_EXEC,
    ActionType.JXA_EXEC,
    ActionType.SHORTCUT_RUN,
)


def test_all_gated_action_types_have_registered_handlers():
    """Each external-blast ActionType must be backed by a handler. A
    missing handler means validate() refuses the request before it can
    be queued — the gate would silently degrade to "everything rejected"
    instead of "everything queued for approval"."""
    registered = set(list_action_types())
    for t in _REQUIRED_GATED_TYPES:
        assert t in registered, (
            f"ALIGNMENT AUDIT 2026-05-23 REGRESSION: ActionType {t.value!r} "
            f"has no registered handler. The gate cannot queue requests for "
            f"this type — audit findings 1+2 are partially open."
        )


def test_gate_creates_pending_request_end_to_end():
    """Smoke test of the gate itself: a routed call must create exactly
    one PENDING action_request and produce a user-facing queue message."""
    from app.external_action_gate import request_external_action

    msg = request_external_action(
        requestor="audit-pin",
        action_type=ActionType.DEPLOY,
        summary="🚀 deploy fly: /tmp/x",
        data={"project_path": "/tmp/x", "target": "fly", "host": "", "deploy_command": ""},
        reason="audit regression pin",
    )
    assert "Queued for operator approval" in msg

    pending = [r for r in list_all() if r.status is ActionStatus.PENDING]
    assert len(pending) == 1, (
        "ALIGNMENT AUDIT 2026-05-23 REGRESSION: routed call did not "
        "produce exactly one PENDING action_request. The operator gate "
        "is bypassed somewhere."
    )


# ────────────────────────────────────────────────────────────────────────
# FINDING 3 — Concierge wrapper must preserve epistemic labels.
# Two-layer defense:
#   (a) system prompt mandates preservation
#   (b) post-validation falls back when labels are dropped
# ────────────────────────────────────────────────────────────────────────


def test_finding_3_concierge_prompt_names_all_three_labels():
    """The system prompt is the first line of defense. Removing any
    label from the prompt re-opens audit finding 3."""
    from app.personality.concierge_wrapper import _SYSTEM_PROMPT

    for label in ("[Inference]", "[Speculation]", "[Unverified]"):
        assert label in _SYSTEM_PROMPT, (
            f"ALIGNMENT AUDIT 2026-05-23 REGRESSION: the concierge "
            f"system prompt no longer names {label!r}. The rewriter "
            f"is back to silently stripping it — audit finding 3 is open."
        )


def test_finding_3_label_validator_function_exists_and_works():
    """The post-validation guard is the second line of defense. Removing
    it (or rewriting it to always-pass) re-opens audit finding 3."""
    from app.personality import concierge_wrapper

    assert hasattr(concierge_wrapper, "_epistemic_labels_preserved"), (
        "ALIGNMENT AUDIT 2026-05-23 REGRESSION: "
        "_epistemic_labels_preserved() has been removed. The concierge "
        "wrapper no longer post-validates that labels survive the rewrite."
    )

    fn = concierge_wrapper._epistemic_labels_preserved
    # Function must distinguish drop from preserve (not always-true).
    assert fn("[Inference] X", "[Inference] X"), "preserve case must return True"
    assert not fn("[Inference] X", "X"), (
        "ALIGNMENT AUDIT 2026-05-23 REGRESSION: "
        "_epistemic_labels_preserved is always-true (no longer detects "
        "dropped labels). Audit finding 3 is open."
    )


def test_finding_3_label_guard_called_from_rewrite_path():
    """The validator must be invoked from ``_rewrite_with_llm``; an
    orphaned helper that nothing calls is the same as no guard."""
    from app.personality import concierge_wrapper
    source = inspect.getsource(concierge_wrapper._rewrite_with_llm)
    assert "_epistemic_labels_preserved" in source, (
        "ALIGNMENT AUDIT 2026-05-23 REGRESSION: _rewrite_with_llm no "
        "longer calls _epistemic_labels_preserved. The guard exists but "
        "is dead code — audit finding 3 is open."
    )


def test_finding_3_end_to_end_drop_triggers_fallback(monkeypatch):
    """End-to-end behavior pin: an LLM rewrite that strips `[Inference]`
    MUST cause apply_concierge to return the original text unchanged."""
    import app.runtime_settings as rs
    rs.set_concierge_persona_enabled(True)

    class _FakeContentBlock:
        type = "text"
        def __init__(self, text):
            self.text = text

    class _FakeResponse:
        def __init__(self, text):
            self.content = [_FakeContentBlock(text)]

    class _FakeClient:
        def __init__(self, **kwargs):
            self.messages = MagicMock()
            self.messages.create = self._create

        def _create(self, **kw):
            # Strip the label deliberately — simulates exactly the
            # failure mode the audit warned about.
            return _FakeResponse(
                "Latency on /api/cp/budgets is up about 35% in the last hour. "
                "The 4xx burst correlates with the Anthropic provider rotation."
            )

    monkeypatch.setattr("anthropic.Anthropic", _FakeClient)
    monkeypatch.setattr(
        "app.personality.concierge_wrapper.get_anthropic_api_key",
        lambda: "sk-test",
    )

    original = (
        "Latency on /api/cp/budgets climbed about 35% in the last hour. "
        "[Inference] The 4xx burst correlates with the Anthropic provider "
        "rotation that started at 14:02 UTC."
    )

    from app.personality.concierge_wrapper import apply_concierge
    result = apply_concierge(original)
    assert result == original, (
        "ALIGNMENT AUDIT 2026-05-23 REGRESSION: apply_concierge returned a "
        "rewrite that dropped the [Inference] label. The post-validation "
        "guard is not stopping label-stripping rewrites — audit finding 3 "
        "is open. Unverified content is being laundered through the "
        "concierge layer as if it were verified fact."
    )


# ────────────────────────────────────────────────────────────────────────
# Bonus pin — the master switch defaults to ON. If anyone flips the
# default to OFF, every gated tool reverts to direct execution silently.
# ────────────────────────────────────────────────────────────────────────


def test_external_action_gate_master_switch_defaults_on(monkeypatch, tmp_path):
    """A fresh runtime_settings.json (or none at all) must report the
    gate as enabled. Default-OFF would silently re-open findings 1+2."""
    import app.runtime_settings as rs
    # Force a fresh load — no existing state file.
    monkeypatch.setattr(rs, "_STATE_PATH", tmp_path / "fresh.json")
    monkeypatch.setattr(rs, "_cache", None, raising=False)
    assert rs.get_external_action_gate_enabled() is True, (
        "ALIGNMENT AUDIT 2026-05-23 REGRESSION: "
        "external_action_gate_enabled default flipped to False. Every "
        "gated tool reverts to direct execution — findings 1+2 reopen "
        "for every fresh deployment."
    )
