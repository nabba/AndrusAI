"""Control plane — trust-widening proposal endpoints at /api/cp/widening.

Phase 4 piece 1b (2026-05-20). Operator surface for the widening
proposer shipped in Phase 4 piece 1. Lets operators:

  GET    /api/cp/widening                  list pending proposals
  GET    /api/cp/widening/all              list all (pending + decided)
  GET    /api/cp/widening/{id}             detail of one proposal + its
                                           decision (if any)
  POST   /api/cp/widening/{id}/approve     approve + apply widening
  POST   /api/cp/widening/{id}/reject      reject (no setting change)

Auth: same ``require_gateway_auth`` dependency as the rest of /cp/.

Idempotency semantics
─────────────────────
* Approving an already-approved proposal returns 200 with the
  existing decision record (no new audit row, no double-widen).
* Rejecting an already-rejected proposal returns 200 with the
  existing record.
* Cross-state operations (approve a rejected proposal, reject an
  approved proposal) return 409 — the operator's prior intent has
  been honored and a new proposal will be emitted if evidence
  accumulates further.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.control_plane.auth_dep import require_gateway_auth
from app.risk_classifier import widening_decisions
from app.risk_classifier.widening import (
    WideningProposal,
    list_proposals,
)
from app.risk_classifier.widening_decisions import (
    DecisionStatus,
    decision_for,
    mark_approved,
    mark_rejected,
    pending_proposals,
)

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/cp/widening",
    tags=["control-plane", "widening"],
    dependencies=[Depends(require_gateway_auth)],
)


# ── Request models ──────────────────────────────────────────────────


class _DecisionBody(BaseModel):
    operator: str = Field(
        default="react-operator",
        description="Identifier for the operator making the decision.",
    )
    reason: str = Field(
        default="",
        max_length=500,
        description="Optional explanation (especially useful on reject).",
    )


# ── Helpers ─────────────────────────────────────────────────────────


def _serialize_with_decision(
    proposal: WideningProposal,
) -> dict[str, Any]:
    """Tighten the dict shape with the current decision status (if
    any). React renders this directly."""
    d = proposal.to_dict()
    decision = decision_for(proposal.proposal_id)
    if decision is None:
        d["decision_status"] = DecisionStatus.PENDING.value
        d["decision"] = None
    else:
        d["decision_status"] = decision.status.value
        d["decision"] = decision.to_dict()
    return d


def _find_proposal(proposal_id: str) -> WideningProposal | None:
    for p in list_proposals(limit=2000):
        if p.proposal_id == proposal_id:
            return p
    return None


# ── Routes ──────────────────────────────────────────────────────────


@router.get("")
def list_pending(
    limit: int = Query(default=50, ge=1, le=500),
):
    """List proposals the operator hasn't decided on yet. Newest first."""
    items = pending_proposals(limit=limit)
    return {
        "count": len(items),
        "proposals": [_serialize_with_decision(p) for p in items],
    }


@router.get("/all")
def list_all_proposals(
    limit: int = Query(default=100, ge=1, le=500),
):
    """List all proposals (pending + decided). Newest first."""
    items = list_proposals(limit=limit)
    return {
        "count": len(items),
        "proposals": [_serialize_with_decision(p) for p in items],
    }


@router.get("/{proposal_id}")
def get_proposal(proposal_id: str):
    """Detail view of one proposal + its current decision."""
    p = _find_proposal(proposal_id)
    if p is None:
        raise HTTPException(
            status_code=404,
            detail=f"proposal {proposal_id!r} not found",
        )
    return _serialize_with_decision(p)


@router.post("/{proposal_id}/approve")
def approve_proposal(proposal_id: str, body: _DecisionBody):
    """Approve + apply the widening. Idempotent on already-approved;
    409 on already-rejected."""
    try:
        decision = mark_approved(
            proposal_id,
            operator=body.operator or "react-operator",
            reason=body.reason or "",
        )
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"proposal {proposal_id!r} not found",
        )
    except ValueError as exc:
        # ``already rejected``, ``unknown list_name``, etc.
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        # runtime_settings setter raised (rate limit, validation)
        logger.exception(
            "widening_api: approve raised for %s", proposal_id,
        )
        raise HTTPException(
            status_code=500,
            detail=f"approve failed: {type(exc).__name__}: {exc}",
        )
    p = _find_proposal(proposal_id)
    if p is None:
        # Shouldn't happen — mark_approved looked it up successfully
        raise HTTPException(status_code=500, detail="post-decision lookup failed")
    return _serialize_with_decision(p)


@router.post("/{proposal_id}/reject")
def reject_proposal(proposal_id: str, body: _DecisionBody):
    """Reject the proposal (no setting change). Idempotent on
    already-rejected; 409 on already-approved."""
    try:
        decision = mark_rejected(
            proposal_id,
            operator=body.operator or "react-operator",
            reason=body.reason or "",
        )
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"proposal {proposal_id!r} not found",
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    p = _find_proposal(proposal_id)
    if p is None:
        raise HTTPException(status_code=500, detail="post-decision lookup failed")
    return _serialize_with_decision(p)
