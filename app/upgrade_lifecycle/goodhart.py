"""U9 — Goodhart resistance.

PROGRAM §63. Three behaviors that keep the upgrade-lifecycle pipeline
honest about its own value:

  * **MAJOR auto-CR rejection-rate throttle**. If > 40 % of recent
    MAJOR auto-CRs were rejected, widen U4's post-release window
    from 30 d → 60 d so the system waits longer before proposing
    the next MAJOR. Reset when rejection rate drops below 25 %.
  * **Capability-adoption rejection-rate pause**. If > 50 % of
    recent U5 CRs were rejected, pause U5 for 30 d and Signal
    the operator. Lets the operator review the reasons before the
    system burns more LLM budget.
  * **Per-package cooldown after rollback**. The standard
    ``proposal_bridge`` cooldown is enforced by the lifecycle there;
    this module exposes a helper U4/U5 can consult to refuse a
    re-proposal for 90 d when the most recent CR for the same
    ``(package, to_version)`` was rolled back.

Everything reads the change-request audit log; nothing mutates code.
All rate calculations are pure functions of the audit log + the
runtime clock, so the operator can sanity-check by reading the same
data.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────


_DEFAULT_WINDOW_DAYS = 90

# MAJOR auto-CR throttle thresholds.
_MAJOR_HIGH_REJECT_RATE = 0.40           # widen-window trigger
_MAJOR_RECOVER_REJECT_RATE = 0.25        # return-to-default trigger
_MAJOR_WIDENED_WINDOW_DAYS = 60
_MAJOR_DEFAULT_WINDOW_DAYS = 30

# Capability-adoption pause thresholds.
_ADOPTION_PAUSE_REJECT_RATE = 0.50
_ADOPTION_PAUSE_DAYS = 30
_ADOPTION_MIN_SAMPLE_FOR_PAUSE = 6        # don't pause off 2/4 rejected

# Per-package cooldown after rollback.
_PACKAGE_ROLLBACK_COOLDOWN_DAYS = 90


# ── Audit log access ─────────────────────────────────────────────────────


def _audit_log_path() -> Path:
    """Standard change-request audit path."""
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT) / "change_requests" / "audit.jsonl"
    except Exception:
        return Path("/app/workspace/change_requests/audit.jsonl")


def _iter_audit_rows(
    *,
    requestor: Optional[str] = None,
    since: Optional[datetime] = None,
    audit_path: Optional[Path] = None,
) -> Iterable[dict]:
    """Yield matching audit rows in chronological order.

    Filters at the row level so the caller doesn't have to.
    Each row is expected to have ``ts``, ``status`` (or ``transition``),
    and ``requestor`` fields. Tolerant of legacy / extended schemas.
    """
    path = audit_path or _audit_log_path()
    if not path.exists():
        return
    cutoff_iso = since.isoformat() if since else None
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if requestor and row.get("requestor") != requestor:
                    continue
                if cutoff_iso and str(row.get("ts", "")) < cutoff_iso:
                    continue
                yield row
    except OSError:
        return


# ── Approval / rejection rate ────────────────────────────────────────────


def _classify_outcome(row: dict) -> Optional[str]:
    """Return ``"applied"``, ``"rejected"``, ``"rolled_back"``, or None."""
    status = str(row.get("status") or row.get("transition") or "").lower()
    if status in ("applied", "approved", "applied_ok"):
        return "applied"
    if status in ("rejected", "denied"):
        return "rejected"
    if status in ("rolled_back", "reverted"):
        return "rolled_back"
    return None


def compute_rejection_rate(
    *,
    requestor: str,
    window_days: int = _DEFAULT_WINDOW_DAYS,
    now: Optional[datetime] = None,
    audit_path: Optional[Path] = None,
) -> tuple[float, int]:
    """Return ``(rejection_rate, sample_size)``.

    ``rejection_rate`` is rejected / (applied + rejected + rolled_back).
    Rolled-back counts as rejected for throttling purposes since the
    operator's eventual decision was "this shouldn't be in tree."
    """
    now_dt = now or datetime.now(timezone.utc)
    cutoff = now_dt - timedelta(days=window_days)
    counts = {"applied": 0, "rejected": 0, "rolled_back": 0}
    for row in _iter_audit_rows(
        requestor=requestor, since=cutoff, audit_path=audit_path,
    ):
        outcome = _classify_outcome(row)
        if outcome is None:
            continue
        counts[outcome] = counts.get(outcome, 0) + 1
    sample = counts["applied"] + counts["rejected"] + counts["rolled_back"]
    if sample == 0:
        return 0.0, 0
    rejected_like = counts["rejected"] + counts["rolled_back"]
    return rejected_like / sample, sample


# ── MAJOR auto-CR window throttle ────────────────────────────────────────


_MAJOR_THROTTLE_STATE_FILE = "major_auto_cr_throttle.json"


def _major_state_path() -> Path:
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT) / "upgrade_lifecycle" / _MAJOR_THROTTLE_STATE_FILE
    except Exception:
        return Path("/app/workspace/upgrade_lifecycle") / _MAJOR_THROTTLE_STATE_FILE


def _read_major_state() -> dict:
    p = _major_state_path()
    if not p.exists():
        return {"window_days": _MAJOR_DEFAULT_WINDOW_DAYS}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"window_days": _MAJOR_DEFAULT_WINDOW_DAYS}


def _write_major_state(state: dict) -> None:
    p = _major_state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
        tmp.replace(p)
    except OSError:
        logger.debug("ul.goodhart: major state write failed", exc_info=True)


def evaluate_major_window(
    *, now: Optional[datetime] = None,
    audit_path: Optional[Path] = None,
) -> int:
    """Read the audit log + return the effective post-release window in days.

    Side effect: persists the window decision to the throttle state file
    so callers (U4 gate) can read it cheaply via :func:`current_major_window`.
    """
    rate, sample = compute_rejection_rate(
        requestor="dependency_radar",
        window_days=_DEFAULT_WINDOW_DAYS,
        now=now, audit_path=audit_path,
    )
    state = _read_major_state()
    current = int(state.get("window_days") or _MAJOR_DEFAULT_WINDOW_DAYS)
    new_window = current

    # Skip throttling entirely for tiny samples — too noisy.
    if sample >= 5:
        if rate > _MAJOR_HIGH_REJECT_RATE and current == _MAJOR_DEFAULT_WINDOW_DAYS:
            new_window = _MAJOR_WIDENED_WINDOW_DAYS
        elif rate < _MAJOR_RECOVER_REJECT_RATE and current == _MAJOR_WIDENED_WINDOW_DAYS:
            new_window = _MAJOR_DEFAULT_WINDOW_DAYS

    if new_window != current:
        state["window_days"] = new_window
        state["last_change_at"] = (now or datetime.now(timezone.utc)).isoformat()
        state["last_change_reason"] = (
            f"rejection_rate={rate:.0%}, sample={sample}"
        )
        _write_major_state(state)
    return new_window


def current_major_window() -> int:
    """Cheap read of the persisted window (consulted by U4 at gate time)."""
    state = _read_major_state()
    return int(state.get("window_days") or _MAJOR_DEFAULT_WINDOW_DAYS)


# ── Capability-adoption pause ────────────────────────────────────────────


_ADOPTION_PAUSE_STATE_FILE = "capability_adoption_pause.json"


def _adoption_state_path() -> Path:
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT) / "upgrade_lifecycle" / _ADOPTION_PAUSE_STATE_FILE
    except Exception:
        return Path("/app/workspace/upgrade_lifecycle") / _ADOPTION_PAUSE_STATE_FILE


def _read_adoption_state() -> dict:
    p = _adoption_state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_adoption_state(state: dict) -> None:
    p = _adoption_state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
        tmp.replace(p)
    except OSError:
        logger.debug("ul.goodhart: adoption state write failed", exc_info=True)


def evaluate_adoption_pause(
    *, now: Optional[datetime] = None,
    audit_path: Optional[Path] = None,
) -> Optional[str]:
    """Decide whether U5 should be paused for 30 days.

    Returns the pause-until ISO timestamp if paused, None otherwise.
    Idempotent — calling repeatedly returns the same answer until
    the window expires.
    """
    now_dt = now or datetime.now(timezone.utc)
    rate, sample = compute_rejection_rate(
        # U5's CRs flow through proposal_bridge with the same
        # requestor; we read the same audit log.
        requestor="dependency_radar",
        window_days=_DEFAULT_WINDOW_DAYS,
        now=now_dt, audit_path=audit_path,
    )
    state = _read_adoption_state()
    paused_until_iso = state.get("paused_until_iso")
    if paused_until_iso and now_dt.isoformat() < paused_until_iso:
        # Still in pause window.
        return paused_until_iso

    if sample >= _ADOPTION_MIN_SAMPLE_FOR_PAUSE and rate > _ADOPTION_PAUSE_REJECT_RATE:
        until = now_dt + timedelta(days=_ADOPTION_PAUSE_DAYS)
        state["paused_until_iso"] = until.isoformat()
        state["paused_reason"] = (
            f"rejection_rate={rate:.0%} over last {_DEFAULT_WINDOW_DAYS}d "
            f"(sample={sample})"
        )
        _write_adoption_state(state)
        return state["paused_until_iso"]
    return None


def is_adoption_paused(*, now: Optional[datetime] = None) -> bool:
    """Read-only check — does NOT re-evaluate. Use from U5 gate."""
    now_dt = now or datetime.now(timezone.utc)
    state = _read_adoption_state()
    paused_until_iso = state.get("paused_until_iso")
    return bool(paused_until_iso and now_dt.isoformat() < paused_until_iso)


# ── Per-package rollback cooldown ────────────────────────────────────────


def is_package_in_rollback_cooldown(
    package: str, to_version: str,
    *, now: Optional[datetime] = None,
    audit_path: Optional[Path] = None,
    cooldown_days: int = _PACKAGE_ROLLBACK_COOLDOWN_DAYS,
) -> bool:
    """True if the most recent audit entry for ``(package, to_version)``
    is a rollback and it happened less than ``cooldown_days`` ago.

    The lookup is restricted to the package + version pair so a
    rollback of starlette 1.0.1 doesn't muzzle starlette 1.0.2.
    """
    now_dt = now or datetime.now(timezone.utc)
    cutoff = now_dt - timedelta(days=cooldown_days)
    latest_rollback_ts: Optional[str] = None
    for row in _iter_audit_rows(
        requestor=None,  # walk all requestors — operator may rollback via any path
        since=cutoff, audit_path=audit_path,
    ):
        # Match on path + content — the standard CR carries the package
        # name in either the target_path (==> requirements.txt) or in
        # the new_content (==> "starlette==1.0.1"). Be conservative
        # and match on either appearance.
        title = str(row.get("title") or "")
        new_content = str(row.get("new_content") or "")
        body = str(row.get("body") or "")
        haystack = f"{title}\n{new_content}\n{body}"
        if package not in haystack or to_version not in haystack:
            continue
        outcome = _classify_outcome(row)
        if outcome == "rolled_back":
            ts = str(row.get("ts") or "")
            if latest_rollback_ts is None or ts > latest_rollback_ts:
                latest_rollback_ts = ts
    return latest_rollback_ts is not None
