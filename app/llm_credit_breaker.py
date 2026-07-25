"""llm_credit_breaker.py — stop the 402 failover storm.

The problem this solves
-----------------------
When a paid provider returns HTTP 402 ("Insufficient credits"),
``rate_throttle._try_credit_failover_{sync,async}`` retries the call once
against a local Ollama model.  As a *blip* absorber that is right: a
momentarily-empty balance degrades the system to "slower but still working"
instead of "completely broken".

As a *sustained-outage* absorber it is actively harmful, because the failover
target is `ollama/llama3.1:8b` — a model the codebase itself documents as
unable to handle tool calls (``llm_selector`` §"tool-incapable").  Once credits
are genuinely exhausted, every single call takes the paid attempt's latency,
fails, and then gets answered by an 8B model that cannot use the tools the task
depends on.  The system keeps *replying*, so nothing looks broken, but the
answers are quietly worthless.

That is not hypothetical.  During the 2026-07-24 golden-set run:

  * **69 × HTTP 402 in 38 minutes, and all 69 failed over to llama3.1:8b.**
  * The first landed during question 2 of 12.  Question 1, which ran before the
    outage, produced a complete report; almost everything after it failed on
    missing evidence, empty output, or 24-minute latency.
  * No alert fired.  The outage was only found the next day, by reading logs.

Full account: ``reports/GATE_DIAGNOSIS_2026-07-25.md``.

Relationship to the existing credit alerting
--------------------------------------------
``rate_throttle._check_credit_error`` already calls
``firebase_reporter.report_credit_alert`` on every credit error, which lands in
the ``status/credit_alerts`` Firebase doc and the budget dashboard.  That
machinery was fully active during the incident and did not help, for two
reasons: it records the condition on a surface nobody was watching, and it does
nothing to stop the failover.  This module is the missing half — it changes
*behaviour* (suppress the pointless failover) and pages the operator on the
channel they actually read.  Dashboard reporting stays where it is; nothing here
replaces it.

Design
------
A rolling-window counter per provider:

* Under ``threshold`` 402s in ``window_s`` → **CLOSED**: failover proceeds, the
  blip is absorbed as before.
* At the threshold → **OPEN**: :func:`should_failover` returns False, so the
  original 402 propagates to the caller instead of being answered by a
  tool-incapable model.  Callers already handle credit errors as a *typed*
  condition (``orchestrator`` reports "Today's <provider> budget is exhausted"
  and alerts the operator), which is the honest outcome.
* The operator is alerted **once per trip**, not once per error — 69 Signal
  messages would be its own outage.
* After ``cooldown_s`` with no further 402s the window empties and the breaker
  closes again, so a top-up needs no manual reset.

Deliberately dependency-light and failure-closed-to-safe: every public function
swallows its own exceptions and degrades to "behave as before", because this
runs inside the LLM error path where a raising helper would convert a
recoverable provider error into a crash.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# Number of credit errors within ``_WINDOW_S`` that trips a provider.
# 6 is comfortably above any plausible blip (a transient 402 during a top-up
# race is 1–2 calls) and far below the 69 observed in the incident.
_THRESHOLD = 6

# Rolling window for counting credit errors.
_WINDOW_S = 300.0

# Quiet period after which a tripped provider is retried. Credit top-ups are a
# human action on a minutes-to-hours scale, so re-probing every few minutes is
# the right cadence: cheap (one call), and it self-heals without operator input.
_COOLDOWN_S = 600.0


@dataclass
class _ProviderState:
    """Rolling 402 timestamps plus trip bookkeeping for one provider."""

    errors: deque[float] = field(default_factory=deque)
    tripped_at: float | None = None
    alerted: bool = False
    total_errors: int = 0
    suppressed_failovers: int = 0


_lock = threading.Lock()
_states: dict[str, _ProviderState] = {}


def provider_of(model: str) -> str:
    """Best-effort provider name from a litellm model string.

    ``"openrouter/anthropic/claude-opus-4.7"`` → ``"openrouter"``;
    a bare ``"gpt-5"`` → ``"unknown"`` rather than raising.
    """
    text = str(model or "").strip()
    if not text:
        return "unknown"
    return text.split("/", 1)[0].lower() if "/" in text else "unknown"


def _prune(state: _ProviderState, now: float) -> None:
    cutoff = now - _WINDOW_S
    while state.errors and state.errors[0] < cutoff:
        state.errors.popleft()


def _state_for(provider: str) -> _ProviderState:
    state = _states.get(provider)
    if state is None:
        state = _ProviderState()
        _states[provider] = state
    return state


def record_credit_error(model: str) -> bool:
    """Record one credit (402) error. Returns True if this call tripped it.

    Safe to call on every credit error; the rolling window does the rest.
    """
    try:
        provider = provider_of(model)
        now = time.monotonic()
        with _lock:
            state = _state_for(provider)
            _prune(state, now)
            state.errors.append(now)
            state.total_errors += 1
            if state.tripped_at is not None:
                # Already open — refresh so the cooldown measures quiet time,
                # not time since the first error.
                state.tripped_at = now
                return False
            if len(state.errors) < _THRESHOLD:
                return False
            state.tripped_at = now
            already_alerted = state.alerted
            state.alerted = True
            count = len(state.errors)
        logger.error(
            "credit breaker OPEN for provider %r — %d credit errors in %.0fs; "
            "suppressing failover to local models so the real error surfaces "
            "instead of being answered by a tool-incapable fallback",
            provider, count, _WINDOW_S,
        )
        if not already_alerted:
            _alert_operator(provider, count)
        return True
    except Exception:  # pragma: no cover — must never break the error path
        logger.debug("record_credit_error failed", exc_info=True)
        return False


def is_open(model: str) -> bool:
    """Whether the breaker is currently open for this model's provider."""
    try:
        provider = provider_of(model)
        now = time.monotonic()
        with _lock:
            state = _states.get(provider)
            if state is None or state.tripped_at is None:
                return False
            if now - state.tripped_at >= _COOLDOWN_S:
                # Cooldown elapsed with no fresh errors — half-open: let the
                # next call through. If credits are still out, the next 402
                # re-trips immediately (threshold 1, since the window kept its
                # entries only if they were recent).
                state.tripped_at = None
                state.alerted = False
                state.errors.clear()
                logger.warning(
                    "credit breaker for provider %r closing after %.0fs quiet "
                    "— will retry paid calls", provider, _COOLDOWN_S,
                )
                return False
            return True
    except Exception:  # pragma: no cover
        logger.debug("is_open failed", exc_info=True)
        return False


def should_failover(model: str) -> bool:
    """Whether a credit-error failover to a local model should proceed.

    False once the provider's breaker is open: continuing would answer the
    user from a tool-incapable 8B model and hide a real outage.
    """
    try:
        if not is_open(model):
            return True
        with _lock:
            state = _states.get(provider_of(model))
            if state is not None:
                state.suppressed_failovers += 1
        return False
    except Exception:  # pragma: no cover
        logger.debug("should_failover failed", exc_info=True)
        return True


def _alert_operator(provider: str, count: int) -> None:
    """Tell the operator once, on the channel they actually read."""
    body = (
        f"💳 {provider} credits appear exhausted — {count} HTTP 402 errors "
        f"in {_WINDOW_S / 60:.0f} min.\n\n"
        "Failover to local models is now SUPPRESSED for this provider: the "
        "fallback target can't call tools, so answers built on it are "
        "unreliable and it hid this outage for a full day on 2026-07-24.\n\n"
        "Requests needing a paid model will fail with a clear credit error "
        "until you top up. The breaker retries automatically after "
        f"{_COOLDOWN_S / 60:.0f} min of quiet."
    )
    try:
        from app.config import get_settings
        from app.signal_client import send_message

        recipient = (get_settings().signal_owner_number or "").strip()
        if recipient:
            send_message(recipient, body)
            return
    except Exception:
        logger.exception(
            "credit breaker: operator alert via Signal failed for %r", provider,
        )
    logger.error("credit breaker alert (undelivered): %s", body)


def snapshot() -> dict[str, dict]:
    """Observable state for /cp dashboards and tests."""
    try:
        now = time.monotonic()
        out: dict[str, dict] = {}
        with _lock:
            for provider, state in _states.items():
                _prune(state, now)
                out[provider] = {
                    "errors_in_window": len(state.errors),
                    "threshold": _THRESHOLD,
                    "open": state.tripped_at is not None,
                    "seconds_since_trip": (
                        round(now - state.tripped_at, 1)
                        if state.tripped_at is not None else None
                    ),
                    "total_errors": state.total_errors,
                    "suppressed_failovers": state.suppressed_failovers,
                }
        return out
    except Exception:  # pragma: no cover
        logger.debug("snapshot failed", exc_info=True)
        return {}


def reset(provider: str | None = None) -> None:
    """Clear breaker state. Test helper / operator escape hatch."""
    with _lock:
        if provider is None:
            _states.clear()
        else:
            _states.pop(provider.lower(), None)
