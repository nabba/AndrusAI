"""Escalation + operator resume for BLOCKED executor runs.

Verified Implementation Plan Gap #2 (2026-05-22). The driver can
transition a run to BLOCKED when it can't make progress without
operator input. Before this module, BLOCKED was a dead-end — the
operator could see the state but had no canonical path back to
RUNNING. This file closes that loop:

  * :func:`escalate_blocker` fires when the driver enters BLOCKED.
    Sends a Signal alert (failure-isolated) with the run_id + reason
    and registers the ``signal_ts → run_id`` mapping in a bridge
    file so a Signal reaction OR a typed-phrase reply can later be
    resolved back to the run.
  * :func:`resume_blocker` transitions BLOCKED → RUNNING, records
    the operator's unblock_hint as a step note, and clears the
    bridge entry.

The bridge is JSON-backed at ``workspace/autonomous_executor/
escalation_bridge.json`` and follows the same pattern as
``governance_signal_bridge.py``: typed map, 25h auto-purge, prefix-
match helper, failure-isolated.

REST endpoint :func:`POST /api/cp/delegate/{run_id}/resume` calls
:func:`resume_blocker`. The Signal reaction handler can resolve the
bridge entry and call it the same way.

Why this lives in its own module:
  * Keeps the driver lean — the driver only knows "I'm transitioning
    to BLOCKED" and calls one function.
  * Lets the REST endpoint and the Signal handler share resume code.
  * Keeps the bridge state separate from the run's own state file
    so a bridge corruption can't damage the run's audit chain.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


_BRIDGE_TTL_HOURS = 25
_lock = threading.RLock()


def _workspace_root() -> Path:
    return Path(os.environ.get("WORKSPACE_ROOT", "workspace"))


def _bridge_path() -> Path:
    return _workspace_root() / "autonomous_executor" / "escalation_bridge.json"


# ── Bridge read / write ──────────────────────────────────────────────


def _read_bridge() -> dict[str, dict]:
    """Read the ts→run_id bridge dict. Empty on any failure."""
    path = _bridge_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_bridge(data: dict[str, dict]) -> None:
    """Atomic write. Failure-isolated."""
    path = _bridge_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(data, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError as exc:
        logger.debug("escalation: bridge write failed: %s", exc)


def _purge_stale(data: dict[str, dict]) -> dict[str, dict]:
    """Drop rows older than ``_BRIDGE_TTL_HOURS``. Returns the pruned
    dict (caller writes it back)."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=_BRIDGE_TTL_HOURS)
    kept = {}
    for ts, row in data.items():
        try:
            row_ts = datetime.fromisoformat(
                row.get("emitted_at", "").replace("Z", "+00:00"),
            )
            if row_ts.tzinfo is None:
                row_ts = row_ts.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            # Malformed timestamp → drop the row (failure-isolated)
            continue
        if row_ts >= cutoff:
            kept[ts] = row
    return kept


def register_signal_ts(*, signal_ts: str, run_id: str) -> None:
    """Record the ``signal_ts → run_id`` mapping. Idempotent —
    re-registering the same ts updates the row's emitted_at."""
    with _lock:
        data = _read_bridge()
        data[signal_ts] = {
            "run_id": run_id,
            "emitted_at": datetime.now(timezone.utc).isoformat(),
        }
        data = _purge_stale(data)
        _write_bridge(data)


def resolve_signal_ts(signal_ts: str) -> Optional[str]:
    """Return the run_id for a given Signal timestamp, or None.

    Tolerates prefix-match: a Signal reaction's ts is sometimes a
    truncated version of the original ts. We match the longest
    common prefix to handle that case.
    """
    if not signal_ts:
        return None
    with _lock:
        data = _read_bridge()
        # Exact match first
        if signal_ts in data:
            return data[signal_ts].get("run_id")
        # Prefix match
        for stored_ts, row in data.items():
            if signal_ts.startswith(stored_ts) or stored_ts.startswith(signal_ts):
                return row.get("run_id")
        return None


def clear_signal_ts(signal_ts: str) -> None:
    """Remove a bridge entry. Called after successful resume."""
    with _lock:
        data = _read_bridge()
        if signal_ts in data:
            del data[signal_ts]
            _write_bridge(data)


# ── Escalation alert ────────────────────────────────────────────────


def escalate_blocker(
    *,
    run_id: str,
    reason: str,
    goal_preview: str = "",
    signal_sender: Optional[callable] = None,
) -> None:
    """Fire a Signal alert announcing the BLOCKED state. Idempotent
    end-to-end — calling twice for the same (run_id, reason) just
    re-registers the bridge entry.

    ``signal_sender`` is injectable for tests. Defaults to
    ``app.signal_client.send_message_blocking`` which returns the ts
    of the sent message; we register that ts → run_id so a Signal
    reaction can resume.

    Failure-isolated: a broken Signal client never blocks the run's
    BLOCKED transition (the driver has already committed the state
    change before calling this).
    """
    body = (
        f"⏸ Executor BLOCKED — run {run_id}\n"
        f"Reason: {reason}\n"
    )
    if goal_preview:
        body += f"Goal: {goal_preview}\n"
    body += (
        f"\nResume:\n"
        f"  React 👍 to this message, OR\n"
        f"  POST /api/cp/delegate/{run_id}/resume "
        f"with body {{\"unblock_hint\": \"<your guidance>\"}}, OR\n"
        f"  reply: resume {run_id[:8]} <unblock_hint>"
    )

    if signal_sender is None:
        try:
            from app.signal_client import send_message_blocking
            signal_sender = send_message_blocking
        except Exception:
            logger.debug(
                "escalation: signal_client unavailable; skipping alert",
            )
            return

    try:
        result = signal_sender(body)
    except Exception:
        logger.debug(
            "escalation: signal send raised; bridge not registered",
            exc_info=True,
        )
        return

    # Extract ts from the send result. Different signal-client
    # implementations return different shapes; we tolerate both.
    ts = None
    if isinstance(result, dict):
        ts = result.get("ts") or result.get("timestamp")
    elif isinstance(result, str):
        ts = result
    elif hasattr(result, "ts"):
        ts = getattr(result, "ts", None)

    if ts:
        register_signal_ts(signal_ts=str(ts), run_id=run_id)


# ── Resume ──────────────────────────────────────────────────────────


def resume_blocker(
    *,
    run_id: str,
    unblock_hint: str,
    operator: str = "operator",
    signal_ts: Optional[str] = None,
) -> dict:
    """Transition the run from BLOCKED back to RUNNING.

    Records the operator's ``unblock_hint`` as a run note, clears the
    bridge entry when ``signal_ts`` is supplied, and returns a result
    dict the REST endpoint serialises.

    Returns
    -------
    {"ok": bool, "run_id": str, "status": str, "error": str}
    """
    try:
        from app.autonomous_executor.store import load, save
        from app.autonomous_executor.models import (
            ExecutorStatus, InvalidExecutorTransition,
        )
    except Exception as exc:
        return {
            "ok": False, "run_id": run_id,
            "status": "unknown",
            "error": f"executor modules unavailable: {exc}",
        }

    run = load(run_id)
    if run is None:
        return {
            "ok": False, "run_id": run_id,
            "status": "missing",
            "error": "run not found",
        }

    if run.status is not ExecutorStatus.BLOCKED:
        return {
            "ok": False, "run_id": run_id,
            "status": run.status.value,
            "error": (
                f"run is not BLOCKED (current status: "
                f"{run.status.value!r}); resume only valid from "
                f"BLOCKED"
            ),
        }

    hint = (unblock_hint or "").strip()
    if hint:
        run.record_note(
            f"[resume by {operator}] unblock_hint: {hint}",
        )

    try:
        run.transition(
            ExecutorStatus.RUNNING,
            reason=(
                f"resumed by {operator}"
                + (f": {hint[:140]}" if hint else "")
            ),
        )
    except InvalidExecutorTransition as exc:
        return {
            "ok": False, "run_id": run_id,
            "status": run.status.value,
            "error": str(exc),
        }

    save(run)

    if signal_ts:
        clear_signal_ts(signal_ts)

    return {
        "ok": True, "run_id": run_id,
        "status": run.status.value, "error": "",
    }


__all__ = [
    "clear_signal_ts",
    "escalate_blocker",
    "register_signal_ts",
    "resolve_signal_ts",
    "resume_blocker",
]
