"""Gate C — per-producer approval-rate auto-pause (2026-05-30).

The backstop behind Gate A (semantic suppression) and Gate B (evidence
gating). Those two stop *known-rejected* and *unverified* proposals; Gate C
catches the remaining failure mode — an observational producer that floods
the operator with a stream of *distinct* low-value proposals, each evading
Gate A because it's not a paraphrase of a prior rejection.

The mechanism is the Goodhart-guard pattern (already used for the auto-apply
lane) applied to proposal producers: compute each producer's rolling
**operator**-approval rate from the change-request store; if it falls below a
floor with enough samples, auto-pause the producer (its CRs are recorded
REJECTED at the gate instead of queued) and surface it to the operator via
the ``producer_approval_health`` healing monitor — rather than making the
operator reject the same producer's output a 20th time.

Design choices that keep this safe and self-correcting:

  * **Operator decisions only.** The rate counts CRs whose ``decided_by`` is
    an explicit operator action (Signal 👍/👎, React approve/reject). System
    terminal states — validation_failed, TIER_IMMUTABLE_REFUSED, the
    Gate-A/Gate-C suppressions themselves, auto-applies — are EXCLUDED. This
    is load-bearing: counting the gate's own suppressions would create a
    self-reinforcing pause loop that never releases.
  * **Observational producers only.** Reuses
    :func:`app.change_requests.rejection_gate.is_suppressible_producer`
    (the ``proposal_bridge:`` boundary). Humans, bug-fix producers, and the
    evidence-bearing ``library_radar_trial`` adoption CR are never paused.
  * **Cooldown, not a ban.** The rate is computed live over a rolling
    window. While paused, no new operator decisions accrue, so the existing
    rejections age out of the window; once fewer than ``min_samples``
    operator decisions remain, the producer un-pauses and earns a fresh
    chance. A genuinely-bad producer simply re-pauses on its next flood.
  * **min_samples gate.** Never fires below ``min_samples`` explicit operator
    decisions, so a producer is never paused on thin data.
  * **Failure-isolated.** Any error → not paused (the CR proceeds).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_MIN_APPROVAL_RATE = 0.15
DEFAULT_MIN_SAMPLES = 10
DEFAULT_WINDOW_DAYS = 30
# Gate C — structural-failure path (2026-06-07). The operator-approval-rate
# path is statistically dead when the operator stays silent (it needs
# >=min_samples EXPLICIT 👍/👎 that rarely arrive — live: 0/0/0/11 across
# producers). This second, parallel trigger pauses a producer that floods
# structurally-doomed CRs — ones the system rejects at create time (path
# outside allowed roots, or a TIER_IMMUTABLE target) — with NO operator
# reaction required. It counts the ``validation_failed`` /
# ``tier_immutable_refused`` AUDIT events (NOT the CR status, which can't
# distinguish them from Gate-A semantic suppressions), so it never counts the
# gate's own suppressions and therefore never self-latches.
DEFAULT_SYSTEM_FAIL_SAMPLES = 8
_SYSTEM_FAIL_EVENTS = ("validation_failed", "tier_immutable_refused")

# Producers that must NEVER be auto-paused, even when flooding doomed CRs:
# humans / interactive surfaces + the core working agents + critical fixers.
# Everything else (observational monitors, drills, reconcilers, radars, the
# proposal_bridge: family) is eligible for the structural-failure pause. The
# >=8-failures threshold means a protected producer would have to file 8
# un-appliable CRs in 30d to matter anyway — itself a bug worth surfacing.
_NEVER_SYSTEM_PAUSE_EXACT: frozenset[str] = frozenset({
    "coder", "writer", "researcher", "commander", "pim", "media",
    "desktop", "devops", "financial", "self_improver", "error_diagnosis",
    "self_heal_handler",
})
_NEVER_SYSTEM_PAUSE_PREFIXES: tuple[str, ...] = (
    "claude_code", "operator", "discord:", "signal:", "react:",
)


@dataclass(frozen=True)
class ProducerStats:
    requestor: str
    window_days: int
    approved: int
    rejected: int
    system_failed: int = 0

    @property
    def n(self) -> int:
        return self.approved + self.rejected

    @property
    def rate(self) -> Optional[float]:
        return (self.approved / self.n) if self.n else None


@dataclass(frozen=True)
class PauseVerdict:
    paused: bool
    reason: str = ""
    stats: Optional[ProducerStats] = None


def config() -> tuple[bool, float, int, int]:
    """``(enabled, min_approval_rate, min_samples, window_days)``.

    Reads :mod:`app.runtime_settings`; conservative defaults on failure.
    """
    enabled = True
    floor = DEFAULT_MIN_APPROVAL_RATE
    min_samples = DEFAULT_MIN_SAMPLES
    window = DEFAULT_WINDOW_DAYS
    try:
        from app import runtime_settings as rs

        snap = rs.snapshot()
        enabled = bool(snap.get("producer_autopause_enabled", enabled))
        floor = float(snap.get("producer_autopause_min_approval_rate", floor))
        min_samples = int(snap.get("producer_autopause_min_samples", min_samples))
        window = int(snap.get("producer_autopause_window_days", window))
    except Exception:
        logger.debug("producer_health: config read failed", exc_info=True)
    return enabled, floor, min_samples, window


def _operator_decision(cr) -> Optional[bool]:
    """True=operator-approved, False=operator-rejected, None=not an explicit
    operator decision (PENDING, timeout, system terminal state, auto-apply)."""
    try:
        from app.change_requests.models import DecisionSource

        ds = cr.decided_by
    except Exception:
        return None
    if ds in (DecisionSource.SIGNAL_THUMBS_UP, DecisionSource.REACT_APPROVE):
        return True
    if ds in (DecisionSource.SIGNAL_THUMBS_DOWN, DecisionSource.REACT_REJECT):
        return False
    return None


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def approval_stats(requestor: str, *, window_days: int) -> ProducerStats:
    """Rolling explicit-operator-approval stats for one producer."""
    approved = rejected = 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    try:
        from app.change_requests import store

        for cr in store.list_all(limit=5000):
            if cr.requestor != requestor:
                continue
            ts = _parse_iso(cr.created_at)
            if ts is None or ts < cutoff:
                continue
            decision = _operator_decision(cr)
            if decision is True:
                approved += 1
            elif decision is False:
                rejected += 1
    except Exception:
        logger.debug("producer_health: store scan failed", exc_info=True)
    return ProducerStats(
        requestor=requestor, window_days=window_days,
        approved=approved, rejected=rejected,
    )


def _system_fail_samples() -> int:
    """Min structurally-doomed CRs (in the window) that auto-pauses a producer.
    Reads runtime_settings; conservative default on any failure."""
    try:
        from app import runtime_settings as rs

        return int(rs.snapshot().get(
            "producer_autopause_system_fail_samples", DEFAULT_SYSTEM_FAIL_SAMPLES,
        ))
    except Exception:
        return DEFAULT_SYSTEM_FAIL_SAMPLES


def is_system_pausable_producer(requestor: str) -> bool:
    """Eligible for the structural-failure pause (Trigger 1)? Wider than
    :func:`rejection_gate.is_suppressible_producer` (proposal_bridge: only) so
    it also catches observational monitors / drills / reconcilers / radars that
    flood un-appliable CRs — but never humans, the core working agents, or
    critical self-heal fixers (the never-pause set)."""
    r = (requestor or "").strip()
    if not r:
        return False
    if r in _NEVER_SYSTEM_PAUSE_EXACT:
        return False
    if any(r.startswith(p) for p in _NEVER_SYSTEM_PAUSE_PREFIXES):
        return False
    return True


def system_failure_count(requestor: str, *, window_days: int) -> int:
    """This producer's structurally-doomed CRs in the window — the
    ``validation_failed`` / ``tier_immutable_refused`` AUDIT events.

    Reads the change-request audit log (not the CR store) on purpose: a
    validation_failed CR has status=REJECTED + decided_by=None, IDENTICAL on
    the CR object to a Gate-A semantic suppression — only the audit ``event``
    field distinguishes them. Counting Gate-A's own suppressions would
    self-latch the pause; counting only these two events does not (a paused
    producer files nothing new — its suppressed CRs carry a different event —
    so its failures age out of the window and it un-pauses). Failure-isolated:
    returns 0 on any error → no pause."""
    from app.change_requests import store

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    count = 0
    try:
        path = Path(store._STORE_DIR) / "audit.jsonl"
        if not path.exists():
            return 0
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                payload = entry.get("payload") or {}
                if payload.get("event") not in _SYSTEM_FAIL_EVENTS:
                    continue
                if payload.get("requestor") != requestor:
                    continue
                ts = _parse_iso(entry.get("ts", ""))
                if ts is None or ts < cutoff:
                    continue
                count += 1
    except Exception:
        logger.debug("producer_health: audit scan failed", exc_info=True)
        return 0
    return count


def evaluate(requestor: str) -> PauseVerdict:
    """Should this producer be auto-paused right now? Failure-isolated.

    Two independent pause triggers (EITHER fires):
      1. **Structural-failure** (2026-06-07) — the producer filed
         ``>= system_fail_samples`` structurally-doomed CRs
         (validation_failed / tier_immutable_refused) in the window. Needs NO
         operator reaction, so it works even when the operator stays silent
         (the gap that made the original trigger a dead gate). Applies to the
         wider system-pausable set (observational monitors/drills/reconcilers/
         radars), not just proposal_bridge: producers. Non-circular: a paused
         producer files nothing new, so its failures age out → it un-pauses.
      2. **Approval-rate** (original) — a proposal_bridge: producer's rolling
         EXPLICIT-operator-approval rate fell below the floor with enough
         samples.
    """
    enabled, floor, min_samples, window = config()
    if not enabled:
        return PauseVerdict(paused=False, reason="disabled")

    # Trigger 1 — structural-failure (wider eligibility; no operator reaction).
    try:
        if is_system_pausable_producer(requestor):
            sys_samples = _system_fail_samples()
            sys_fails = system_failure_count(requestor, window_days=window)
            if sys_fails >= sys_samples:
                return PauseVerdict(
                    paused=True,
                    reason=(
                        f"producer auto-paused: {sys_fails} structurally-doomed "
                        f"CRs (validation_failed / tier_immutable_refused) over "
                        f"{window}d (>= {sys_samples}) — filing un-appliable paths"
                    ),
                    stats=ProducerStats(
                        requestor=requestor, window_days=window,
                        approved=0, rejected=0, system_failed=sys_fails,
                    ),
                )
    except Exception:
        logger.debug("producer_health: system-fail check failed", exc_info=True)

    # Trigger 2 — operator-approval-rate (proposal_bridge: producers only).
    try:
        from app.change_requests.rejection_gate import is_suppressible_producer

        if not is_suppressible_producer(requestor):
            return PauseVerdict(paused=False, reason="not an observational producer")
    except Exception:
        return PauseVerdict(paused=False, reason="producer check failed")

    stats = approval_stats(requestor, window_days=window)
    if stats.n < min_samples or stats.rate is None:
        return PauseVerdict(
            paused=False,
            reason=f"insufficient data (n={stats.n} < {min_samples})",
            stats=stats,
        )
    if stats.rate >= floor:
        return PauseVerdict(paused=False, reason="approval rate healthy", stats=stats)
    return PauseVerdict(
        paused=True,
        reason=(
            f"producer auto-paused: {stats.approved}/{stats.n} "
            f"({stats.rate:.0%}) operator-approved over {window}d "
            f"(floor {floor:.0%})"
        ),
        stats=stats,
    )


def known_observational_producers(*, window_days: int, limit: int = 5000) -> list[str]:
    """Distinct auto-pausable producer requestors seen in recent CRs — the
    monitor's scan set. Uses the WIDER system-pausable set (not just
    proposal_bridge:) so the monitor surfaces structural-failure pauses on
    observational monitors / drills / reconcilers too; evaluate() then applies
    the correct per-trigger eligibility."""
    out: list[str] = []
    seen: set[str] = set()
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    try:
        from app.change_requests import store

        for cr in store.list_all(limit=limit):
            r = cr.requestor
            if r in seen:
                continue
            ts = _parse_iso(cr.created_at)
            if ts is None or ts < cutoff:
                continue
            if is_system_pausable_producer(r):
                seen.add(r)
                out.append(r)
    except Exception:
        logger.debug("producer_health: producer scan failed", exc_info=True)
    return out
