"""Per-connector daily budget caps.

Complementary to ``app.control_plane.budgets`` (per-agent monthly caps
in Postgres). This module gates EXTERNAL CONNECTOR consumption — third-
party APIs, MCP servers, scraped endpoints — where the per-call cost is
typically small but spending can drift up over the day if a workflow
loops or retries.

Use:

    from app.connector_budget import with_connector_budget

    @with_connector_budget(
        "clearbit",
        daily_cap_usd=2.0,
        estimated_cost_usd=0.05,
    )
    def fetch_company(domain: str) -> dict:
        return clearbit.lookup(domain)

When the master switch ``connector_budgets_enabled`` is OFF (the
default), the decorator is a transparent pass-through. When ON, a
pre-call check raises :class:`ConnectorBudgetExceeded` if the next
estimated call would push the day's spend over the cap. Post-call,
either ``cost_extractor(result)`` or the ``estimated_cost_usd`` value
is appended to the spend ledger.

The decorator supports both sync and async callables.
"""

from app.connector_budget.decorator import (
    ConnectorBudgetExceeded,
    with_connector_budget,
)
from app.connector_budget.store import (
    record_spend,
    reset_for_tests,
    should_alert_budget_exceeded,
    today_calls,
    today_spend,
    today_spend_all_connectors,
    window_spend_by_connector,
)

__all__ = [
    "ConnectorBudgetExceeded",
    "record_spend",
    "reset_for_tests",
    "should_alert_budget_exceeded",
    "today_calls",
    "today_spend",
    "today_spend_all_connectors",
    "window_spend_by_connector",
    "with_connector_budget",
]
