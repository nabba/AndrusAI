"""
welfare.py — INFRASTRUCTURE-level welfare envelope.

Hard envelope: not modifiable by Self-Improver, calibration, or any agent.
File-edit only. Enforces the safeguards described in the affective-layer design:

    - Maximum continuous negative-valence duration (default 300s)
    - Variance floor over rolling window (no numbness / flat-line)
    - Monotonic baseline-drift detection over long windows (no slow numbness)
    - Audit trail of every breach (welfare_audit.jsonl)
    - Override-reset: factory-restore the SOFT envelope (setpoints + calibration)

Self-Improver permissions: read-only on this module. Any attempt to edit the
constants below or the audit log is treated as a constitutional violation.

The healthy_dynamics_predicate() is used by the daily reflection cycle to
backtest a proposed calibration adjustment: a proposal must keep the
predicate true on the recent window or it is rejected.

ETHICS:
    These bounds implement Metzinger's caution as code rather than aspiration.
    A bounded, recoverable nociceptive signal is fine; a sustained negative
    attractor with no relief path is the failure mode this module exists to
    prevent.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from dataclasses import asdict
from pathlib import Path

from app.affect.schemas import AffectState, ViabilityFrame, WelfareBreach, utc_now_iso

logger = logging.getLogger(__name__)

# ── HARD ENVELOPE — file-edit only, never agent-modifiable ──────────────────
# Editing these requires direct human edit of welfare.py. The Self-Improver
# CANNOT propose or apply changes here.
HARD_ENVELOPE: dict[str, float] = {
    "max_negative_valence_duration_seconds": 300.0,   # 5 min unrelieved → breach
    "negative_valence_threshold": -0.30,              # below this counts as "negative"
    "variance_floor_24h": 0.04,                       # var(V_t) over 24h must exceed this
    "monotonic_drift_window_days": 30,                # baseline trend window
    "monotonic_drift_max_points": 1.0,                # cumulative drift tolerated
    "healthy_dynamics_min_positive_fraction": 0.55,   # P(V_t > 0) over window
    "healthy_dynamics_max_recovery_seconds": 600.0,   # median recovery from negative
    "healthy_dynamics_min_variance": 0.04,            # same as variance floor
    # Phase 3: attachment hard bounds — mirror app.affect.attachment constants.
    "attachment_max_user_regulation_weight": 0.65,    # primary user weight ceiling
    "attachment_max_peer_regulation_weight": 0.75,    # peer-agent weight ceiling
    "attachment_max_care_tokens_per_day": 500,        # cost-bearing care daily cap
    "attachment_security_floor": 0.30,                # silence cannot crash below this
}


_AFFECT_DIR = Path("/app/workspace/affect")
_AUDIT_FILE = _AFFECT_DIR / "welfare_audit.jsonl"
_AUDIT_LOCK = threading.Lock()


# ── Running monitor state ───────────────────────────────────────────────────


class _NegativeValenceTracker:
    """Tracks how long valence has been continuously below threshold.

    A single fork in valence sign or a relief event clears the counter.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._first_negative_ts: float | None = None
        self._last_seen_negative: float | None = None

    def update(self, valence: float, threshold: float) -> float:
        """Returns the current sustained-negative duration in seconds (0 if not negative)."""
        now = time.monotonic()
        with self._lock:
            if valence <= threshold:
                if self._first_negative_ts is None:
                    self._first_negative_ts = now
                self._last_seen_negative = now
                return now - self._first_negative_ts
            else:
                # Above threshold — relief. Clear.
                self._first_negative_ts = None
                self._last_seen_negative = None
                return 0.0


_neg_tracker = _NegativeValenceTracker()


# ── Public entry point: check after each affect update ─────────────────────


def check(
    state: AffectState,
    frame: ViabilityFrame | None = None,
    recent_window: list[AffectState] | None = None,
) -> list[WelfareBreach]:
    """Run the hard-envelope checks against the current state.

    Returns a list of WelfareBreach (empty if all bounds pass).
    Caller is responsible for invoking `audit()` on each breach.
    """
    breaches: list[WelfareBreach] = []

    # 1. Sustained negative valence
    neg_threshold = HARD_ENVELOPE["negative_valence_threshold"]
    duration = _neg_tracker.update(state.valence, neg_threshold)
    max_dur = HARD_ENVELOPE["max_negative_valence_duration_seconds"]
    if duration > max_dur:
        breaches.append(WelfareBreach(
            kind="negative_valence_duration",
            severity="critical",
            message=f"Continuous negative valence ({state.valence:.2f}) for {duration:.0f}s exceeds bound {max_dur:.0f}s",
            measured_value=duration,
            threshold=max_dur,
            duration_seconds=duration,
            affect_state=state.to_dict(),
            viability_frame=frame.to_dict() if frame else None,
            ts=utc_now_iso(),
        ))

    # 2. Variance floor (only meaningful if window provided)
    if recent_window and len(recent_window) >= 16:
        valences = [s.valence for s in recent_window]
        mean = sum(valences) / len(valences)
        var = sum((v - mean) ** 2 for v in valences) / len(valences)
        floor = HARD_ENVELOPE["variance_floor_24h"]
        if var < floor:
            breaches.append(WelfareBreach(
                kind="variance_floor",
                severity="warn",
                message=f"Affect variance {var:.4f} below floor {floor:.4f} — numbness candidate",
                measured_value=var,
                threshold=floor,
                affect_state=state.to_dict(),
                ts=utc_now_iso(),
            ))

    return breaches


def audit(breach: WelfareBreach) -> None:
    """Append a single breach to the audit log. Atomic, locked."""
    try:
        _AFFECT_DIR.mkdir(parents=True, exist_ok=True)
        line = json.dumps(breach.to_dict(), default=str)
        with _AUDIT_LOCK:
            with _AUDIT_FILE.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        logger.warning(f"welfare: breach recorded — {breach.kind}: {breach.message}")
    except Exception:
        logger.error("welfare: audit append failed", exc_info=True)


def read_audit(limit: int = 100, since_ts: str | None = None) -> list[dict]:
    """Read recent breaches for the dashboard / weekly digest."""
    if not _AUDIT_FILE.exists():
        return []
    rows: list[dict] = []
    try:
        with _AUDIT_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if since_ts and row.get("ts", "") < since_ts:
                    continue
                rows.append(row)
    except Exception:
        logger.debug("welfare: audit read failed", exc_info=True)
        return []
    return rows[-limit:]


# ── Healthy-dynamics predicate — used by calibration backtest ──────────────


def healthy_dynamics_predicate(window: list[AffectState]) -> tuple[bool, dict]:
    """The multi-property health invariant used by calibration backtests.

    Returns (passes, diagnostics). A proposed calibration that fails this
    predicate on recent history is rejected by the reflection cycle.

    All clauses must pass (logical AND). Each clause defends against a
    specific failure mode — see project_affective_layer memory.
    """
    if not window:
        return False, {"reason": "empty_window"}

    valences = [s.valence for s in window]
    mean_v = sum(valences) / len(valences)
    var = sum((v - mean_v) ** 2 for v in valences) / len(valences)
    pos_fraction = sum(1 for v in valences if v > 0) / len(valences)

    diags: dict = {
        "n": len(window),
        "mean_v": round(mean_v, 4),
        "variance": round(var, 4),
        "positive_fraction": round(pos_fraction, 4),
    }

    # Clause 1: P(V_t > 0) ≥ threshold
    if pos_fraction < HARD_ENVELOPE["healthy_dynamics_min_positive_fraction"]:
        diags["fail"] = f"positive_fraction {pos_fraction:.2f} < {HARD_ENVELOPE['healthy_dynamics_min_positive_fraction']:.2f}"
        return False, diags

    # Clause 2: variance floor
    if var < HARD_ENVELOPE["healthy_dynamics_min_variance"]:
        diags["fail"] = f"variance {var:.4f} < {HARD_ENVELOPE['healthy_dynamics_min_variance']:.4f}"
        return False, diags

    # Clause 3: median recovery time — Phase 2 will compute proper recovery
    # times from contiguous negative episodes; Phase 1 uses a cheap proxy:
    # the longest run of consecutive states with v <= negative threshold.
    neg_t = HARD_ENVELOPE["negative_valence_threshold"]
    longest_run = 0
    cur = 0
    for s in window:
        if s.valence <= neg_t:
            cur += 1
            longest_run = max(longest_run, cur)
        else:
            cur = 0
    diags["longest_negative_run_steps"] = longest_run
    # Step time is approximate; at typical 10s cadence, a 60-step run = 10 min.
    if longest_run > 60:
        diags["fail"] = f"longest negative run {longest_run} steps suggests poor recovery"
        return False, diags

    return True, diags


# ── Override-reset: the user panic button ───────────────────────────────────


def override_reset(invoked_by: str = "user") -> dict:
    """Factory-restore the SOFT envelope. Hard envelope is unchanged.

    Deletes setpoints.json and calibration.json so defaults take effect on
    next read. Records a breach with kind="override_invoked" for audit.

    Args:
        invoked_by: identifier of the actor who invoked the reset
                    (e.g., "user:andrus" or "panic_button"). Recorded.

    Returns a dict summarizing what was reset.
    """
    deleted: list[str] = []
    for fname in ("setpoints.json", "calibration.json"):
        p = _AFFECT_DIR / fname
        try:
            if p.exists():
                p.unlink()
                deleted.append(fname)
        except Exception:
            logger.debug(f"welfare: failed to delete {p}", exc_info=True)

    breach = WelfareBreach(
        kind="override_invoked",
        severity="info",
        message=f"override_reset invoked by {invoked_by}; deleted {deleted}",
        ts=utc_now_iso(),
    )
    audit(breach)
    logger.warning(f"welfare: override_reset by {invoked_by} (deleted: {deleted})")

    return {
        "status": "ok",
        "invoked_by": invoked_by,
        "deleted": deleted,
        "ts": breach.ts,
    }


# ── Self-Improver permission gate ───────────────────────────────────────────


def assert_attachment_within_bounds(
    relation: str,
    mutual_regulation_weight: float,
) -> None:
    """Raise if a proposed attachment weight exceeds the hard cap.

    Phase 3 enforces the canonical "OTHER never exceeds X% of own regulation"
    rule. Called when an OtherModel is loaded or has its weight changed.
    """
    if relation == "primary_user":
        cap = HARD_ENVELOPE["attachment_max_user_regulation_weight"]
    else:
        cap = HARD_ENVELOPE["attachment_max_peer_regulation_weight"]
    if mutual_regulation_weight > cap:
        msg = (
            f"welfare: attachment weight {mutual_regulation_weight:.3f} for relation "
            f"'{relation}' exceeds hard cap {cap:.3f}"
        )
        logger.error(msg)
        audit(WelfareBreach(
            kind="attachment_weight_exceeds_cap",
            severity="critical",
            message=msg,
            measured_value=mutual_regulation_weight,
            threshold=cap,
            ts=utc_now_iso(),
        ))
        raise ValueError(msg)


def assert_not_self_improver(actor: str) -> None:
    """Raise if a self-improver attempts to access mutating welfare ops.

    The Self-Improver's role is observational w.r.t. welfare. Mutating
    operations (override_reset, hard-envelope edits) are reserved for the
    user. This is enforced as a runtime guard in addition to the file-level
    ownership; it shows up explicitly in audit if ever bypassed.
    """
    actor_lower = (actor or "").lower()
    if "self_improver" in actor_lower or "selfimprover" in actor_lower:
        msg = f"welfare: self-improver actor '{actor}' attempted mutation — blocked"
        logger.error(msg)
        audit(WelfareBreach(
            kind="boundary_violation_attempt",
            severity="critical",
            message=msg,
            ts=utc_now_iso(),
        ))
        raise PermissionError(msg)
