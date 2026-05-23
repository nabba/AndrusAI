"""Control plane — connector-budget surface at
/api/cp/connector-budget.

Read-only view over the per-connector daily spend ledger. Operators
see today's spending across all wrapped connectors without needing
shell access to the JSONL file. The master switch is also exposed
so operators can flip the decorator into pass-through mode if a
connector starts mis-behaving.

  GET /api/cp/connector-budget/state    enabled + today_spend_by_connector

The endpoint is dependency-gated by ``require_gateway_auth``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends

from app.control_plane.auth_dep import require_gateway_auth

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/cp/connector-budget",
    tags=["control-plane", "connector-budget"],
    dependencies=[Depends(require_gateway_auth)],
)


def _safe_enabled() -> bool:
    try:
        from app import runtime_settings
        return runtime_settings.get_connector_budgets_enabled()
    except Exception:
        # Failure-isolated: mirror the decorator's fail-OFF default
        return False


def _safe_aggregate() -> dict[str, dict]:
    try:
        from app.connector_budget import today_spend_all_connectors
        return today_spend_all_connectors()
    except Exception:
        logger.debug(
            "connector_budget_api: aggregate read failed", exc_info=True,
        )
        return {}


def _safe_window_aggregate(days: int = 7) -> dict[str, dict]:
    try:
        from app.connector_budget import window_spend_by_connector
        return window_spend_by_connector(days=days)
    except Exception:
        logger.debug(
            "connector_budget_api: window aggregate failed", exc_info=True,
        )
        return {}


@router.get("/state")
def state_endpoint() -> dict[str, Any]:
    aggregate = _safe_aggregate()
    window = _safe_window_aggregate(days=7)
    # Union the connector key set across today + window — both today's
    # row AND historical-only rows surface. Sort by descending recent
    # spend (window first, falls back to today when window empty).
    all_names = set(aggregate) | set(window)

    def _sort_key(name: str) -> float:
        # Prefer window USD; tiebreak by today USD; then alpha.
        w_usd = float(window.get(name, {}).get("usd", 0.0))
        t_usd = float(aggregate.get(name, {}).get("usd", 0.0))
        return -(w_usd if w_usd > 0 else t_usd)

    sorted_rows = []
    for name in sorted(all_names, key=_sort_key):
        today_b = aggregate.get(name, {})
        window_b = window.get(name, {})
        sorted_rows.append({
            "connector": name,
            "today_spend_usd": round(
                float(today_b.get("usd", 0.0)), 6,
            ),
            "today_calls": int(today_b.get("calls", 0)),
            "today_estimated_calls": int(
                today_b.get("estimated_calls", 0),
            ),
            "recent_spend_usd": round(
                float(window_b.get("usd", 0.0)), 6,
            ),
            "recent_calls": int(window_b.get("calls", 0)),
            "recent_window_days": 7,
        })
    return {
        "enabled": _safe_enabled(),
        "connectors": sorted_rows,
        "total_usd": round(
            sum(r["today_spend_usd"] for r in sorted_rows), 6,
        ),
        "total_calls": sum(r["today_calls"] for r in sorted_rows),
        "total_recent_usd": round(
            sum(r["recent_spend_usd"] for r in sorted_rows), 6,
        ),
        "total_recent_calls": sum(
            r["recent_calls"] for r in sorted_rows
        ),
        "recent_window_days": 7,
    }
