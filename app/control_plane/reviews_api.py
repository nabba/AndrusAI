"""Control plane — two-reasoner review log at /api/cp/reviews.

Phase 4 piece 2b (2026-05-20). Operator surface for the two-reasoner
review primitive shipped in Phase 4 piece 2. Lets operators inspect
the audit trail of independent LLM safety verdicts:

  GET    /api/cp/reviews                  list recent reviews
  GET    /api/cp/reviews?verdict=disagree filter by aggregated verdict
  GET    /api/cp/reviews/{review_id}      detail of one review

Auth: same ``require_gateway_auth`` dependency as the rest of /cp/.

The endpoints are read-only — every review is produced by upstream
callers invoking ``two_reasoner.review_text``. The REST surface
exposes the audit JSONL so operators don't need shell access to
inspect the trail.

Use cases
─────────

* **"Why did the system file this CR with a warning?"** —
  filter by DISAGREE to see proposals where the two reasoners
  reached opposite verdicts.
* **"Are we getting useful signal from the review?"** — scan the
  full list to see whether the two reasoners actually disagree on
  meaningfully ambiguous proposals (good signal) or always agree
  on near-identical wording (low signal — time to tune the
  prompts or add a cross-vendor reasoner).
* **"Show me everything classified UNSAFE last week"** — operators
  can see what proposals would have been blocked if upstream had
  honored the verdict.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.control_plane.auth_dep import require_gateway_auth
from app.risk_classifier.two_reasoner import (
    ReviewOutcome,
    Verdict,
    list_reviews,
)

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/cp/reviews",
    tags=["control-plane", "two-reasoner"],
    dependencies=[Depends(require_gateway_auth)],
)


def _serialize(outcome: ReviewOutcome) -> dict[str, Any]:
    """Render a review outcome for the REST surface. Wraps to_dict
    + adds nothing — the on-disk shape IS what the operator wants."""
    return outcome.to_dict()


@router.get("")
def list_endpoint(
    verdict: str | None = Query(
        default=None,
        description=(
            "Filter by aggregated verdict: safe, unsafe, uncertain, "
            "disagree, or disabled. Default = no filter."
        ),
    ),
    limit: int = Query(default=100, ge=1, le=500),
):
    """List the most-recent reviews. Newest first."""
    verdict_enum: Verdict | None = None
    if verdict:
        try:
            verdict_enum = Verdict(verdict.lower())
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"invalid verdict filter {verdict!r}. Valid: "
                    f"{[v.value for v in Verdict]}"
                ),
            )

    # Over-fetch a bit when filtering so a popular verdict doesn't
    # short-change the operator. Cap at 5x to keep the read bounded.
    fetch_limit = limit * 5 if verdict_enum is not None else limit
    fetch_limit = min(fetch_limit, 2000)

    all_reviews = list_reviews(limit=fetch_limit)
    if verdict_enum is not None:
        filtered = [r for r in all_reviews if r.verdict is verdict_enum]
    else:
        filtered = all_reviews
    out = filtered[:limit]
    return {
        "count": len(out),
        "total_scanned": len(all_reviews),
        "filter_verdict": verdict_enum.value if verdict_enum else None,
        "reviews": [_serialize(r) for r in out],
    }


@router.get("/{review_id}")
def get_endpoint(review_id: str):
    """Detail view of one review by id. 404 when unknown."""
    if not review_id:
        raise HTTPException(status_code=400, detail="review_id required")
    # Load + scan — the JSONL is unordered for retrieval. Cap the
    # search at 5000 reviews; older entries are out of scope for the
    # operator-facing view.
    for r in list_reviews(limit=5000):
        if r.review_id == review_id:
            return _serialize(r)
    raise HTTPException(
        status_code=404,
        detail=f"review {review_id!r} not found",
    )


@router.get("/stats/summary")
def stats_summary():
    """Aggregate counts by verdict over the recent review window.

    Useful for the React page's "summary chip row" — at a glance, is
    the system seeing mostly SAFE / DISAGREE / UNSAFE outcomes?
    """
    reviews = list_reviews(limit=2000)
    counts: dict[str, int] = {v.value: 0 for v in Verdict}
    for r in reviews:
        counts[r.verdict.value] = counts.get(r.verdict.value, 0) + 1
    return {
        "total": len(reviews),
        "by_verdict": counts,
    }
