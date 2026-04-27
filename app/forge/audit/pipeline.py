"""Audit pipeline orchestrator.

Phases run in order; any failure stops promotion. Outcomes:
  - All pass → DRAFT promoted to SHADOW (manual canary→active still required)
  - Any fail → tool stays QUARANTINED with full audit details captured
  - Hard violations (forbidden caps, blocked imports) → KILLED immediately

Idempotent: re-running does not duplicate findings; it appends fresh ones
with new timestamps so history is preserved.
"""
from __future__ import annotations

import logging
from typing import Any

from app.forge.audit.semantic import run_semantic_audit
from app.forge.audit.static import run_static_audit
from app.forge.manifest import (
    AuditPhase, Capability, FORBIDDEN_CAPABILITIES, ToolManifest, ToolStatus,
)
from app.forge.registry import (
    append_audit_finding, get_tool, set_security_eval, set_summary, transition,
)
from app.forge.summary import build_summary

logger = logging.getLogger(__name__)


def _manifest_from_row(row: dict[str, Any]) -> ToolManifest:
    raw = row["manifest"]
    if isinstance(raw, str):
        import json
        raw = json.loads(raw)
    return ToolManifest.model_validate(raw)


def _has_forbidden_capability(manifest: ToolManifest) -> str | None:
    for cap in manifest.capabilities:
        token = cap.value if isinstance(cap, Capability) else str(cap)
        if token in FORBIDDEN_CAPABILITIES:
            return token
    return None


def run_registration_audits(tool_id: str) -> dict[str, Any]:
    """Run static + semantic audits against a registered tool, advance state.

    Returns a dict with the resulting status and any failure reasons. Always
    re-reads the tool from the DB so the caller sees the post-audit state.
    """
    row = get_tool(tool_id)
    if not row:
        return {"error": "tool not found"}
    manifest = _manifest_from_row(row)
    source_code = row.get("source_code") or ""

    # Pre-check: forbidden capability declared → instant kill, no further audits.
    forbidden = _has_forbidden_capability(manifest)
    if forbidden:
        transition(
            tool_id, ToolStatus.KILLED, actor="forge.audit",
            reason=f"forbidden capability declared: {forbidden}",
            audit_data={"forbidden_capability": forbidden},
        )
        return {"status": ToolStatus.KILLED.value, "killed": True,
                "reason": f"forbidden capability: {forbidden}"}

    # ── Phase A: static ────────────────────────────────────────────────────
    static_finding, detected_caps = run_static_audit(manifest, source_code)
    append_audit_finding(tool_id, static_finding)

    # Generate a plain-language summary as soon as we have a manifest the
    # static auditor accepted as syntactically valid. Even when later phases
    # block promotion, the operator should see a readable description.
    if static_finding.passed:
        try:
            summary_text, summary_source = build_summary(manifest, source_code)
            set_summary(tool_id, summary_text, summary_source)
        except Exception:
            logger.exception("forge.pipeline: summary generation failed for %s", tool_id)

    if not static_finding.passed:
        # Hard-blocked AST nodes / path fragments → kill, not just quarantine.
        details = static_finding.details or {}
        ast_violations = details.get("ast_violations", [])
        path_violations = details.get("path_violations", [])
        if ast_violations or path_violations:
            transition(
                tool_id, ToolStatus.KILLED, actor="forge.audit",
                reason="static audit found hard-blocked nodes",
                audit_data={
                    "ast_violations": ast_violations,
                    "path_violations": path_violations,
                },
            )
            return {"status": ToolStatus.KILLED.value, "killed": True,
                    "reason": static_finding.summary}
        # Capability mismatch only → quarantine, can be fixed by re-declaration.
        # (Per state machine, DRAFT can transition to QUARANTINED.)
        transition(
            tool_id, ToolStatus.QUARANTINED, actor="forge.audit",
            reason=static_finding.summary,
        )
        return {"status": ToolStatus.QUARANTINED.value, "killed": False,
                "reason": static_finding.summary}

    # ── Phase B: semantic ──────────────────────────────────────────────────
    semantic_finding, security_eval = run_semantic_audit(manifest, source_code)
    set_security_eval(tool_id, security_eval)
    append_audit_finding(tool_id, semantic_finding)

    if not semantic_finding.passed:
        # Quarantine — judge said no or risk too high. Human can review and
        # decide whether to override (Phase 4 UI: kill or re-run).
        # First DRAFT must transition to QUARANTINED (state machine doesn't
        # allow DRAFT->QUARANTINED-via-failure to be skipped on success path).
        # If we're past static and got here, current status is still DRAFT.
        transition(
            tool_id, ToolStatus.QUARANTINED, actor="forge.audit",
            reason=semantic_finding.summary,
            audit_data={"risk_score": security_eval.risk_score},
        )
        return {"status": ToolStatus.QUARANTINED.value, "killed": False,
                "reason": semantic_finding.summary}

    # ── All passed: DRAFT → QUARANTINED → SHADOW ──────────────────────────
    # Two-step transition matches the state machine: passing static gets you
    # to QUARANTINED, passing semantic promotes to SHADOW. Each step audited.
    transition(
        tool_id, ToolStatus.QUARANTINED, actor="forge.audit",
        reason="static audit passed",
    )
    transition(
        tool_id, ToolStatus.SHADOW, actor="forge.audit",
        reason="semantic audit passed",
        audit_data={"risk_score": security_eval.risk_score},
    )
    return {
        "status": ToolStatus.SHADOW.value,
        "killed": False,
        "static_score": static_finding.score,
        "semantic_score": semantic_finding.score,
        "risk_score": security_eval.risk_score,
    }
