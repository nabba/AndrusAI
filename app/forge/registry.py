"""Forge registry — DB-backed CRUD with state machine + hash-chained audit log.

The audit log is append-only and hash-chained: each row's `entry_hash`
covers `prev_hash || event payload`. Detecting tampering reduces to walking
the chain from the most recent entry back.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.control_plane.db import execute, execute_one, execute_scalar
from app.forge.manifest import (
    AuditFinding, SecurityEval, ToolManifest, ToolStatus,
)

logger = logging.getLogger(__name__)


# Forward transitions only. KILLED is sticky and reachable from any state.
_VALID_TRANSITIONS: dict[ToolStatus, set[ToolStatus]] = {
    ToolStatus.DRAFT: {ToolStatus.QUARANTINED, ToolStatus.KILLED},
    ToolStatus.QUARANTINED: {ToolStatus.SHADOW, ToolStatus.KILLED},
    ToolStatus.SHADOW: {ToolStatus.CANARY, ToolStatus.KILLED, ToolStatus.QUARANTINED},
    ToolStatus.CANARY: {ToolStatus.ACTIVE, ToolStatus.KILLED, ToolStatus.SHADOW},
    ToolStatus.ACTIVE: {ToolStatus.DEPRECATED, ToolStatus.KILLED, ToolStatus.CANARY},
    ToolStatus.DEPRECATED: {ToolStatus.KILLED},
    ToolStatus.KILLED: set(),  # terminal — no resurrection without regeneration
}


# ── Hash chain ──────────────────────────────────────────────────────────────

def _compute_entry_hash(prev_hash: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256((prev_hash + canonical).encode("utf-8")).hexdigest()


def _last_audit_hash() -> str:
    row = execute_one(
        "SELECT entry_hash FROM forge_audit_log ORDER BY id DESC LIMIT 1",
    )
    return row["entry_hash"] if row else ""


def _append_audit(
    tool_id: str | None,
    event_type: str,
    actor: str,
    reason: str = "",
    from_status: str | None = None,
    to_status: str | None = None,
    audit_data: dict[str, Any] | None = None,
) -> None:
    prev = _last_audit_hash()
    payload = {
        "tool_id": tool_id,
        "event_type": event_type,
        "actor": actor,
        "reason": reason,
        "from_status": from_status,
        "to_status": to_status,
        "audit_data": audit_data or {},
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    entry_hash = _compute_entry_hash(prev, payload)
    execute(
        """
        INSERT INTO forge_audit_log
            (tool_id, event_type, from_status, to_status, actor, reason,
             audit_data, prev_hash, entry_hash)
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
        """,
        (
            tool_id, event_type, from_status, to_status, actor, reason,
            json.dumps(audit_data or {}), prev, entry_hash,
        ),
    )


# ── CRUD ────────────────────────────────────────────────────────────────────

def register_tool(
    manifest: ToolManifest,
    source_code: str,
    actor: str = "system",
) -> dict[str, Any]:
    """Create a new tool row in DRAFT. Returns the inserted row.

    The audit pipeline is invoked separately; this is the unconditional write.
    """
    execute(
        """
        INSERT INTO forge_tools
            (tool_id, name, version, status, source_type, description,
             manifest, source_code, generator_metadata)
        VALUES (%s, %s, %s, 'DRAFT', %s, %s, %s::jsonb, %s, %s::jsonb)
        """,
        (
            manifest.tool_id,
            manifest.name,
            manifest.version,
            manifest.source_type,
            manifest.description,
            manifest.model_dump_json(),
            source_code,
            manifest.generator.model_dump_json(),
        ),
    )
    _append_audit(
        tool_id=manifest.tool_id,
        event_type="registered",
        actor=actor,
        to_status="DRAFT",
        audit_data={"name": manifest.name, "version": manifest.version},
    )
    return get_tool(manifest.tool_id)


def get_tool(tool_id: str) -> dict[str, Any] | None:
    return execute_one(
        "SELECT * FROM forge_tools WHERE tool_id = %s",
        (tool_id,),
    )


def list_tools(
    status: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    if status:
        rows = execute(
            """
            SELECT tool_id, name, version, status, source_type, description,
                   risk_score, created_at, updated_at, status_changed_at,
                   killed_at, killed_reason
            FROM forge_tools
            WHERE status = %s
            ORDER BY status_changed_at DESC
            LIMIT %s
            """,
            (status, limit), fetch=True,
        )
    else:
        rows = execute(
            """
            SELECT tool_id, name, version, status, source_type, description,
                   risk_score, created_at, updated_at, status_changed_at,
                   killed_at, killed_reason
            FROM forge_tools
            ORDER BY status_changed_at DESC
            LIMIT %s
            """,
            (limit,), fetch=True,
        )
    return rows or []


def count_tools_by_status() -> dict[str, int]:
    rows = execute(
        "SELECT status, COUNT(*) AS n FROM forge_tools GROUP BY status",
        fetch=True,
    )
    return {row["status"]: int(row["n"]) for row in (rows or [])}


# ── State transitions ───────────────────────────────────────────────────────

def transition(
    tool_id: str,
    to_status: ToolStatus,
    actor: str,
    reason: str = "",
    audit_data: dict[str, Any] | None = None,
) -> bool:
    """Move a tool to a new status if the transition is valid.

    KILLED is sticky and unreachable from itself — once killed, gone.
    """
    row = get_tool(tool_id)
    if not row:
        return False
    current = ToolStatus(row["status"])
    if to_status not in _VALID_TRANSITIONS[current]:
        logger.warning(
            "forge: refusing %s -> %s for %s",
            current.value, to_status.value, tool_id,
        )
        return False

    killed_clauses = ""
    params: tuple
    if to_status == ToolStatus.KILLED:
        killed_clauses = ", killed_at = NOW(), killed_reason = %s"
        params = (
            to_status.value, reason or "killed", tool_id,
        )
        sql = f"""
            UPDATE forge_tools
            SET status = %s, status_changed_at = NOW(), updated_at = NOW()
                {killed_clauses}
            WHERE tool_id = %s
        """
    else:
        params = (to_status.value, tool_id)
        sql = """
            UPDATE forge_tools
            SET status = %s, status_changed_at = NOW(), updated_at = NOW()
            WHERE tool_id = %s
        """
    execute(sql, params)

    _append_audit(
        tool_id=tool_id,
        event_type="transition",
        actor=actor,
        reason=reason,
        from_status=current.value,
        to_status=to_status.value,
        audit_data=audit_data,
    )
    return True


def kill_tool(tool_id: str, actor: str, reason: str) -> bool:
    """Force a tool to KILLED. Sticky — not reversible."""
    row = get_tool(tool_id)
    if not row:
        return False
    if row["status"] == ToolStatus.KILLED.value:
        return True  # idempotent
    return transition(tool_id, ToolStatus.KILLED, actor, reason or "manual_kill")


# ── Audit results ───────────────────────────────────────────────────────────

def append_audit_finding(tool_id: str, finding: AuditFinding) -> None:
    """Append an AuditFinding to the tool's audit_results JSONB array."""
    execute(
        """
        UPDATE forge_tools
        SET audit_results = audit_results || %s::jsonb,
            updated_at = NOW()
        WHERE tool_id = %s
        """,
        (json.dumps([finding.model_dump(mode="json")]), tool_id),
    )
    _append_audit(
        tool_id=tool_id,
        event_type=f"audit.{finding.phase.value}",
        actor="forge.audit",
        reason=finding.summary,
        audit_data={
            "passed": finding.passed,
            "score": finding.score,
            "details_keys": list(finding.details.keys()),
        },
    )


def set_summary(tool_id: str, summary: str, source: str) -> None:
    execute(
        """
        UPDATE forge_tools
        SET summary = %s, summary_source = %s, updated_at = NOW()
        WHERE tool_id = %s
        """,
        (summary, source, tool_id),
    )
    _append_audit(
        tool_id=tool_id, event_type="summary.generated", actor="forge.summary",
        reason=f"summary_source={source}",
    )


def set_security_eval(tool_id: str, security_eval: SecurityEval) -> None:
    execute(
        """
        UPDATE forge_tools
        SET security_eval = %s::jsonb,
            risk_score = %s,
            updated_at = NOW()
        WHERE tool_id = %s
        """,
        (
            security_eval.model_dump_json(),
            float(security_eval.risk_score),
            tool_id,
        ),
    )


# ── Settings (runtime override) ─────────────────────────────────────────────

def get_setting(key: str) -> Any | None:
    row = execute_one(
        "SELECT value FROM forge_settings WHERE key = %s", (key,),
    )
    if not row:
        return None
    return row["value"]


def set_setting(key: str, value: Any, actor: str = "ui") -> None:
    execute(
        """
        INSERT INTO forge_settings (key, value, updated_at, updated_by)
        VALUES (%s, %s::jsonb, NOW(), %s)
        ON CONFLICT (key) DO UPDATE
        SET value = EXCLUDED.value, updated_at = NOW(), updated_by = EXCLUDED.updated_by
        """,
        (key, json.dumps(value), actor),
    )
    _append_audit(
        tool_id=None, event_type="setting.update", actor=actor,
        reason=key, audit_data={"key": key, "value": value},
    )


# ── Audit-log queries ───────────────────────────────────────────────────────

def list_audit_log(
    tool_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    if tool_id:
        rows = execute(
            """
            SELECT id, tool_id, event_type, from_status, to_status, actor,
                   reason, audit_data, entry_hash, created_at
            FROM forge_audit_log
            WHERE tool_id = %s
            ORDER BY id DESC
            LIMIT %s
            """,
            (tool_id, limit), fetch=True,
        )
    else:
        rows = execute(
            """
            SELECT id, tool_id, event_type, from_status, to_status, actor,
                   reason, audit_data, entry_hash, created_at
            FROM forge_audit_log
            ORDER BY id DESC
            LIMIT %s
            """,
            (limit,), fetch=True,
        )
    return rows or []


def list_invocations(tool_id: str, limit: int = 100) -> list[dict[str, Any]]:
    rows = execute(
        """
        SELECT id, tool_id, tool_version, caller_crew_id, caller_agent,
               request_id, composition_id, output_size, capabilities_used,
               capability_violations, duration_ms, error, mode, created_at
        FROM forge_invocations
        WHERE tool_id = %s
        ORDER BY id DESC
        LIMIT %s
        """,
        (tool_id, limit), fetch=True,
    )
    return rows or []


def total_tool_count() -> int:
    return int(execute_scalar("SELECT COUNT(*) FROM forge_tools") or 0)
