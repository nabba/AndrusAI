"""Anthropic per-day cap REST surface at /api/cp/anthropic-budget
(Phase D.3, 2026-05-22).

Read + write surface for the vendor-level Anthropic spend cap. Three
endpoints:

  * ``GET  /state``    — current cap + rolling-24h spend + headroom
  * ``POST /cap``      — set or clear the cap (body: ``{cap_usd: float|null}``)
  * ``POST /pre-check`` — operator-initiated dry-run of the gate
    (useful from the dashboard "would this estimate be refused?" probe).

Mirrors the connector_budget surface for shape consistency. All endpoints
are guarded by ``require_gateway_auth``.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.control_plane.auth_dep import require_gateway_auth

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/cp/anthropic-budget",
    tags=["control-plane", "anthropic-budget"],
    dependencies=[Depends(require_gateway_auth)],
)


class _SetCapBody(BaseModel):
    cap_usd: Optional[float] = Field(
        default=None,
        description=(
            "USD ceiling for rolling-24h Anthropic spend. Null or a "
            "non-positive value disables the cap."
        ),
    )


class _PreCheckBody(BaseModel):
    estimated_cost_usd: float = Field(
        default=0.0,
        description="Estimate for the next call.",
        ge=0.0,
    )


@router.get("/state")
def state_endpoint() -> dict[str, Any]:
    """Return the current state snapshot for the React Settings card.

    Shape matches :func:`app.llm_anthropic_budget.state_snapshot` plus
    a ``ok: True`` flag for cheap caller-side health checks.
    """
    try:
        from app import llm_anthropic_budget
        s = llm_anthropic_budget.state_snapshot()
    except Exception as exc:
        logger.warning("anthropic_budget_api.state failed: %s", exc)
        return {
            "enabled": False,
            "cap_usd": None,
            "spent_usd_24h": 0.0,
            "headroom_usd": None,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    s["ok"] = True
    return s


@router.post("/cap")
def set_cap_endpoint(body: _SetCapBody) -> dict[str, Any]:
    """Set or clear the cap. Returns the new state snapshot."""
    try:
        from app import runtime_settings
        runtime_settings.set_anthropic_daily_cap_usd(body.cap_usd)
    except Exception as exc:
        logger.warning("anthropic_budget_api.set_cap failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"failed to set cap: {type(exc).__name__}: {exc}",
        )
    return state_endpoint()


@router.post("/pre-check")
def pre_check_endpoint(body: _PreCheckBody) -> dict[str, Any]:
    """Run the gate as a dry-run probe — useful from the dashboard
    to ask "would this estimate be refused right now?"

    Returns:
      ``{would_refuse: bool, today_spent_usd, cap_usd, estimated, headroom}``.
    """
    try:
        from app import llm_anthropic_budget
        snap = llm_anthropic_budget.state_snapshot()
        try:
            llm_anthropic_budget.pre_check(
                estimated_cost_usd=body.estimated_cost_usd,
            )
            would_refuse = False
            reason = ""
        except llm_anthropic_budget.AnthropicDailyCapExceeded as exc:
            would_refuse = True
            reason = str(exc)
    except Exception as exc:
        logger.warning("anthropic_budget_api.pre_check failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"pre-check failed: {type(exc).__name__}: {exc}",
        )
    return {
        "would_refuse": would_refuse,
        "reason": reason,
        "estimated_cost_usd": body.estimated_cost_usd,
        "cap_usd": snap.get("cap_usd"),
        "spent_usd_24h": snap.get("spent_usd_24h"),
        "headroom_usd": snap.get("headroom_usd"),
        "enabled": snap.get("enabled", False),
    }
