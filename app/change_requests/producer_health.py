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

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_MIN_APPROVAL_RATE = 0.15
DEFAULT_MIN_SAMPLES = 10
DEFAULT_WINDOW_DAYS = 30


@dataclass(frozen=True)
class ProducerStats:
    requestor: str
    window_days: int
    approved: int
    rejected: int

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


def evaluate(requestor: str) -> PauseVerdict:
    """Should this producer be auto-paused right now? Failure-isolated."""
    enabled, floor, min_samples, window = config()
    if not enabled:
        return PauseVerdict(paused=False, reason="disabled")
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
    """Distinct observational producer requestors seen in recent CRs — the
    monitor's scan set."""
    out: list[str] = []
    seen: set[str] = set()
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    try:
        from app.change_requests import store
        from app.change_requests.rejection_gate import is_suppressible_producer

        for cr in store.list_all(limit=limit):
            r = cr.requestor
            if r in seen:
                continue
            ts = _parse_iso(cr.created_at)
            if ts is None or ts < cutoff:
                continue
            if is_suppressible_producer(r):
                seen.add(r)
                out.append(r)
    except Exception:
        logger.debug("producer_health: producer scan failed", exc_info=True)
    return out
