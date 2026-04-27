"""Top-level invocation dispatcher.

Every forged-tool call goes through ``invoke_tool``:
  1. Resolve killswitch + tool status
  2. Check per-tool budget
  3. Dispatch to declarative or python_sandbox runtime
  4. Record the invocation in forge_invocations (capabilities used vs declared,
     duration, error, mode, output hash + size)
  5. Return a structured InvocationResult

Mode handling:
  - SHADOW: runs, records telemetry, returns result with shadow_mode flag set
  - CANARY / ACTIVE: same as shadow but caller is expected to use the result
  - DEPRECATED: still runs but flagged
  - DRAFT / QUARANTINED / KILLED: refused at killswitch step
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.control_plane.db import execute
from app.forge.config import get_forge_config
from app.forge.killswitch import get_effective_state, is_invocation_allowed
from app.forge.manifest import Capability, ToolManifest, ToolStatus
from app.forge.registry import get_tool
from app.forge.runtime.budget import check_budget
from app.forge.runtime.declarative import (
    DeclarativeRuntimeError, run_declarative,
)
from app.forge.runtime.python_sandbox import run_python_sandbox

logger = logging.getLogger(__name__)


def _manifest_from_row(row: dict[str, Any]) -> ToolManifest:
    raw = row["manifest"]
    if isinstance(raw, str):
        raw = json.loads(raw)
    return ToolManifest.model_validate(raw)


def _record_invocation(
    *,
    tool_id: str,
    tool_version: int,
    caller_crew_id: str | None,
    caller_agent: str | None,
    request_id: str | None,
    composition_id: str | None,
    inputs_redacted: dict[str, Any] | None,
    output_hash: str | None,
    output_size: int | None,
    capabilities_declared: list[str],
    capabilities_used: list[str],
    capability_violations: list[str],
    duration_ms: int,
    error: str | None,
    mode: str,
) -> None:
    execute(
        """
        INSERT INTO forge_invocations
            (tool_id, tool_version, caller_crew_id, caller_agent, request_id,
             composition_id, inputs_redacted, output_hash, output_size,
             capabilities_declared, capabilities_used, capability_violations,
             duration_ms, error, mode)
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            tool_id, tool_version, caller_crew_id, caller_agent, request_id,
            composition_id,
            json.dumps(inputs_redacted) if inputs_redacted is not None else None,
            output_hash, output_size,
            capabilities_declared, capabilities_used, capability_violations,
            duration_ms, error, mode,
        ),
    )


def _redact_inputs(params: dict[str, Any]) -> dict[str, Any]:
    """Drop obvious credential-looking fields before storing."""
    out: dict[str, Any] = {}
    for k, v in (params or {}).items():
        kl = k.lower()
        if any(t in kl for t in ("key", "token", "secret", "password", "auth", "bearer")):
            out[k] = "[redacted]"
        elif isinstance(v, str) and len(v) > 1024:
            out[k] = v[:1024] + "...[truncated]"
        else:
            out[k] = v
    return out


def invoke_tool(
    tool_id: str,
    params: dict[str, Any] | None = None,
    *,
    caller_crew_id: str | None = None,
    caller_agent: str | None = None,
    request_id: str | None = None,
    composition_id: str | None = None,
) -> dict[str, Any]:
    """Run a forged tool. Always logs an invocation row, even on refusal."""
    params = params or {}
    cfg = get_forge_config()
    state = get_effective_state()

    # 1. Killswitch / status
    allowed, reason = is_invocation_allowed(tool_id)
    if not allowed:
        # Log the refusal so the operator can see attempted-but-blocked calls.
        row = get_tool(tool_id)
        ver = int(row["version"]) if row else 0
        _record_invocation(
            tool_id=tool_id, tool_version=ver,
            caller_crew_id=caller_crew_id, caller_agent=caller_agent,
            request_id=request_id, composition_id=composition_id,
            inputs_redacted=_redact_inputs(params),
            output_hash=None, output_size=None,
            capabilities_declared=[], capabilities_used=[],
            capability_violations=[reason],
            duration_ms=0, error=f"refused: {reason}",
            mode="refused",
        )
        return {
            "ok": False, "result": None, "error": reason,
            "mode": "refused", "shadow_mode": False, "elapsed_ms": 0,
        }

    row = get_tool(tool_id)
    if not row:
        return {
            "ok": False, "result": None, "error": "tool not found",
            "mode": "refused", "shadow_mode": False, "elapsed_ms": 0,
        }

    # 2. Budget
    ok, budget_reason = check_budget(tool_id)
    if not ok:
        _record_invocation(
            tool_id=tool_id, tool_version=int(row["version"]),
            caller_crew_id=caller_crew_id, caller_agent=caller_agent,
            request_id=request_id, composition_id=composition_id,
            inputs_redacted=_redact_inputs(params),
            output_hash=None, output_size=None,
            capabilities_declared=[], capabilities_used=[],
            capability_violations=[budget_reason],
            duration_ms=0, error=f"budget: {budget_reason}",
            mode="refused",
        )
        return {
            "ok": False, "result": None, "error": budget_reason,
            "mode": "refused", "shadow_mode": False, "elapsed_ms": 0,
        }

    # 3. Dispatch
    manifest = _manifest_from_row(row)
    declared_caps = [
        c.value if isinstance(c, Capability) else str(c)
        for c in manifest.capabilities
    ]
    status = ToolStatus(row["status"])
    mode = status.value.lower()
    is_shadow = status == ToolStatus.SHADOW
    dry_run = state.dry_run

    if dry_run:
        _record_invocation(
            tool_id=tool_id, tool_version=int(row["version"]),
            caller_crew_id=caller_crew_id, caller_agent=caller_agent,
            request_id=request_id, composition_id=composition_id,
            inputs_redacted=_redact_inputs(params),
            output_hash=None, output_size=None,
            capabilities_declared=declared_caps,
            capabilities_used=[], capability_violations=[],
            duration_ms=0, error=None,
            mode="dry_run",
        )
        return {
            "ok": True, "result": None,
            "error": None, "mode": "dry_run", "shadow_mode": False,
            "elapsed_ms": 0,
            "note": "dry-run mode is on; tool not actually executed",
        }

    source_code = row.get("source_code") or ""
    try:
        if manifest.source_type == "declarative":
            outcome = run_declarative(
                manifest, source_code, params,
                blocked_domains=cfg.blocked_domains,
            )
        elif manifest.source_type == "python_sandbox":
            outcome = run_python_sandbox(manifest, source_code, params)
        else:
            outcome = {
                "ok": False, "result": None,
                "error": f"unknown source_type: {manifest.source_type}",
                "elapsed_ms": 0, "capability_used": None,
                "resolved_ip": None, "status_code": None,
            }
    except DeclarativeRuntimeError as exc:
        outcome = {
            "ok": False, "result": None,
            "error": f"declarative recipe error: {exc}",
            "elapsed_ms": 0, "capability_used": None,
            "resolved_ip": None, "status_code": None,
        }
    except Exception as exc:
        logger.exception("forge.dispatcher: runtime error for %s", tool_id)
        outcome = {
            "ok": False, "result": None,
            "error": f"runtime error: {type(exc).__name__}: {exc}",
            "elapsed_ms": 0, "capability_used": None,
            "resolved_ip": None, "status_code": None,
        }

    used = [outcome["capability_used"]] if outcome.get("capability_used") else []
    violations: list[str] = []
    if outcome.get("violation"):
        violations.append(str(outcome["violation"]))
    if not outcome.get("ok") and outcome.get("error", "").startswith("capability guard refused"):
        violations.append(outcome["error"])

    output_size = None
    output_hash = None
    if outcome.get("ok") and outcome.get("result") is not None:
        try:
            blob = json.dumps(outcome["result"], default=str).encode("utf-8")
            output_size = len(blob)
            output_hash = hashlib.sha256(blob).hexdigest()
        except (TypeError, ValueError):
            pass

    _record_invocation(
        tool_id=tool_id, tool_version=int(row["version"]),
        caller_crew_id=caller_crew_id, caller_agent=caller_agent,
        request_id=request_id, composition_id=composition_id,
        inputs_redacted=_redact_inputs(params),
        output_hash=output_hash, output_size=output_size,
        capabilities_declared=declared_caps,
        capabilities_used=used, capability_violations=violations,
        duration_ms=int(outcome.get("elapsed_ms") or 0),
        error=outcome.get("error"),
        mode=mode,
    )

    return {
        "ok": bool(outcome.get("ok")),
        "result": outcome.get("result") if not is_shadow else None,
        "shadow_result": outcome.get("result") if is_shadow else None,
        "error": outcome.get("error"),
        "mode": mode,
        "shadow_mode": is_shadow,
        "elapsed_ms": int(outcome.get("elapsed_ms") or 0),
        "capability_used": outcome.get("capability_used"),
        "resolved_ip": outcome.get("resolved_ip"),
        "status_code": outcome.get("status_code"),
    }
