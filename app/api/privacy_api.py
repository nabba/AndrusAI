"""Privacy aggregator + forget orchestrator REST surface.

Gap #7 (2026-05-24). Two endpoints under ``/api/cp/privacy``:

  * ``GET  /audit/{subject_type}/{subject_id}`` — what does the system
    know about this subject? Walks every adapter, returns per-adapter
    counts + non-sensitive samples + total reference count.
  * ``POST /forget`` — body ``{subject_type, subject_id, confirm_phrase}``
    where ``confirm_phrase`` must literally equal
    ``FORGET <subject_type>:<subject_id>``.

Both are gateway-bearer-secret gated via the standard
``require_gateway_auth`` dependency. The aggregator itself enforces
``privacy_audit_enabled`` runtime switch (off → endpoint returns
``{enabled: false}`` rather than 403; this is a discoverability vs
silence trade-off, the system explicitly surfaces that the feature
is off).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.control_plane.auth_dep import require_gateway_auth

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/cp/privacy",
    tags=["control-plane", "privacy"],
    dependencies=[Depends(require_gateway_auth)],
)


@router.get("/audit/{subject_type}/{subject_id:path}")
async def audit_subject_endpoint(subject_type: str, subject_id: str) -> dict[str, Any]:
    """Walk every adapter for the given subject. Returns aggregated
    references + per-adapter counts + non-sensitive sample snippets."""
    from app.privacy.aggregator import audit_subject, VALID_SUBJECT_TYPES
    if subject_type not in VALID_SUBJECT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"subject_type must be one of {VALID_SUBJECT_TYPES}",
        )
    try:
        return audit_subject(subject_type, subject_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("privacy_audit: aggregator failed", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/forget")
async def forget_subject_endpoint(request: Request) -> dict[str, Any]:
    """Trigger the per-adapter forget path. Body:

        {
          "subject_type": "person" | "domain" | "sender_id",
          "subject_id":   "<id>",
          "confirm_phrase": "FORGET <type>:<id>"
        }

    The confirm phrase is the friction gate — same pattern as the
    governance-ratchet relax flow.
    """
    payload = await request.json()
    subject_type = str(payload.get("subject_type") or "").strip()
    subject_id = str(payload.get("subject_id") or "").strip()
    confirm = str(payload.get("confirm_phrase") or "").strip()
    if not subject_type or not subject_id:
        raise HTTPException(
            status_code=400,
            detail="Both subject_type and subject_id are required.",
        )
    try:
        from app.privacy.aggregator import forget_subject
        return forget_subject(
            subject_type, subject_id, confirm_phrase=confirm,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("privacy_audit: forget failed", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
