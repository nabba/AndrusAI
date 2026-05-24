"""discovery_funnel — observation → adoption funnel surface.

Gap #6 (2026-05-24). Surfaces the funnel counts the briefing operator
otherwise has to query manually: how many discoveries entered the
system in the last 90 days, how many became CRs, how many were
applied. The briefing only renders this candidate when something is
actionable (stagnant sources surface; otherwise a one-line headline
is enough).

Composes with — does not replace — the existing ``paper-picks``
candidate, which surfaces the *content* of individual findings. This
candidate is the *flow rate*.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


ID = "discovery-funnel"
DISPLAY_NAME = "📊 Discovery → adoption"
DESCRIPTION = (
    "Per-source counts of staged proposals → CRs filed → CRs applied "
    "across the last 90 days. Surfaces stagnant sources (≥5 stagings, "
    "0 applied) so the operator sees when observation is producing "
    "no action."
)

_WINDOW_DAYS = 90


def gather() -> list[str]:
    try:
        from app.observability.discovery_funnel import compute
    except Exception:
        logger.debug("discovery_funnel: import failed", exc_info=True)
        return []
    try:
        result = compute(window_days=_WINDOW_DAYS)
    except Exception:
        logger.debug("discovery_funnel: compute failed", exc_info=True)
        return []

    sources = result.get("sources") or []
    totals = result.get("totals") or {}
    if not sources:
        return []
    if (totals.get("staged") or 0) == 0 and (totals.get("cr_filed") or 0) == 0:
        return []

    out: list[str] = [
        (
            f"  • {len(sources)} source(s): "
            f"{totals.get('staged') or 0} staged · "
            f"{totals.get('cr_filed') or 0} CR filed · "
            f"{totals.get('cr_applied') or 0} applied · "
            f"{totals.get('cr_rejected') or 0} rejected · "
            f"{totals.get('cr_pending') or 0} pending"
        ),
    ]
    stagnant = result.get("stagnant_sources") or []
    if stagnant:
        out.append(
            "  ⚠ Stagnant (≥5 staged, 0 applied): "
            + ", ".join(f"`{s}`" for s in stagnant)
        )
    return out
