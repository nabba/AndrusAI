"""Anthropic per-day USD spend cap (Phase D.3, 2026-05-22).

Vendor-level cost ceiling on top of the existing per-model breakers
and connector budgets. Sits next to ``app/connector_budget/`` rather
than inside it because the cap-mechanism is structurally identical
but the *scope* is different:

  * ``connector_budget`` — per-external-service quota (Aviationstack,
    OSV.dev, GitHub API, …). One ledger row per *call* per *connector*.
  * ``llm_anthropic_budget`` — vendor-wide rolling USD across every
    Anthropic call regardless of which subsystem made it (Mem0
    extractor, brainstorm crew, coder agent, dossier composer, …).

When a runaway loop / accidentally-cheaper-model-rotation / new
high-volume subsystem starts burning Anthropic credit, this cap is
the back-stop that fires before the bill arrives. The existing
``circuit_breaker["anthropic_credits"]`` (in ``llm_factory.py``) is
reactive — it only trips once Anthropic itself returns a 402 /
"insufficient credit" error. This cap is proactive — it refuses the
*next* call when projected spend would exceed the operator-set
ceiling.

Design constraints
──────────────────

  * Default OFF (``anthropic_daily_cap_usd = None``). Operators flip
    it on once they've watched cost shape for a few weeks and know
    a sane ceiling.
  * Pure-function pre-check: ``pre_check(estimated_usd) -> None``
    raises ``AnthropicDailyCapExceeded`` when the cap would be
    breached, OR is a no-op when disabled.
  * Spend accounting reuses ``app.audit_log`` rolling-24h aggregation
    — same source of truth the React Cost dashboard reads, so the
    cap matches what operators see.
  * Failure-isolated: any error reading the cap / today's spend
    degrades to "no cap" rather than blocking a real call.

Caller contract
───────────────

The high-volume direct callers (Mem0 extractor, structured-diagnosis,
brainstorm seed/react rounds) opt into the gate by calling
``pre_check`` BEFORE invoking ``anthropic.Anthropic().messages.create``.
The factory-routed calls (CrewAI cascade) get it for free once the
factory's chokepoint is wired (separate ship — keeping this module
itself decoupled from llm_factory so the primitive is testable in
isolation).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ── Exception ───────────────────────────────────────────────────────


# ``CapExceededError`` is the provider-neutral base class — moved out
# of this module to :mod:`app.llm_cost_exceptions` (2026-05-26) so
# new providers don't have to cross-import from Anthropic to subclass
# it.  Re-exported here for back-compat with the small set of callers
# that already import ``app.llm_anthropic_budget.CapExceededError``.
from app.llm_cost_exceptions import CapExceededError


class AnthropicDailyCapExceeded(CapExceededError):
    """Raised by :func:`pre_check` when the next Anthropic call would
    push the rolling-24h spend over the configured cap.
    """
    provider = "Anthropic"


# ── Cap reader ──────────────────────────────────────────────────────


def get_cap() -> Optional[float]:
    """Read the operator-set cap. Returns ``None`` when disabled
    (no cap in effect). Failure-isolated — any error reading
    runtime_settings degrades to None.

    The runtime_settings module is imported via ``sys.modules`` so
    tests can swap the implementation. ``from app import runtime_settings``
    would bind the attribute on the ``app`` package on first call,
    making test-time substitution awkward; the ``importlib.import_module``
    path resolves fresh each call.
    """
    try:
        import importlib
        rs = importlib.import_module("app.runtime_settings")
        cap = rs.get_anthropic_daily_cap_usd()
    except Exception:
        return None
    if cap is None:
        return None
    try:
        v = float(cap)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return v


# ── Spend reader ────────────────────────────────────────────────────
#
# ``today_spent_usd`` is read on every Anthropic call through
# :func:`pre_check`, which after the 2026-05-25 cost-wiring is called
# from:
#   * ``AnthropicClientHandle._InstrumentedMessages.create``
#   * ``CreditAwareAnthropicCompletion.call`` (sync)
#   * ``CreditAwareAnthropicCompletion.acall`` (async)
#
# The underlying spend computation does a full audit-log JSONL scan —
# microseconds per row but linear in log size, and after months of
# operation the log has tens of thousands of rows.  Caching is safe
# because the cap is a coarse ceiling, not a precise per-call budget:
# within a 5-second window, even at peak throughput, the unobserved
# spend can't accumulate by more than ~$0.50 worst case (high-cost
# Sonnet at maximum call rate).  That's well below any reasonable
# cap-headroom check, so the cache is operationally lossless while
# eliminating the file-scan cost on hot paths.

_SPENT_CACHE_TTL_SECONDS = 5.0
_spent_cache: dict[str, float] = {}  # {"value": cached_usd, "expires_at": ts}
_spent_cache_lock = threading.Lock()


def today_spent_usd(use_cache: bool = True) -> float:
    """Return rolling-24h Anthropic spend from the canonical ledger.

    Delegates to :func:`app.llm_cost_ledger.spend_for_provider` so the
    "you spent X" number is consistent with the React Cost dashboard
    and the per-role spend ledger.  Failure-isolated: returns 0.0 on
    any error so the gate defaults to "no spend recorded" rather than
    blocking legitimate calls.

    Parameters
    ----------
    use_cache : bool
        When True (default), reads from the canonical ledger's
        5-second TTL cache.  When False, invalidates that cache and
        reads fresh — for the operator-facing state snapshot
        endpoint where the most recent number matters more than
        latency.
    """
    try:
        if not use_cache:
            from app.llm_cost_ledger import _invalidate_for_tests
            _invalidate_for_tests()
        from app.llm_cost_ledger import spend_for_provider
        return spend_for_provider("anthropic", hours=24.0)
    except Exception:
        logger.debug(
            "llm_anthropic_budget: ledger read failed", exc_info=True,
        )
        return 0.0


def _invalidate_spent_cache() -> None:
    """Test-only — wipes the canonical ledger's TTL cache.

    Kept as a thin shim around :func:`app.llm_cost_ledger._invalidate_for_tests`
    so existing tests that import this symbol continue to work.  The
    leading underscore marks this as test-only.
    """
    try:
        from app.llm_cost_ledger import _invalidate_for_tests
        _invalidate_for_tests()
    except Exception:
        pass


# _read_audit_log_anthropic_spend + _row_is_anthropic removed
# 2026-05-25.  They read from a non-existent ``app.audit_log`` module
# (never existed in git history) and silently returned 0.0 — making
# the per-day cap operationally dormant since this module's
# creation.  The fix routes through :mod:`app.llm_cost_ledger` which
# queries the canonical SQLite ``token_usage`` table.
# Provider classification moved to :mod:`app.llm_provider_classify`.


# ── Gate ────────────────────────────────────────────────────────────


def pre_check(estimated_cost_usd: float = 0.0) -> None:
    """Refuse with :class:`AnthropicDailyCapExceeded` when the next
    Anthropic call would push the rolling-24h spend past the cap.

    No-op when the cap is disabled (default), the spend cannot be
    read (failure-isolated), or the estimate would still fit under
    the cap.

    Call this RIGHT BEFORE invoking ``anthropic.Anthropic().messages.create``
    in any high-volume code path. Single-shot calls don't strictly need
    it (the cumulative ceiling already covers them), but it's cheap
    enough that protecting them too costs nothing.
    """
    cap = get_cap()
    if cap is None:
        return  # disabled — no-op
    spent = today_spent_usd()
    try:
        est = float(estimated_cost_usd)
    except (TypeError, ValueError):
        est = 0.0
    if est < 0:
        est = 0.0
    if spent + est > cap:
        raise AnthropicDailyCapExceeded(
            today_spent_usd=spent,
            daily_cap_usd=cap,
            estimated_cost_usd=est,
        )


def call_or_skip(
    estimated_cost_usd: float = 0.0,
    *,
    source: str = "",
) -> bool:
    """Convenience wrapper around :func:`pre_check` for the common
    call-site pattern.

    Returns
    -------
    bool
        ``True`` when the call should proceed (cap not breached OR
        gate disabled OR pre_check itself failed).
        ``False`` when :class:`AnthropicDailyCapExceeded` was raised
        — caller should return its empty-result sentinel.

    Posture
    -------
    Failure-OPEN: anything other than the specific cap-exceeded
    exception is treated as "cap doesn't know what's going on, let
    the call through." Gate bugs must never block legitimate calls.

    Usage
    -----

    ::

        if not llm_anthropic_budget.call_or_skip(
            estimated_cost_usd=0.005, source="brainstorm:idea_evolve",
        ):
            return ""  # caller picks the skip sentinel

        # proceed with the Anthropic call
        client = anthropic.Anthropic()
        ...

    The ``source`` parameter is purely informational — surfaces in
    the skip log line so operators can spot which subsystem hit the
    ceiling.
    """
    try:
        pre_check(estimated_cost_usd=estimated_cost_usd)
        return True
    except AnthropicDailyCapExceeded as exc:
        if source:
            logger.info(
                "Anthropic call skipped (%s): %s", source, exc,
            )
        else:
            logger.info("Anthropic call skipped: %s", exc)
        return False
    except Exception:
        # Failure-OPEN — see docstring posture note.
        logger.debug(
            "llm_anthropic_budget.call_or_skip: pre_check unexpected "
            "error; letting call proceed", exc_info=True,
        )
        return True


def state_snapshot() -> dict:
    """Return ``{cap, spent, headroom, enabled}`` for the operator
    surface (REST endpoint + React Settings card).

    Bypasses the 5-second TTL cache so the operator dashboard always
    shows the most recent spend — staleness on the operator surface
    would be confusing whereas the call-path gate is fine with 5s
    inaccuracy.
    """
    cap = get_cap()
    spent = today_spent_usd(use_cache=False)
    if cap is None:
        return {
            "enabled": False,
            "cap_usd": None,
            "spent_usd_24h": round(spent, 6),
            "headroom_usd": None,
        }
    return {
        "enabled": True,
        "cap_usd": cap,
        "spent_usd_24h": round(spent, 6),
        "headroom_usd": round(max(0.0, cap - spent), 6),
    }


__all__ = [
    "AnthropicDailyCapExceeded",
    "call_or_skip",
    "get_cap",
    "pre_check",
    "state_snapshot",
    "today_spent_usd",
]
