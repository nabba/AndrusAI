"""Periodic re-audit — weekly cron job.

For every tool in {SHADOW, CANARY, ACTIVE}, re-run the static audit. Rules
evolve over time (new HARD_BLOCKED_PATH_FRAGMENTS, new bandit checks); this
catches tools that were clean when registered but no longer pass current
policy. Findings are appended to the tool's audit_results so the operator
can see drift over time.

Auto-demotion policy:
  - static fail with hard-blocked node/path → KILL (irreversible). The same
    rule that applies at registration time.
  - static fail with capability mismatch only → demote one tier
    (ACTIVE→CANARY, CANARY→SHADOW, SHADOW→QUARANTINED).

Returns a summary dict so the API can surface it in the UI.
"""
from __future__ import annotations

import logging
from typing import Any

from app.forge.audit.static import run_static_audit
from app.forge.manifest import AuditPhase, ToolManifest, ToolStatus
from app.forge.registry import (
    append_audit_finding, get_tool, list_tools, transition,
)

logger = logging.getLogger(__name__)


_DEMOTE_FROM: dict[ToolStatus, ToolStatus] = {
    ToolStatus.ACTIVE: ToolStatus.CANARY,
    ToolStatus.CANARY: ToolStatus.SHADOW,
    ToolStatus.SHADOW: ToolStatus.QUARANTINED,
}


def _manifest(row: dict[str, Any]) -> ToolManifest:
    raw = row["manifest"]
    if isinstance(raw, str):
        import json
        raw = json.loads(raw)
    return ToolManifest.model_validate(raw)


def run_periodic_reaudit() -> dict[str, Any]:
    """Re-run static audit on every promoted tool. Idempotent."""
    targets: list[dict[str, Any]] = []
    for status in ("SHADOW", "CANARY", "ACTIVE"):
        targets.extend(list_tools(status=status, limit=500))

    summary: dict[str, Any] = {
        "checked": 0,
        "passed": 0,
        "demoted": 0,
        "killed": 0,
        "details": [],
    }

    for slim in targets:
        tool_id = slim["tool_id"]
        # list_tools returns a slim projection; re-fetch the full row so we have
        # manifest + source_code.
        row = get_tool(tool_id)
        if not row:
            continue
        try:
            manifest = _manifest(row)
        except Exception as exc:
            logger.warning("forge.periodic: cannot load manifest for %s: %s", tool_id, exc)
            continue
        source_code = row.get("source_code") or ""

        try:
            finding, _ = run_static_audit(manifest, source_code)
        except Exception:
            logger.exception("forge.periodic: static audit raised for %s", tool_id)
            continue

        # Tag the finding so operators can distinguish periodic from registration
        # without changing the AuditPhase enum (PERIODIC already exists for this).
        finding.phase = AuditPhase.PERIODIC
        append_audit_finding(tool_id, finding)
        summary["checked"] += 1

        if finding.passed:
            summary["passed"] += 1
            continue

        details = finding.details or {}
        ast_violations = details.get("ast_violations", [])
        path_violations = details.get("path_violations", [])
        hard_block = bool(ast_violations or path_violations)

        current = ToolStatus(row["status"])
        if hard_block:
            transition(
                tool_id, ToolStatus.KILLED, actor="forge.periodic",
                reason=f"periodic re-audit found hard-blocked nodes: {finding.summary}",
                audit_data={
                    "ast_violations_count": len(ast_violations),
                    "path_violations_count": len(path_violations),
                },
            )
            summary["killed"] += 1
            summary["details"].append({
                "tool_id": tool_id, "outcome": "killed",
                "from": current.value, "to": "KILLED",
                "reason": finding.summary,
            })
        else:
            target = _DEMOTE_FROM.get(current)
            if target is None:
                continue
            ok = transition(
                tool_id, target, actor="forge.periodic",
                reason=f"periodic re-audit demoted: {finding.summary}",
            )
            if ok:
                summary["demoted"] += 1
                summary["details"].append({
                    "tool_id": tool_id, "outcome": "demoted",
                    "from": current.value, "to": target.value,
                    "reason": finding.summary,
                })

    logger.info(
        "forge.periodic: checked=%d passed=%d demoted=%d killed=%d",
        summary["checked"], summary["passed"],
        summary["demoted"], summary["killed"],
    )
    return summary
