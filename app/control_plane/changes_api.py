"""Control plane — change-request endpoints at /api/cp/changes.

Phase 5.3a backend. Operators (via React or curl) can:

  GET    /api/cp/changes                    list (filtered by status)
  GET    /api/cp/changes/{id}                detail
  POST   /api/cp/changes/{id}/approve        approve + apply (gate 1)
  POST   /api/cp/changes/{id}/reject         reject (terminal)
  POST   /api/cp/changes/{id}/rollback       rollback (APPLIED only)
  POST   /api/cp/changes/{id}/retry-apply    retry after APPLY_FAILED

Auth: same `require_gateway_auth` dependency as the rest of /cp/.

These endpoints are the "React surface" of the change-request
system. The Signal surface (👍/👎 reactions) is handled by the
reaction-handler hook in main.py. Both surfaces dispatch through
the same lifecycle module — the audit log records which source
made each decision.

When operator React-side approves a change that already has a
Signal ASK out, the lifecycle's idempotent transitions handle
the race correctly: whoever wins first becomes the decided_by;
the loser sees "already approved" / "already rejected".
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.change_requests import (
    DecisionSource,
    Status,
    apply_change,
    approve,
    get,
    is_protected,
    list_all,
    reject,
    rollback_change,
)
from app.control_plane.auth_dep import require_gateway_auth

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/cp/changes",
    tags=["control-plane", "change-requests"],
    dependencies=[Depends(require_gateway_auth)],
)


# ── Request models for POST endpoints ───────────────────────────────


class _ApproveBody(BaseModel):
    operator: str = Field(
        default="react-operator",
        description="Identifier for the operator approving via React.",
    )
    reason: str | None = Field(
        default=None,
        description="Optional approval note (e.g. 'reviewed and looks correct').",
    )


class _RejectBody(BaseModel):
    operator: str = Field(default="react-operator")
    reason: str | None = Field(
        default=None,
        description="Required-for-good-hygiene rejection reason.",
    )


class _RollbackBody(BaseModel):
    operator: str = Field(
        default="react-operator",
        description="Identifier for the operator triggering the rollback.",
    )


# ── Helpers ─────────────────────────────────────────────────────────


def _serialize(cr) -> dict[str, Any]:
    """Tighten the dict shape for the React UI — adds derived fields
    that are awkward to compute client-side."""
    if cr is None:
        return {}
    d = cr.to_dict()
    d["is_terminal"] = cr.is_terminal
    d["is_rollbackable"] = cr.is_rollbackable
    d["is_protected"] = is_protected(cr.path)
    return d


# ── Routes ──────────────────────────────────────────────────────────


@router.get("")
def list_changes(
    status: str | None = Query(
        default=None,
        description=(
            "Filter by status (pending, approved, rejected, applied, "
            "apply_failed, rolled_back, tier_immutable_refused, timeout)."
        ),
    ),
    limit: int = Query(default=100, ge=1, le=500),
):
    """List change requests, newest first, optionally filtered by status."""
    status_enum: Status | None = None
    if status:
        try:
            status_enum = Status(status)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"invalid status {status!r}. Valid: "
                    f"{[s.value for s in Status]}"
                ),
            )
    items = list_all(status=status_enum, limit=limit)
    # Phase 3 v2 follow-up (2026-05-22) — single-pass type-error
    # count map so list rows can render a badge without N round trips.
    # Failure-isolated: helper returns empty dict on any error.
    try:
        from app.coding_session.submit import build_type_error_count_map
        type_error_counts = build_type_error_count_map()
    except Exception:
        type_error_counts = {}
    rows = []
    for c in items:
        d = _serialize(c)
        cnt = type_error_counts.get(c.id, 0)
        if cnt > 0:
            d["type_error_count"] = cnt
        rows.append(d)
    return {
        "count": len(items),
        "changes": rows,
    }


@router.get("/{request_id}")
def get_change(request_id: str):
    cr = get(request_id)
    if cr is None:
        raise HTTPException(status_code=404, detail=f"change request {request_id!r} not found")
    return _serialize(cr)


@router.get("/{request_id}/review")
def get_change_review(request_id: str):
    """Return the most-recent two-reasoner review for this CR.

    Phase 4 piece 2c (2026-05-20). The review hook in
    ``change_requests.lifecycle.create_request`` fires when the CR's
    classified zone is high-stakes. The outcome lands in the
    two_reasoner audit log with the CR id as its ``context_id``.

    Returns:
      * 200 with the review outcome (matching the shape from
        ``/api/cp/reviews``).
      * 404 when the CR doesn't exist OR no review has been
        recorded for it.

    Note: a missing review can mean either "the CR's zone is not
    high-stakes" or "the master switch is off — no review fired."
    Operators can disambiguate via /cp/settings.
    """
    cr = get(request_id)
    if cr is None:
        raise HTTPException(
            status_code=404,
            detail=f"change request {request_id!r} not found",
        )
    from app.risk_classifier.two_reasoner import find_review_for_context
    outcome = find_review_for_context(request_id)
    if outcome is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no two-reasoner review recorded for CR {request_id!r} "
                "(zone may be low-stakes, master switch may be off, "
                "or audit log may have been rotated)"
            ),
        )
    return outcome.to_dict()


@router.get("/{request_id}/type-errors")
def get_change_type_errors(request_id: str):
    """Return pyright type-check results for this CR, if any.

    Phase 3 v2 follow-up (2026-05-22). When a coding session submits
    with ``with_type_check=True``, each per-file ``SubmitResult``
    captures the error-severity pyright diagnostics. This endpoint
    cross-references the CR id back to the originating session and
    surfaces those diagnostics so operators can see type-error
    context at the gate moment.

    Returns:
      * 200 with ``{session_id, path, submitted_at, type_errors}``.
        ``type_errors`` may be an empty list — that means "type
        check ran clean," not "no type check ran."
      * 404 when the CR doesn't exist, OR no coding session
        references this CR id in its submit_results.

    Note: a 404 can mean either "not from a coding session" (the
    CR was filed by another path, e.g. the agent's
    ``request_restricted_write`` tool) or "session did not opt into
    ``with_type_check``" or "session was discarded before submitting."
    """
    cr = get(request_id)
    if cr is None:
        raise HTTPException(
            status_code=404,
            detail=f"change request {request_id!r} not found",
        )
    from app.coding_session.submit import find_type_errors_for_cr
    payload = find_type_errors_for_cr(request_id)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no type-check data recorded for CR {request_id!r} "
                "(not from a coding session, session opted out of "
                "type-check, or session was discarded)"
            ),
        )
    return payload


@router.post("/{request_id}/check-types")
def check_types_now(request_id: str):
    """Run pyright against the CR's proposed new_content immediately.

    Phase 3 v2 follow-up (2026-05-22). Unlike the read-only /type-errors
    endpoint which surfaces type-errors recorded by a coding session,
    this endpoint runs a FRESH pyright pass against the CR's new_content
    regardless of where the CR came from. Useful for:
      * Agent-direct CRs (request_restricted_write) that bypass the
        coding-session flow entirely.
      * Operator verification after the originating session's
        type-check is stale.

    Cooperative response (always 200 unless CR is unknown):
      * ``{ran: true, diagnostics: [...]}`` — pyright completed
      * ``{ran: false, reason: "..."}`` — disabled / binary missing /
        non-.py path / write failure / pyright crash

    Never raises out — the React button shows the operator-readable
    reason instead of an opaque HTTP error.
    """
    cr = get(request_id)
    if cr is None:
        raise HTTPException(
            status_code=404,
            detail=f"change request {request_id!r} not found",
        )
    if not cr.path.endswith(".py"):
        return {
            "ran": False,
            "reason": f"path {cr.path!r} is not a Python file",
        }

    # Lazy imports — keep the endpoint dormant when sidecar unavailable
    try:
        from app.code_intel.pyright_sidecar import (
            check_paths,
            is_available,
        )
    except Exception as exc:
        return {"ran": False, "reason": f"sidecar import failed: {exc}"}

    if not is_available():
        return {
            "ran": False,
            "reason": (
                "pyright binary not on PATH. Install pyright in the "
                "gateway image."
            ),
        }

    # Write the proposed content to a temp file and run pyright on it.
    # Use the file's basename so pyright reports paths the operator
    # recognises rather than the /tmp/... random path.
    import os
    import tempfile
    from pathlib import Path

    base = os.path.basename(cr.path) or "proposed.py"
    try:
        with tempfile.TemporaryDirectory(prefix="cr-typecheck-") as tmp:
            tmp_path = Path(tmp) / base
            tmp_path.write_text(cr.new_content or "", encoding="utf-8")
            report = check_paths([tmp_path], cwd=tmp_path.parent)
    except Exception as exc:
        return {"ran": False, "reason": f"check failed: {exc}"}

    if report.disabled:
        return {"ran": False, "reason": "sidecar disabled"}
    if report.timed_out:
        return {
            "ran": False,
            "reason": f"timed out after {report.duration_s:.1f}s",
        }
    if report.error:
        return {"ran": False, "reason": report.error}

    return {
        "ran": True,
        "path": cr.path,
        "diagnostics": [d.to_dict() for d in report.diagnostics],
        "error_count": len(report.errors),
        "warning_count": len(report.warnings),
        "duration_s": round(report.duration_s, 3),
        # config_root is empty string when pyright ran with defaults
        # (no pyrightconfig.json / pyproject.toml discovered above the
        # tmp file). When non-empty, it points to the project root
        # whose config pyright applied — useful debugging context.
        "config_root": report.config_root,
    }


@router.post("/{request_id}/approve")
def approve_change(request_id: str, body: _ApproveBody):
    """Approve + apply. The lifecycle moves PENDING → APPROVED → APPLIED
    (or APPLY_FAILED). Idempotent: a second approve on an already-
    APPROVED request just retries the apply."""
    cr = get(request_id)
    if cr is None:
        raise HTTPException(status_code=404, detail="not found")
    if cr.status == Status.TIER_IMMUTABLE_REFUSED:
        raise HTTPException(
            status_code=403,
            detail=(
                "TIER_IMMUTABLE files cannot be approved through this "
                "path, even by operator React override. Operator must "
                "edit directly via PR."
            ),
        )
    if cr.status not in (Status.PENDING, Status.APPROVED, Status.APPLY_FAILED):
        raise HTTPException(
            status_code=409,
            detail=(
                f"cannot approve in status {cr.status.value}. "
                f"Already-applied / rolled-back / timed-out / rejected "
                f"requests cannot be re-approved."
            ),
        )
    # PENDING → APPROVED
    if cr.status == Status.PENDING:
        approve(
            request_id,
            source=DecisionSource.REACT_APPROVE,
            decision_reason=body.reason,
        )
    # APPROVED or APPLY_FAILED → trigger apply
    apply_result = apply_change(request_id)
    final = get(request_id)
    return {
        "ok": apply_result.ok,
        "change": _serialize(final),
        "apply_result": {
            "ok": apply_result.ok,
            "git_branch": apply_result.git_branch,
            "git_commit_sha": apply_result.git_commit_sha,
            "pr_url": apply_result.pr_url,
            "module_reload_ok": apply_result.module_reload_ok,
            "module_reload_note": apply_result.module_reload_note,
            "error": apply_result.error,
        },
    }


@router.post("/{request_id}/reject")
def reject_change(request_id: str, body: _RejectBody):
    cr = get(request_id)
    if cr is None:
        raise HTTPException(status_code=404, detail="not found")
    if cr.status != Status.PENDING:
        raise HTTPException(
            status_code=409,
            detail=f"cannot reject in status {cr.status.value}",
        )
    updated = reject(
        request_id,
        source=DecisionSource.REACT_REJECT,
        decision_reason=body.reason,
    )
    return {"ok": True, "change": _serialize(updated)}


@router.post("/{request_id}/rollback")
def rollback_route(request_id: str, body: _RollbackBody):
    """Roll back an APPLIED change. Reverts the commit + hot-reverts
    the file + opens a revert PR. Operator merges the revert PR to
    make the rollback durable in main."""
    cr = get(request_id)
    if cr is None:
        raise HTTPException(status_code=404, detail="not found")
    if not cr.is_rollbackable:
        raise HTTPException(
            status_code=409,
            detail=(
                f"cannot rollback in status {cr.status.value}. Only "
                f"APPLIED requests can be rolled back."
            ),
        )
    result = rollback_change(request_id, operator=body.operator)
    final = get(request_id)
    return {
        "ok": result.ok,
        "change": _serialize(final),
        "rollback_result": {
            "ok": result.ok,
            "revert_branch": result.git_branch,
            "revert_commit_sha": result.git_commit_sha,
            "revert_pr_url": result.pr_url,
            "module_reload_ok": result.module_reload_ok,
            "module_reload_note": result.module_reload_note,
            "error": result.error,
        },
    }


@router.post("/{request_id}/retry-apply")
def retry_apply(request_id: str):
    """Retry the apply step for an APPLY_FAILED request. Same code
    path as approve()'s apply call; useful when the original failure
    was transient (bridge briefly unreachable, etc)."""
    cr = get(request_id)
    if cr is None:
        raise HTTPException(status_code=404, detail="not found")
    if cr.status != Status.APPLY_FAILED:
        raise HTTPException(
            status_code=409,
            detail=(
                f"retry-apply only allowed for APPLY_FAILED; "
                f"current={cr.status.value}"
            ),
        )
    # Move back to APPROVED so apply_change accepts it; lifecycle
    # tolerates the re-approval as idempotent.
    approve(
        request_id,
        source=DecisionSource.REACT_APPROVE,
        decision_reason="retry after apply failure",
    )
    result = apply_change(request_id)
    final = get(request_id)
    return {
        "ok": result.ok,
        "change": _serialize(final),
        "apply_result": {
            "ok": result.ok,
            "git_branch": result.git_branch,
            "git_commit_sha": result.git_commit_sha,
            "pr_url": result.pr_url,
            "error": result.error,
        },
    }
