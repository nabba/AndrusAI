"""Signal-timestamp ↔ epistemic-gate-context bridge (Stage F, 2026-05-26).

Closes the operator-feedback gap in the epistemic stack. Until now,
gate verdicts were one-way: the gate revised/blocked a reply, the user
read the result, and nothing fed back into the bias library or
calibration. With this bridge, a 👎 reaction on a reply becomes a
structured signal the system learns from.

Architecture (mirrors :mod:`app.governance_signal_bridge` exactly):

* JSON sidecar at ``workspace/epistemic_reaction_bridge.json``.
* Threading-locked read/write.
* 25-hour TTL — entries past that age are purged on next access.
* Bounded growth — soft cap at ``_MAX_ENTRIES`` (default 5,000).
* Idempotent registration: same ``signal_ts`` overwrites (the latest
  context is what the operator reacted to anyway).

Used by:

  * ``app.main`` reaction handler — calls :func:`find_context` to
    resolve the reacted-to timestamp back to its gate context.
  * ``app.main`` reply dispatch — calls :func:`register` immediately
    after ``send_durable`` returns the Signal timestamp.

Why a JSON sidecar instead of a Postgres column:
  * No new schema migration (Postgres migrations aren't auto-applied
    at boot — same constraint that drove the governance bridge design).
  * Map is purely a routing aid; loss of the file just means a 👎
    silently no-ops (the user can re-engage via slash command).
  * 25h TTL keeps the file small and load fast — no compaction logic
    needed.

When :func:`handle_reaction` is called with ``"👎"``:

  * If the gate revised/blocked this reply →
    :func:`app.epistemic.override.record_override` with
    ``user_action=FORCE_PROCEED`` (operator disagrees with the
    intervention — restore the original).
  * If the gate shipped this reply →
    :func:`record_disagreement` (operator thinks the gate *should* have
    intervened). Feeds the advisory report's "operator pushback" signal.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_MAX_AGE_SECONDS = 25 * 3600
_MAX_ENTRIES = 5_000
_LOCK = threading.Lock()


def _bridge_path() -> Path:
    from app.paths import WORKSPACE_ROOT
    return WORKSPACE_ROOT / "epistemic_reaction_bridge.json"


def _disagreement_path() -> Path:
    from app.paths import WORKSPACE_ROOT
    return WORKSPACE_ROOT / "epistemic" / "operator_disagreements.jsonl"


def _load() -> dict[str, dict[str, Any]]:
    p = _bridge_path()
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text() or "{}")
    except Exception:
        logger.debug("reaction_bridge: load failed; starting fresh", exc_info=True)
        return {}
    if not isinstance(raw, dict):
        return {}
    # Purge stale entries while we're here.
    cutoff = time.time() - _MAX_AGE_SECONDS
    purged = {
        k: v for k, v in raw.items()
        if isinstance(v, dict) and float(v.get("registered_at", 0)) >= cutoff
    }
    return purged


def _save(state: dict[str, dict[str, Any]]) -> None:
    p = _bridge_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        # Bound the size on the way out — keeps the JSON load cheap.
        if len(state) > _MAX_ENTRIES:
            # Drop oldest by registered_at.
            items = sorted(
                state.items(),
                key=lambda kv: float(kv[1].get("registered_at", 0)),
                reverse=True,
            )
            state = dict(items[:_MAX_ENTRIES])
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, default=str))
        tmp.replace(p)
    except Exception:
        logger.debug("reaction_bridge: save failed", exc_info=True)


def register(
    signal_ts: int,
    *,
    task_id: str,
    gate_action: str = "ship",
    user_visible_reason: str = "",
    reply_preview: str = "",
) -> None:
    """Record context for a reply just sent to Signal. Never raises.

    ``signal_ts`` is the value returned by :func:`send_durable` — the
    Signal-message timestamp the operator's reaction will reference."""
    if not signal_ts or not task_id:
        return
    try:
        with _LOCK:
            state = _load()
            state[str(signal_ts)] = {
                "task_id": task_id[:128],
                "gate_action": gate_action,
                "user_visible_reason": (user_visible_reason or "")[:300],
                "reply_preview": (reply_preview or "")[:300],
                "registered_at": time.time(),
            }
            _save(state)
    except Exception:
        logger.debug("reaction_bridge: register failed", exc_info=True)


def find_context(signal_ts: int) -> Optional[dict[str, Any]]:
    """Resolve a Signal timestamp back to its gate context. Returns
    ``None`` if the entry is missing or has expired."""
    if not signal_ts:
        return None
    try:
        with _LOCK:
            state = _load()
            entry = state.get(str(signal_ts))
            return dict(entry) if entry else None
    except Exception:
        logger.debug("reaction_bridge: find_context failed", exc_info=True)
        return None


def record_disagreement(
    *,
    task_id: str,
    gate_action: str,
    user_visible_reason: str,
    reply_preview: str,
    sender: str = "",
) -> None:
    """Append a 👎-pushback entry to the disagreement ledger.

    Used when the gate shipped a reply (no intervention) but the
    operator thinks it shouldn't have. Feeds the advisory report's
    operator-pushback signal — the inverse of an OverrideEvent."""
    try:
        path = _disagreement_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": time.time(),
            "task_id": task_id[:128],
            "gate_action": gate_action,
            "user_visible_reason": (user_visible_reason or "")[:300],
            "reply_preview": (reply_preview or "")[:300],
            "sender": (sender or "")[:128],
        }
        try:
            from app.safe_io import append_with_cap
            append_with_cap(path, json.dumps(row, default=str) + "\n", 10_000)
        except Exception:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, default=str) + "\n")
    except Exception:
        logger.debug("reaction_bridge: record_disagreement failed", exc_info=True)


def handle_reaction(
    signal_ts: int,
    emoji: str,
    *,
    sender: str = "",
) -> Optional[str]:
    """Dispatch a 👍/👎 reaction to the right downstream sink.

    Returns a short human-readable acknowledgement string the caller
    can echo to Signal, or ``None`` if the reaction is uninteresting
    (👍 or no context known)."""
    if emoji not in ("👎", "-1"):
        return None  # Only 👎 is actionable for the epistemic feedback loop.
    ctx = find_context(signal_ts)
    if not ctx:
        return None

    task_id = ctx.get("task_id") or ""
    gate_action = ctx.get("gate_action") or "ship"
    user_visible_reason = ctx.get("user_visible_reason") or ""
    reply_preview = ctx.get("reply_preview") or ""

    # The bridge only knows what main.py recorded at send time. The
    # gate's actual verdict for this task lives in verdict_telemetry —
    # consult it as the source of truth so 👎 routes to record_override
    # vs record_disagreement based on what really happened. Failure-
    # isolated; the bridge's own values are the fallback.
    try:
        from app.epistemic.verdict_telemetry import latest_verdict_for_task
        latest = latest_verdict_for_task(task_id)
        if latest:
            gate_action = str(latest.get("action") or gate_action)
            user_visible_reason = str(
                latest.get("user_visible_reason") or user_visible_reason,
            )
    except Exception:
        logger.debug("reaction_bridge: verdict lookup failed", exc_info=True)

    if gate_action in ("revise", "block"):
        # Operator disagrees with the gate's intervention. Existing
        # primitive: record_override(user_action=FORCE_PROCEED). The
        # Self-Improver flush is opt-in inside record_override and
        # default-on, so calibration sees this on the next pass.
        try:
            from app.epistemic.override import (
                OverrideAction, record_override,
            )
            record_override(
                task_id=task_id,
                blocked_action=gate_action,
                user_action=OverrideAction.FORCE_PROCEED,
                user_reasoning="signal_thumbs_down on a gate-intervened reply",
            )
            return (
                f"✓ Recorded override on task {task_id[:16]}: gate "
                f"{gate_action} → restore. Calibration will see this."
            )
        except Exception:
            logger.debug("reaction_bridge: record_override failed", exc_info=True)
            return None

    # Default: gate shipped. Record as operator-disagreement (gate
    # should have intervened but didn't).
    record_disagreement(
        task_id=task_id,
        gate_action=gate_action,
        user_visible_reason=user_visible_reason,
        reply_preview=reply_preview,
        sender=sender,
    )
    return (
        f"✓ Recorded operator pushback on task {task_id[:16]}: gate shipped, "
        f"operator disagrees. Surfaces in advisory report."
    )
