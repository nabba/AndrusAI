"""``@with_connector_budget`` — sync + async decorator factory.

The wrapped function is gated by a daily per-connector USD cap:

  1. Pre-call: if ``today_spend(name) + estimated_cost_usd > daily_cap_usd``
     raise :class:`ConnectorBudgetExceeded`. The cap is INCLUSIVE — a
     day's spend may reach exactly ``daily_cap_usd``; the next call
     that would push past is the one refused. (Set ``daily_cap_usd``
     to one cent less than your true budget if you want to be sure
     never to land exactly at the cap.)
  2. Execute the wrapped function.
  3. Post-call: if ``cost_extractor`` was provided, call it on the
     return value to obtain the actual USD cost; otherwise fall back
     to ``estimated_cost_usd``. Record the spend (with the ``estimated``
     flag set when the fallback fired).

When the master switch ``connector_budgets_enabled`` is OFF, the
decorator is a transparent pass-through — no spend recording, no
pre-check. This makes the decorator safe to add to a call site
proactively; it activates only when the operator flips the switch.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any, Callable, Optional

from app.connector_budget.store import (
    record_spend,
    should_alert_budget_exceeded,
    today_calls,
    today_spend,
    # Phase B.3 (2026-05-22) — expose the Decimal helper so the
    # pre-check comparison is exact at the cap boundary.
    _to_decimal as _to_dec,
)

logger = logging.getLogger(__name__)


class ConnectorBudgetExceeded(Exception):
    """Raised pre-call when the daily cap would be breached.

    Two cap modes (Phase B.4 cleanup, 2026-05-22):

      * USD mode: ``daily_cap_usd`` is set; ``today_spent_usd +
        estimated_cost_usd`` would exceed it.
      * Call-count mode: ``daily_call_cap`` is set; ``today_calls + 1``
        would exceed it.

    Attributes:
      connector: the connector name
      today_spent_usd: how much has already been spent today (0.0 in
        call-count mode)
      daily_cap_usd: the configured USD cap (None in call-count mode)
      estimated_cost_usd: the call's estimate (0.0 in call-count mode)
      today_calls_made: how many calls already today (USD mode populates
        this for context but doesn't gate on it)
      daily_call_cap: the configured call cap (None in USD mode)
    """

    def __init__(
        self,
        connector: str,
        today_spent_usd: float,
        daily_cap_usd: Optional[float],
        estimated_cost_usd: float,
        today_calls_made: int = 0,
        daily_call_cap: Optional[int] = None,
    ) -> None:
        self.connector = connector
        self.today_spent_usd = today_spent_usd
        self.daily_cap_usd = daily_cap_usd
        self.estimated_cost_usd = estimated_cost_usd
        self.today_calls_made = today_calls_made
        self.daily_call_cap = daily_call_cap

        if daily_call_cap is not None:
            msg = (
                f"connector_budget: {connector!r} daily call cap "
                f"{daily_call_cap} would be exceeded — "
                f"{today_calls_made} calls already today"
            )
        else:
            msg = (
                f"connector_budget: {connector!r} daily cap "
                f"${daily_cap_usd:.4f} would be exceeded — "
                f"spent ${today_spent_usd:.4f}, next call estimated "
                f"${estimated_cost_usd:.4f}"
            )
        super().__init__(msg)


def _master_switch_on() -> bool:
    try:
        from app import runtime_settings
        return runtime_settings.get_connector_budgets_enabled()
    except Exception:
        # Failure-isolated: if runtime_settings is sick, the decorator
        # defaults to OFF (pass-through) so we never accidentally block
        # work just because the switch can't be read.
        return False


def _maybe_alert_budget_exceeded(
    *,
    connector: str,
    spent: float,
    cap: float,
    estimate: float,
) -> None:
    """Fire a Signal alert when a daily budget cap is hit — once per
    (connector, UTC day) so we don't spam the operator on repeated
    refusals in the same loop iteration.

    Failure-isolated: notify() failures, store failures, and missing
    notify module all suppress silently. The alert is best-effort —
    the ConnectorBudgetExceeded exception still fires regardless.
    """
    try:
        if not should_alert_budget_exceeded(connector):
            return
    except Exception:
        # If the dedup check is broken, fail open and let the alert fire.
        # Worst case: a repeated alert today. Better than missing the
        # signal entirely.
        pass

    title = f"Connector budget hit: {connector}"
    body = (
        f"`{connector}` daily cap ${cap:.4f} reached.\n"
        f"Today's spend: ${spent:.4f}\n"
        f"Next call estimate: ${estimate:.4f}\n\n"
        "Tune via /cp/settings → Connector budgets → Overrides."
    )
    try:
        from app.notify import notify
        notify(
            title=title,
            body=body,
            url="/cp/settings",
            topic="connector_budget_exceeded",
            tag=f"connector-budget-{connector}",
        )
    except Exception:
        logger.debug(
            "connector_budget: notify failed for %r", connector,
            exc_info=True,
        )


def _resolve_overrides(connector: str) -> dict:
    """Look up per-connector overrides from runtime_settings.

    Returns a possibly-empty dict with optional keys ``daily_cap_usd``
    and ``estimated_cost_usd``. Failure-isolated: a sick
    runtime_settings or missing entry returns an empty dict, and the
    decorator falls back to its hardcoded defaults.
    """
    try:
        from app import runtime_settings
        all_overrides = runtime_settings.get_connector_budget_overrides()
    except Exception:
        return {}
    if not isinstance(all_overrides, dict):
        return {}
    entry = all_overrides.get(connector)
    if not isinstance(entry, dict):
        return {}
    return entry


def with_connector_budget(
    connector: str,
    *,
    daily_cap_usd: Optional[float] = None,
    daily_call_cap: Optional[int] = None,
    estimated_cost_usd: float = 0.0,
    cost_extractor: Optional[Callable[[Any], float]] = None,
) -> Callable:
    """Decorator factory.

    Args:
      connector: stable identifier (e.g. "clearbit", "aviationstack").
        Used as the spend-ledger group key.
      daily_cap_usd: USD-mode cap. Hard ceiling for spend per UTC day.
        Mutually exclusive with ``daily_call_cap``.
      daily_call_cap: call-count-mode cap. Hard ceiling on the number
        of wrapped invocations per UTC day. Phase B.4 cleanup
        (2026-05-22): for connectors that are genuinely free at the
        API boundary (e.g. Aviationstack's free tier) but rate-limited,
        modelling the cap as a synthetic dollar amount produced
        misleading numbers in the operator surface. Call-count mode
        records ``usd=0`` per call and gates on call count instead.
        Mutually exclusive with ``daily_cap_usd``.
      estimated_cost_usd: per-call cost estimate (USD mode only).
        Used for the pre-check and as the recorded amount when
        ``cost_extractor`` is None or raises. Set conservatively
        HIGH so the cap is hit early rather than late. Ignored in
        call-count mode (each call counts as 1 regardless).
      cost_extractor: optional callable that takes the wrapped
        function's return value and returns the actual USD cost.
        Useful for connectors that surface cost in their response
        envelope (e.g. an LLM's usage block). USD mode only.

    The returned decorator works for both sync and async functions.

    Raises ValueError at decoration time if both or neither cap mode
    is supplied — exactly one must win the XOR.
    """

    # XOR check — exactly one cap mode required
    usd_mode = daily_cap_usd is not None
    call_mode = daily_call_cap is not None
    if usd_mode == call_mode:  # both True or both False
        raise ValueError(
            "with_connector_budget: exactly one of daily_cap_usd or "
            "daily_call_cap must be supplied. Got "
            f"daily_cap_usd={daily_cap_usd!r}, "
            f"daily_call_cap={daily_call_cap!r}."
        )

    if usd_mode and daily_cap_usd <= 0:
        raise ValueError(
            f"daily_cap_usd must be positive, got {daily_cap_usd}"
        )
    if call_mode and daily_call_cap <= 0:
        raise ValueError(
            f"daily_call_cap must be positive, got {daily_call_cap}"
        )
    if estimated_cost_usd < 0:
        raise ValueError(
            f"estimated_cost_usd must be >= 0, got {estimated_cost_usd}"
        )

    def _effective_values() -> tuple[float, float]:
        """Apply any operator override on top of the decorator-supplied
        defaults. Lookup happens per-call so the operator's tuning is
        live without process restart. Failure-isolated end-to-end:
        a sick resolver / bogus override values / unexpected exception
        all fall back to the decorator-supplied defaults."""
        try:
            overrides = _resolve_overrides(connector)
        except Exception:
            return daily_cap_usd, estimated_cost_usd
        eff_cap = overrides.get("daily_cap_usd", daily_cap_usd)
        eff_est = overrides.get("estimated_cost_usd", estimated_cost_usd)
        # Belt + suspenders — if the override is bogus, fall back to
        # the decorator defaults rather than crashing the wrapped call.
        try:
            eff_cap = float(eff_cap)
            eff_est = float(eff_est)
            if eff_cap <= 0 or eff_est < 0:
                raise ValueError
        except (TypeError, ValueError):
            return daily_cap_usd, estimated_cost_usd
        return eff_cap, eff_est

    def _pre_check() -> tuple[float, float]:
        # Phase B.4 (2026-05-22) — branch on cap mode.
        # Call-count mode: count today's invocations against the cap.
        # USD mode: sum today's spend + this call's estimate against
        # the dollar cap (Phase B.3's Decimal arithmetic preserved).
        if call_mode:
            calls = today_calls(connector)
            if calls + 1 > daily_call_cap:
                _maybe_alert_budget_exceeded(
                    connector=connector,
                    spent=0.0,
                    cap=float(daily_call_cap),
                    estimate=1.0,
                )
                raise ConnectorBudgetExceeded(
                    connector=connector,
                    today_spent_usd=0.0,
                    daily_cap_usd=None,
                    estimated_cost_usd=0.0,
                    today_calls_made=calls,
                    daily_call_cap=daily_call_cap,
                )
            # Call-count mode records $0 per call — returned tuple
            # has est=0.0 so _post_record persists usd=0 + estimated=False.
            return float(daily_call_cap), 0.0

        # USD mode (existing semantics with Phase B.3 Decimal compare).
        eff_cap, eff_est = _effective_values()
        spent = today_spend(connector)
        if _to_dec(spent) + _to_dec(eff_est) > _to_dec(eff_cap):
            # Fire a once-per-day Signal alert BEFORE raising — so
            # the operator hears about the budget hit even though
            # the per-call exception is typically caught + swallowed
            # by the wrapping loop. Failure-isolated end-to-end.
            _maybe_alert_budget_exceeded(
                connector=connector,
                spent=spent,
                cap=eff_cap,
                estimate=eff_est,
            )
            raise ConnectorBudgetExceeded(
                connector=connector,
                today_spent_usd=spent,
                daily_cap_usd=eff_cap,
                estimated_cost_usd=eff_est,
            )
        return eff_cap, eff_est

    def _post_record(result: Any, eff_est: float) -> None:
        actual: Optional[float] = None
        if cost_extractor is not None:
            try:
                actual = float(cost_extractor(result))
            except Exception:
                logger.debug(
                    "connector_budget: cost_extractor failed for %r, "
                    "falling back to estimate",
                    connector, exc_info=True,
                )
                actual = None
        if actual is None:
            record_spend(connector, eff_est, estimated=True)
        else:
            record_spend(connector, actual, estimated=False)

    def _decorate(fn: Callable) -> Callable:
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def _async_wrapper(*args, **kwargs):
                if not _master_switch_on():
                    return await fn(*args, **kwargs)
                _eff_cap, eff_est = _pre_check()
                result = await fn(*args, **kwargs)
                _post_record(result, eff_est)
                return result
            return _async_wrapper

        @functools.wraps(fn)
        def _sync_wrapper(*args, **kwargs):
            if not _master_switch_on():
                return fn(*args, **kwargs)
            _eff_cap, eff_est = _pre_check()
            result = fn(*args, **kwargs)
            _post_record(result, eff_est)
            return result
        return _sync_wrapper

    return _decorate
