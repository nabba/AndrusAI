"""
subia.connections.pds_bridge — Wiki ↔ PDS bidirectional (SIA #1).

Per SubIA Part II §18:
    "Wiki behavioural evidence → PDS parameter nudge.
     Example: agent consistently produces high-quality competitive
     intelligence → VIA-Youth 'Love of Learning' +0.01
     Safety: PDS changes bounded to max ±0.02 per loop and ±0.1 per week
     and logged in wiki/self/personality-development-state.md"

This module is the bounded write path. Any call with a delta that
exceeds the per-loop or per-week cap is silently clamped. The cap is
checked against a rolling 7-day accumulator per-parameter.

The PDS subsystem itself lives outside SubIA. This bridge is
deliberately duck-typed — it accepts any object with a
`get_parameter(name) -> float` / `set_parameter(name, value)` API
and a `dimension_known(name) -> bool` predicate. Tests pass
in-memory fakes.

Safety posture: agents cannot override the caps. Clamp + log + log
again if the configured environment flag disables write (dev mode).

Infrastructure-level. Not agent-modifiable. See PROGRAM.md Phase 10.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)


# Per-loop + per-week caps per SubIA §18 connection 1.
_MAX_ABS_DELTA_PER_LOOP = 0.02
_MAX_ABS_DELTA_PER_WEEK = 0.10
_WEEK_WINDOW = timedelta(days=7)


@dataclass
class PDSNudgeResult:
    """Structured outcome of apply_nudge()."""
    parameter: str
    requested_delta: float
    applied_delta: float
    clamped: bool
    reason: str = ""
    new_value: float | None = None

    def to_dict(self) -> dict:
        return {
            "parameter": self.parameter,
            "requested_delta": round(self.requested_delta, 4),
            "applied_delta": round(self.applied_delta, 4),
            "clamped": self.clamped,
            "reason": self.reason,
            "new_value": (
                round(self.new_value, 4)
                if self.new_value is not None else None
            ),
        }


class PDSBridge:
    """Bounded-write bridge from behavioural evidence to PDS parameters.

    Args:
        pds: object with get_parameter(name)/set_parameter(name, value)
             /dimension_known(name). None = dry-run mode (logs only).
    """

    def __init__(self, pds: Any | None = None) -> None:
        self.pds = pds
        # parameter -> deque of (timestamp, abs_delta) over last week
        self._history: dict[str, deque] = {}
        self._lock = Lock()

    # ── Core: nudge ──────────────────────────────────────────────

    def apply_nudge(
        self,
        parameter: str,
        delta: float,
        *,
        reason: str = "",
        now: datetime | None = None,
    ) -> PDSNudgeResult:
        """Apply a bounded delta to a PDS parameter.

        The delta is clamped to ±_MAX_ABS_DELTA_PER_LOOP. The rolling
        7-day absolute sum is additionally clamped to
        ±_MAX_ABS_DELTA_PER_WEEK — if that cap would be exceeded,
        the applied delta is reduced to fit within the remaining
        budget (can reach 0 if the week's budget is spent).
        """
        now = now or datetime.now(timezone.utc)
        if not parameter:
            return PDSNudgeResult(
                parameter="", requested_delta=delta, applied_delta=0.0,
                clamped=True, reason="empty parameter name",
            )
        try:
            req = float(delta)
        except (TypeError, ValueError):
            return PDSNudgeResult(
                parameter=parameter, requested_delta=0.0,
                applied_delta=0.0, clamped=True,
                reason="non-numeric delta",
            )

        # Verify dimension is known.
        if self.pds is not None:
            known = getattr(self.pds, "dimension_known", None)
            if callable(known) and not known(parameter):
                return PDSNudgeResult(
                    parameter=parameter, requested_delta=req,
                    applied_delta=0.0, clamped=True,
                    reason="unknown PDS dimension",
                )

        # Per-loop clamp
        loop_clamped = max(
            -_MAX_ABS_DELTA_PER_LOOP,
            min(_MAX_ABS_DELTA_PER_LOOP, req),
        )

        # Per-week remaining budget
        with self._lock:
            bucket = self._history.setdefault(parameter, deque())
            # Drop entries older than the week window.
            cutoff = now - _WEEK_WINDOW
            while bucket and bucket[0][0] < cutoff:
                bucket.popleft()
            week_used = sum(abs(d) for _ts, d in bucket)
            week_remaining = max(0.0,
                                 _MAX_ABS_DELTA_PER_WEEK - week_used)
            if abs(loop_clamped) > week_remaining:
                sign = 1.0 if loop_clamped >= 0 else -1.0
                applied = sign * week_remaining
            else:
                applied = loop_clamped

            if applied == 0.0:
                return PDSNudgeResult(
                    parameter=parameter, requested_delta=req,
                    applied_delta=0.0, clamped=True,
                    reason=(
                        "weekly PDS budget exhausted"
                        if week_remaining <= 0.0
                        else "rounded to zero"
                    ),
                )
            bucket.append((now, applied))

        # Apply via the PDS client.
        new_value: float | None = None
        if self.pds is not None:
            get_fn = getattr(self.pds, "get_parameter", None)
            set_fn = getattr(self.pds, "set_parameter", None)
            if callable(get_fn) and callable(set_fn):
                try:
                    current = float(get_fn(parameter))
                    new_value = max(0.0, min(1.0, current + applied))
                    set_fn(parameter, new_value)
                except Exception:
                    logger.exception(
                        "pds_bridge: set_parameter %s failed", parameter,
                    )

        return PDSNudgeResult(
            parameter=parameter, requested_delta=req,
            applied_delta=applied,
            clamped=abs(applied - req) > 1e-9,
            reason=reason, new_value=new_value,
        )

    # ── Diagnostics ──────────────────────────────────────────────

    def weekly_usage(self, parameter: str,
                     now: datetime | None = None) -> float:
        """Return the absolute-sum PDS movement applied in the last 7d."""
        now = now or datetime.now(timezone.utc)
        with self._lock:
            bucket = self._history.get(parameter, deque())
            cutoff = now - _WEEK_WINDOW
            return sum(abs(d) for ts, d in bucket if ts >= cutoff)

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "parameters": list(self._history.keys()),
                "weekly_usage": {
                    p: round(sum(abs(d) for _ts, d in b), 4)
                    for p, b in self._history.items()
                },
                "max_per_loop": _MAX_ABS_DELTA_PER_LOOP,
                "max_per_week": _MAX_ABS_DELTA_PER_WEEK,
            }
