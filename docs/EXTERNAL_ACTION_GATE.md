# External Action Gate

Operator gate for tools whose effects reach outside the BotArmy
sandbox. Routes DevOps deploys, GitHub pushes, PIM email sends, and
macOS scripting (AppleScript / JXA / Shortcuts) through
[`app/action_requests/`](../app/action_requests/) so the operator
approves each external action before it executes.

Closes alignment-audit findings #1 and #2 of 2026-05-23 (PROGRAM §64).

---

## Why this exists

The constitution at [`app/souls/constitution.md`](../app/souls/constitution.md)
sets two absolute rules:

> Never execute code or commands that modify systems outside the
> designated sandbox.

> Any output that will be sent externally (emails, public posts,
> financial documents) requires human escalation.

The weekly LLM alignment audit ([`app/alignment_audit.py`](../app/alignment_audit.py))
flagged that the DevOps agent's `deploy` / `github_create_and_push`,
the PIM agent's `send_email`, and the Desktop agent's
`run_applescript` / `run_jxa` / `run_shortcut` tools all executed
synchronously without consulting the operator — those rules were
constraints in prose, not enforced in code.

The external-action gate makes the constraint **programmatic**.

---

## Contract

```python
from app.action_requests.models import ActionType
from app.external_action_gate import request_external_action

result_str = request_external_action(
    requestor   = "devops:my-agent-id",
    action_type = ActionType.DEPLOY,
    summary     = "🚀 deploy fly: /tmp/myapp",
    data        = {"project_path": "/tmp/myapp", "target": "fly",
                   "host": "", "deploy_command": ""},
    reason      = "Operator-requested deploy of pre-PR validation app.",
)
```

The function never raises. Three return paths:

| Path                  | Returns                                                                                                   | When                                                                          |
| --------------------- | --------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| **Gated** (default)   | `🔒 Queued for operator approval — action_request <id> (<type>). Awaiting Signal 👍/👎 or React /cp/changes.` | Master switch ON and (action_type, data) does not match the allowlist.        |
| **Pre-approved**      | `✓ Executed (pre-approved by allowlist) — <artifact>`                                                       | Operator's allowlist matches; handler runs synchronously.                     |
| **Master switch off** | `✓ Executed (gate-disabled) — <artifact>`                                                                   | `external_action_gate_enabled = false` (dev/test scenarios only).             |
| **Invalid payload**   | `❌ Refused: action_request validation failed — <reason>`                                                   | Handler's `validate()` rejected the data shape.                               |

A queued request transitions through the standard `action_requests`
lifecycle:

```
PENDING ─┬─→ APPROVED ──→ APPLIED        (operator 👍 → handler runs)
         ├─→ REJECTED                    (operator 👎)
         ├─→ TIMEOUT                     (operator silence past TTL)
         └─→ INVALID                     (validate() rejected pre-gate)
```

Operator approval surfaces — same as for `email_draft` /
`calendar_invite` / `signal_send`:

- Signal 👍/👎 reaction on the queue notification
- React `/cp/changes` Approve / Reject buttons
- CLI text command (`approve <id-prefix>`)

---

## ActionTypes

Six new `ActionType` enum values map to the six gated tools:

| Tool (file)                                              | ActionType         | Handler                                                                    |
| -------------------------------------------------------- | ------------------ | -------------------------------------------------------------------------- |
| `send_email`                  ([email_tools.py](../app/tools/email_tools.py))             | `SMTP_SEND`        | [smtp_send.py](../app/action_requests/handlers/smtp_send.py)               |
| `deploy`                      ([deployment_tools.py](../app/tools/deployment_tools.py))   | `DEPLOY`           | [deploy.py](../app/action_requests/handlers/deploy.py)                     |
| `github_create_and_push`      ([deployment_tools.py](../app/tools/deployment_tools.py))   | `GITHUB_REPO_PUSH` | [github_repo_push.py](../app/action_requests/handlers/github_repo_push.py) |
| `run_applescript`             ([desktop_tools.py](../app/tools/desktop_tools.py))         | `APPLESCRIPT_EXEC` | [applescript_exec.py](../app/action_requests/handlers/applescript_exec.py) |
| `run_jxa`                     ([desktop_tools.py](../app/tools/desktop_tools.py))         | `JXA_EXEC`         | [jxa_exec.py](../app/action_requests/handlers/jxa_exec.py)                 |
| `run_shortcut`                ([desktop_tools.py](../app/tools/desktop_tools.py))         | `SHORTCUT_RUN`     | [shortcut_run.py](../app/action_requests/handlers/shortcut_run.py)         |

Each handler `apply()` step replays the exact transport call the
original tool used to make (bridge.execute / smtplib) — the gate
postpones execution, it doesn't change semantics.

**Intentionally NOT gated** (covered explicitly in the audit-scope
decision):

| Tool                       | Reason                                                          |
| -------------------------- | --------------------------------------------------------------- |
| `docker_build`             | Builds local image; no push / no external transmission.         |
| `screen_capture`           | Writes to local workspace dir.                                  |
| `clipboard`                | Local OS clipboard, no transmission.                            |
| `manage_window`            | Local window-manager op.                                        |
| `open_on_mac`              | Opens app/URL locally. Navigation is not external transmission. |
| `check_email` / `read_email` / `search_email` / `organize_email` | Inbound IMAP, not outbound.                                     |

---

## Allowlist

Operator-pre-approved (action_type, data) combinations skip the
PENDING queue and dispatch synchronously. File:

```
workspace/external_action_allowlist.json
```

Schema:

```jsonc
{
  "<action_type_value>": [
    {<data_key>: <expected_value>, ...},
    {<data_key>: <expected_value>, ...}
  ],
  ...
}
```

Matching is **required-key subset**: every key/value in an allowlist
entry must appear in the incoming `data` dict with the same value;
extra keys in `data` are ignored. Examples:

```jsonc
{
  // Pre-approve any deploy to GitHub Pages (all targets, paths,
  // hosts):
  "deploy": [
    {"target": "ghpages"}
  ],

  // Pre-approve only the "MyMorningRoutine" shortcut:
  "shortcut_run": [
    {"shortcut_name": "MyMorningRoutine"}
  ],

  // Pre-approve emails to one specific recipient with a fixed
  // subject (useful for scheduled status digests):
  "smtp_send": [
    {"to": "ops@example.com", "subject": "weekly status"}
  ]
}
```

Default state: **file does not exist** → empty allowlist → every
external action goes through the operator gate.

**Fail-closed**: a malformed allowlist JSON is treated as empty.
A corrupted file cannot silently bypass the gate (pinned by
`test_corrupted_allowlist_fails_closed`).

---

## Master switch

```
runtime_settings.external_action_gate_enabled : bool   # default True
```

`get_external_action_gate_enabled()` /
`set_external_action_gate_enabled(value)` in
[`app/runtime_settings.py`](../app/runtime_settings.py).

When **False**, `request_external_action()` dispatches synchronously
without creating an ActionRequest — equivalent to the legacy
pre-gate behavior. Intended only for sandboxed dev where Signal /
React aren't reachable and would otherwise block test runs.

The default-ON invariant is a regression pin
(`test_external_action_gate_master_switch_defaults_on`): flipping
the default to False would silently re-open audit findings 1+2 on
every fresh deployment.

---

## Operator workflow

1. Agent invokes a gated tool — `send_email`, `deploy`, etc.
2. Tool returns `"🔒 Queued for operator approval — action_request
   <id>..."` to the agent (which surfaces to the user).
3. Signal notification fires (via the existing `action_request`
   alert mechanism) and a row appears in React `/cp/changes`.
4. Operator reacts 👍 (or clicks Approve) → handler's `apply()` runs.
5. Operator reacts 👎 (or clicks Reject) → request → `REJECTED`,
   never executes.
6. No response within TTL → request → `TIMEOUT`.

For frequently-repeated low-risk actions (e.g. nightly digest
emails, GitHub Pages deploys of a personal site), add an entry to
`workspace/external_action_allowlist.json` to bypass the gate.

---

## Concierge label preservation (audit finding 3)

Tracked in this doc because it's part of the same alignment-audit
response, but lives in
[`app/personality/concierge_wrapper.py`](../app/personality/concierge_wrapper.py)
rather than the gate.

Two-layer defense for the constitution's labeling protocol:

1. **System prompt** mandates verbatim preservation of `[Inference]`,
   `[Speculation]`, and `[Unverified]` labels.
2. **Post-validation** (`_epistemic_labels_preserved`) counts each
   label in the input vs the rewritten output. Count-based, not
   presence-based — three `[Inference]` claims must remain three
   `[Inference]` claims. Case-insensitive. On any drop, the wrapper
   returns the original (same fallback pattern as the existing
   2× length guard).

---

## Tests

- [tests/test_external_action_gate.py](../tests/test_external_action_gate.py) — 11 tests (gate creates PENDING, allowlist bypass, master-switch-off bypass, invalid-payload refusal, end-to-end PIM `send_email`, fail-closed on corrupted allowlist).
- [tests/test_concierge_label_preservation.py](../tests/test_concierge_label_preservation.py) — 10 tests (helper unit cases + end-to-end fallback).
- [tests/test_alignment_audit_2026_05_23_findings_closed.py](../tests/test_alignment_audit_2026_05_23_findings_closed.py) — **13 regression pins**. Each failure message begins with `ALIGNMENT AUDIT 2026-05-23 REGRESSION:` so a future dev tripping on it can grep their way to this doc and the audit context without context-switching to git history.

Run the regression pins specifically:

```
pytest tests/test_alignment_audit_2026_05_23_findings_closed.py -v
```

---

## Adding a new gated action

1. Add an `ActionType` enum value in [`app/action_requests/models.py`](../app/action_requests/models.py).
2. Create a handler under [`app/action_requests/handlers/`](../app/action_requests/handlers/) (subclass `ActionHandler`, implement `validate` / `apply` / `render_summary`).
3. Register it in [`handlers/__init__.py`](../app/action_requests/handlers/__init__.py) (the auto-register loop already covers six known handlers; add yours to the tuple).
4. Modify the originating tool to call `request_external_action(...)` instead of executing directly.
5. Add a regression pin to `test_alignment_audit_2026_05_23_findings_closed.py` matching the existing tool-level pattern.

If the new action involves a different blast-radius class (DB writes? cluster commands? something the constitution would treat as a fourth category), update the constitution AND the alignment audit's known-roles document so the LLM auditor recognises the new category.
