"""external_action_gate — operator gate for external-blast-radius tools.

Built to close the alignment-audit finding (2026-05-23) that DevOps,
Desktop, and PIM agents had tools whose effects reached outside the
sandbox without going through the operator-approval rule the
constitution mandates ("Any output that will be sent externally" must
be human-escalated).

Contract:

    request_external_action(
        *,
        requestor:   str,
        action_type: ActionType,
        summary:     str,
        data:        dict,
        reason:      str,
    ) -> str

Returns a user-facing string describing what happened. Two paths:

1. **Pre-approved** — the (action_type, data) matches an entry in the
   operator's allowlist at workspace/external_action_allowlist.json.
   The function dispatches the handler synchronously and returns its
   result string (typically the same output the original tool would
   have produced).

2. **Gated** — the default path. Creates a PENDING ActionRequest and
   returns a message like "Queued for approval — action_request <id>.
   Awaiting Signal 👍/👎." The agent reports this to the caller; an
   operator approves via Signal reaction or React /cp/changes, the
   apply step runs, and the action_request transitions to APPLIED.

The master switch ``external_action_gate_enabled`` in runtime_settings
governs whether gating runs at all. Default ON. When OFF the function
dispatches the handler synchronously (legacy behavior) — useful for
sandboxed dev environments where Signal interaction would block
automated tests.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from app.action_requests import (
    ActionType,
    create_request,
    get_handler,
)
from app.action_requests.handlers.base import ApplyResult

logger = logging.getLogger(__name__)


_ALLOWLIST_PATH = Path("/app/workspace/external_action_allowlist.json")
_lock = threading.Lock()


# ── Allowlist helpers ───────────────────────────────────────────────────────


def _load_allowlist() -> dict[str, list[dict[str, Any]]]:
    """Load the operator allowlist. Schema::

        {
            "<action_type_value>": [
                {<data_key>: <expected_value>, ...},
                ...
            ],
            ...
        }

    An incoming (action_type, data) is pre-approved when its data dict
    is a superset of one of the entries. Missing file or invalid JSON
    yields an empty allowlist (everything gated).
    """
    if not _ALLOWLIST_PATH.exists():
        return {}
    try:
        with _ALLOWLIST_PATH.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError):
        logger.warning(
            "external_action_gate: allowlist at %s is unreadable; "
            "treating as empty (fail-closed).",
            _ALLOWLIST_PATH,
        )
        return {}
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, list[dict[str, Any]]] = {}
    for key, entries in raw.items():
        if not isinstance(entries, list):
            continue
        cleaned[key] = [e for e in entries if isinstance(e, dict)]
    return cleaned


def is_preapproved(action_type: ActionType, data: dict[str, Any]) -> bool:
    """True when (action_type, data) matches the operator's allowlist."""
    allowlist = _load_allowlist()
    entries = allowlist.get(action_type.value, [])
    for entry in entries:
        if _matches(entry, data):
            return True
    return False


def _matches(entry: dict[str, Any], data: dict[str, Any]) -> bool:
    """True when every key/value in ``entry`` is present in ``data``."""
    for k, v in entry.items():
        if data.get(k) != v:
            return False
    return True


# ── Gate ────────────────────────────────────────────────────────────────────


def _gate_enabled() -> bool:
    """Read the master switch from runtime_settings. Default ON on any
    error so a corrupted state file fails closed (gated)."""
    try:
        from app.runtime_settings import get_external_action_gate_enabled
        return bool(get_external_action_gate_enabled())
    except Exception:
        return True


def request_external_action(
    *,
    requestor: str,
    action_type: ActionType,
    summary: str,
    data: dict[str, Any],
    reason: str,
) -> str:
    """Route an external-blast-radius operation through the operator gate.

    Returns a user-facing string suitable for displaying to the agent
    (which will display it to the caller). Never raises — failures
    are reported in the return string.
    """
    if not _gate_enabled():
        return _dispatch_synchronously(
            action_type=action_type,
            data=data,
            reason="gate-disabled",
        )

    with _lock:
        if is_preapproved(action_type, data):
            return _dispatch_synchronously(
                action_type=action_type,
                data=data,
                reason="pre-approved by allowlist",
            )

        try:
            req = create_request(
                requestor=requestor,
                action_type=action_type,
                summary=summary,
                data=data,
                reason=reason,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "external_action_gate: create_request raised for %s: %s",
                action_type.value, exc, exc_info=True,
            )
            return (
                f"⚠️ External-action gate failed to file request for "
                f"{action_type.value}: {exc}. Action NOT executed."
            )

    if req.status.value == "invalid":
        return (
            f"❌ Refused: action_request validation failed — "
            f"{req.invalid_reason or 'unknown reason'}."
        )

    return (
        f"🔒 Queued for operator approval — action_request {req.id} "
        f"({action_type.value}). Awaiting Signal 👍/👎 or React /cp/changes. "
        f"Action NOT executed yet."
    )


def _dispatch_synchronously(
    *,
    action_type: ActionType,
    data: dict[str, Any],
    reason: str,
) -> str:
    """Apply the handler immediately. Used for the pre-approved and
    gate-disabled paths. Returns a user-facing string."""
    handler = get_handler(action_type)
    if handler is None:
        return (
            f"❌ No handler registered for {action_type.value}; "
            f"cannot dispatch ({reason})."
        )
    try:
        result: ApplyResult = handler.apply(data)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "external_action_gate: handler.apply raised for %s: %s",
            action_type.value, exc, exc_info=True,
        )
        return f"❌ Handler raised: {exc}"
    if not result.ok:
        return f"❌ Action failed ({reason}): {result.error}"
    artifact_str = ""
    if result.artifact:
        try:
            artifact_str = " — " + json.dumps(result.artifact)[:300]
        except (TypeError, ValueError):
            artifact_str = ""
    return f"✓ Executed ({reason}){artifact_str}"


def reset_allowlist_path_for_tests(path: Path | None) -> None:
    """Test-only: redirect the allowlist file to a fixture path."""
    global _ALLOWLIST_PATH
    if path is None:
        _ALLOWLIST_PATH = Path("/app/workspace/external_action_allowlist.json")
    else:
        _ALLOWLIST_PATH = path
