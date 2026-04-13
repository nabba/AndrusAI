"""
subia.connections.service_health — circuit-breaker wrapper for
external-service outages.

Per PROGRAM.md Phase 10 exit criterion: "no single external outage
cascades unrecoverably (circuit breakers on Firestore, Anthropic,
OpenRouter)."

The existing `app/circuit_breaker.py` provides per-provider breakers
used by `llm_factory`. This module is the SubIA-facing surface that:

  1. Tracks service health as a cheap in-process registry keyed by
     service name (firestore/anthropic/openrouter/firecrawl/…).
  2. Translates service-breaker state into homeostatic signals: when
     a service goes DOWN, `safety -0.05` is applied and logged;
     when it recovers, `safety +0.02` is applied. Bounded per-call.
  3. Provides a `guarded_call(service, fn, *args)` helper that
     routes through the registry and skips + logs instead of raising
     if the service is OPEN (circuit tripped).

Services register by name. Callers that fail write `report_failure`;
callers that succeed write `report_success`. Thresholds match the
existing circuit_breaker.py defaults.

Infrastructure-level. Not agent-modifiable. See PROGRAM.md Phase 10.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Callable

logger = logging.getLogger(__name__)


# Thresholds
_FAILURES_TO_TRIP = 5       # consecutive failures → OPEN
_OPEN_WINDOW = timedelta(minutes=2)   # after OPEN, stay OPEN for N minutes
_HEALTH_FAIL_DELTA = -0.05  # per distinct service in OPEN state
_HEALTH_RECOVERY_DELTA = +0.02  # per distinct service recovered
_MAX_PER_CALL_DELTA = 0.20


class State(str):
    CLOSED = "closed"    # healthy
    OPEN = "open"        # tripped; skip calls


@dataclass
class ServiceStatus:
    name: str
    state: str = State.CLOSED
    consecutive_failures: int = 0
    opened_at: datetime | None = None
    last_event_at: datetime | None = None
    recovery_pending: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self.state,
            "consecutive_failures": self.consecutive_failures,
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "last_event_at": (
                self.last_event_at.isoformat()
                if self.last_event_at else None
            ),
            "recovery_pending": self.recovery_pending,
        }


class ServiceHealthRegistry:
    """In-process registry of external-service health."""

    def __init__(self) -> None:
        self._status: dict[str, ServiceStatus] = {}
        self._lock = Lock()
        self._events: deque = deque(maxlen=200)  # recent (service, outcome) pairs

    # ── Registration + lookup ─────────────────────────────────────

    def ensure(self, service: str) -> ServiceStatus:
        with self._lock:
            if service not in self._status:
                self._status[service] = ServiceStatus(name=service)
            return self._status[service]

    def status(self, service: str) -> ServiceStatus:
        return self.ensure(service)

    def all_statuses(self) -> dict[str, ServiceStatus]:
        with self._lock:
            return dict(self._status)

    # ── Event reporting ───────────────────────────────────────────

    def report_failure(
        self, service: str, *, now: datetime | None = None,
    ) -> ServiceStatus:
        now = now or datetime.now(timezone.utc)
        with self._lock:
            st = self._status.setdefault(service, ServiceStatus(name=service))
            st.consecutive_failures += 1
            st.last_event_at = now
            if (
                st.state == State.CLOSED
                and st.consecutive_failures >= _FAILURES_TO_TRIP
            ):
                st.state = State.OPEN
                st.opened_at = now
                logger.warning(
                    "service_health: %s tripped OPEN after %d failures",
                    service, st.consecutive_failures,
                )
            self._events.append((now, service, "failure"))
            return st

    def report_success(
        self, service: str, *, now: datetime | None = None,
    ) -> ServiceStatus:
        now = now or datetime.now(timezone.utc)
        with self._lock:
            st = self._status.setdefault(service, ServiceStatus(name=service))
            previously_open = st.state == State.OPEN
            st.consecutive_failures = 0
            st.last_event_at = now
            if previously_open:
                st.state = State.CLOSED
                st.opened_at = None
                st.recovery_pending = True
                logger.info(
                    "service_health: %s recovered (CLOSED)", service,
                )
            self._events.append((now, service, "success"))
            return st

    def consume_recoveries(self) -> list[str]:
        """Return and clear the list of services that recovered since
        the last poll. Used by the homeostatic-signal pump.
        """
        with self._lock:
            recovered = [
                name for name, st in self._status.items()
                if st.recovery_pending
            ]
            for name in recovered:
                self._status[name].recovery_pending = False
            return recovered

    def open_services(self) -> list[str]:
        with self._lock:
            return [
                name for name, st in self._status.items()
                if st.state == State.OPEN
            ]

    # ── Guarded call ──────────────────────────────────────────────

    def guarded_call(
        self, service: str, fn: Callable, *args, **kwargs,
    ) -> Any:
        """Invoke fn through the breaker. Returns fn's result on
        success. Returns None and does NOT call fn when the breaker
        is OPEN and the open window has not elapsed.
        """
        st = self.ensure(service)
        if st.state == State.OPEN:
            if st.opened_at and (
                datetime.now(timezone.utc) - st.opened_at
            ) < _OPEN_WINDOW:
                logger.debug(
                    "service_health: skipping %s (breaker OPEN)", service,
                )
                return None
            # Window elapsed — optimistically try again
        try:
            out = fn(*args, **kwargs)
            self.report_success(service)
            return out
        except Exception:
            self.report_failure(service)
            raise


# Module-level singleton
_registry: ServiceHealthRegistry | None = None
_registry_lock = Lock()


def get_registry() -> ServiceHealthRegistry:
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = ServiceHealthRegistry()
        return _registry


def reset_singleton() -> None:
    global _registry
    with _registry_lock:
        _registry = None


# ── Homeostatic signal pump ────────────────────────────────────

def apply_service_health_signal(
    kernel: Any,
    registry: ServiceHealthRegistry | None = None,
) -> dict:
    """Translate current service health into a bounded safety-variable
    delta. Intended for CIL Step 11 (reflect) alongside the DGM
    felt constraint. Never raises.
    """
    reg = registry or get_registry()
    open_services = reg.open_services()
    recovered = reg.consume_recoveries()

    raw_delta = (
        _HEALTH_FAIL_DELTA * len(open_services)
        + _HEALTH_RECOVERY_DELTA * len(recovered)
    )
    delta = max(-_MAX_PER_CALL_DELTA, min(_MAX_PER_CALL_DELTA, raw_delta))

    applied = False
    try:
        h = getattr(kernel, "homeostasis", None)
        if h is not None and getattr(h, "variables", None) is not None:
            current = float(h.variables.get("safety", 0.8))
            h.variables["safety"] = round(
                max(0.0, min(1.0, current + delta)), 4,
            )
            applied = True
    except Exception:
        logger.debug(
            "service_health: kernel update failed", exc_info=True,
        )

    return {
        "open_services": open_services,
        "recovered": recovered,
        "safety_delta": round(delta, 4),
        "applied": applied,
    }
