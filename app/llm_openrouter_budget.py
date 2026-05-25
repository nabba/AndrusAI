"""OpenRouter per-day USD spend cap.

Sibling to :mod:`app.llm_anthropic_budget` — closes the per-provider
asymmetry where the factory had a daily-cap gate for Anthropic but
left OpenRouter spend uncapped at sub-monthly granularity (only the
total-cost-ceiling monthly brake engaged for OR).

Same shape:

  * ``OpenRouterDailyCapExceeded`` — typed exception raised by
    :func:`pre_check` when the next OpenRouter call would push
    rolling-24h spend over the operator-configured cap.
  * ``pre_check(estimated_cost_usd)`` — refuses with the typed
    exception, no-op when the cap is disabled.
  * ``today_spent_usd(use_cache=True)`` — rolling-24h OR spend
    from the audit log, 5-second TTL cache.
  * ``state_snapshot()`` — operator-facing
    ``{cap, spent, headroom, enabled}`` for the React Settings card.

What's NOT here that the Anthropic equivalent has:

  * No ``call_or_skip`` wrapper — the migrated site pattern was
    Anthropic-specific historical inertia.  New OpenRouter callers
    should catch :class:`OpenRouterDailyCapExceeded` directly.
  * No per-call wrapping layer like ``CreditAwareAnthropicCompletion``.
    OpenRouter is reached via LiteLLM through ``crewai.LLM(...)``;
    wrapping every LiteLLM-routed LLM would be invasive.  The cap is
    therefore enforced at *construction* time (in ``_try_api``), not
    on every call to a cached LLM.  This is a coarser granularity
    than the Anthropic gate but symmetric with how the
    ``idle_pause_due_to_budget`` brake already gates OR at construction.

Posture
-------

  * Failure-OPEN — any error reading runtime_settings or the audit log
    is treated as "cap doesn't know what's happening, let the call
    through."  A broken cost ledger must NEVER block legitimate calls.
  * Default-OFF — the operator opts in by setting a value via the
    React Settings card.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ── Exception ───────────────────────────────────────────────────────


from app.llm_cost_exceptions import CapExceededError


class OpenRouterDailyCapExceeded(CapExceededError):
    """Raised by :func:`pre_check` when the next OpenRouter call would
    push the rolling-24h spend over the configured cap.

    Inherits from :class:`app.llm_anthropic_budget.CapExceededError`
    so :class:`app.llms.budget_aware.BudgetAwareCompletion` and other
    generic wrappers can catch the family without string-matching
    class names.  Distinct subclass so callers can still catch one
    or both with intent (e.g. only-Anthropic cap handling).
    """
    provider = "OpenRouter"


# ── Cap reader ──────────────────────────────────────────────────────


def get_cap() -> Optional[float]:
    """Read the operator-set cap.  Returns ``None`` when disabled.

    Same import discipline as :func:`app.llm_anthropic_budget.get_cap`
    — uses ``importlib.import_module`` so tests can swap the module
    fresh per call.
    """
    try:
        import importlib
        rs = importlib.import_module("app.runtime_settings")
        cap = rs.get_openrouter_daily_cap_usd()
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
# Same caching strategy as the Anthropic sibling — 5-second TTL.  See
# :mod:`app.llm_anthropic_budget` for the rationale.

_SPENT_CACHE_TTL_SECONDS = 5.0
_spent_cache: dict[str, float] = {}
_spent_cache_lock = threading.Lock()


def today_spent_usd(use_cache: bool = True) -> float:
    """Return rolling-24h OpenRouter spend from the canonical ledger.

    Delegates to :func:`app.llm_cost_ledger.spend_for_provider`.
    Failure-isolated: returns 0.0 on any error.
    """
    try:
        if not use_cache:
            from app.llm_cost_ledger import _invalidate_for_tests
            _invalidate_for_tests()
        from app.llm_cost_ledger import spend_for_provider
        return spend_for_provider("openrouter", hours=24.0)
    except Exception:
        logger.debug(
            "llm_openrouter_budget: ledger read failed", exc_info=True,
        )
        return 0.0


def _invalidate_spent_cache() -> None:
    """Test-only helper — wipes the canonical ledger's TTL cache."""
    try:
        from app.llm_cost_ledger import _invalidate_for_tests
        _invalidate_for_tests()
    except Exception:
        pass


# _read_audit_log_openrouter_spend + _row_is_openrouter removed
# 2026-05-25 alongside the Anthropic sibling — same broken-import
# pattern (read from non-existent ``app.audit_log``).  Provider
# classification now lives in :mod:`app.llm_provider_classify`; spend
# aggregation in :mod:`app.llm_cost_ledger`.


# ── Gate ────────────────────────────────────────────────────────────


def pre_check(estimated_cost_usd: float = 0.0) -> None:
    """Refuse with :class:`OpenRouterDailyCapExceeded` when the next
    OpenRouter call would push rolling-24h spend past the cap.

    No-op when the cap is disabled (default) or any error reading
    spend.  Called at construction time from ``_try_api`` in
    ``app.llm_factory`` so a fresh LLM build is refused when the cap
    is breached; existing cached LLMs continue to serve until evicted.
    """
    cap = get_cap()
    if cap is None:
        return
    spent = today_spent_usd()
    try:
        est = float(estimated_cost_usd)
    except (TypeError, ValueError):
        est = 0.0
    if est < 0:
        est = 0.0
    if spent + est > cap:
        raise OpenRouterDailyCapExceeded(
            today_spent_usd=spent,
            daily_cap_usd=cap,
            estimated_cost_usd=est,
        )


def state_snapshot() -> dict:
    """Operator-facing ``{cap, spent, headroom, enabled}`` snapshot."""
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
    "OpenRouterDailyCapExceeded",
    "get_cap",
    "today_spent_usd",
    "pre_check",
    "state_snapshot",
]
