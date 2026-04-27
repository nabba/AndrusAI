"""FastAPI router for /api/forge/*.

Endpoints:
  GET    /api/forge/state               — env + override + effective + counts
  GET    /api/forge/tools                — list (filter by status)
  GET    /api/forge/tools/{tool_id}      — detail (manifest, code, audits, eval, invocations)
  POST   /api/forge/tools                — register a tool (runs audit pipeline)
  POST   /api/forge/tools/{tool_id}/kill — kill switch (sticky)
  POST   /api/forge/tools/{tool_id}/audit/rerun — re-run static + semantic audits
  POST   /api/forge/settings/override    — set runtime override toggle
  GET    /api/forge/audit-log            — global audit log
  GET    /api/forge/audit-log/{tool_id}  — per-tool audit log
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.forge.audit.pipeline import run_registration_audits
from app.forge.composition import audit_composition, list_compositions
from app.forge.config import get_forge_config
from app.forge.killswitch import get_effective_state, invalidate_cache
from app.forge.manifest import (
    Capability, GeneratorMetadata, ToolManifest, ToolStatus, compute_tool_id,
)
from app.forge.registry import (
    count_tools_by_status, get_tool, kill_tool, list_audit_log,
    list_invocations, list_tools, register_tool, set_setting, total_tool_count,
    transition,
)
from app.forge.runtime.dispatcher import invoke_tool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/forge", tags=["forge"])


# ── Schemas ─────────────────────────────────────────────────────────────────


class RegisterToolRequest(BaseModel):
    name: str
    description: str = ""
    source_type: Literal["declarative", "python_sandbox"]
    source_code: str
    capabilities: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    returns: dict[str, Any] = Field(default_factory=dict)
    domain_allowlist: list[str] = Field(default_factory=list)
    generator: dict[str, Any] = Field(default_factory=dict)


class KillRequest(BaseModel):
    reason: str = ""


class OverrideRequest(BaseModel):
    enabled: bool | None = None
    dry_run: bool | None = None


class InvokeRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)
    caller_crew_id: str | None = None
    caller_agent: str | None = "ui"
    request_id: str | None = None
    composition_id: str | None = None


class PromoteRequest(BaseModel):
    # SHADOW only valid as a manual override out of QUARANTINED — e.g. when the
    # LLM judge was unavailable, a human can review and grant promotion.
    target: Literal["SHADOW", "CANARY", "ACTIVE"]
    reason: str = ""


class DemoteRequest(BaseModel):
    target: Literal["SHADOW", "CANARY", "DEPRECATED"]
    reason: str = ""


class CompositionAuditRequest(BaseModel):
    composition_id: str
    tool_ids: list[str]
    call_graph: dict[str, Any] | None = None


# ── Endpoints ───────────────────────────────────────────────────────────────


@router.get("/state")
def get_state() -> dict[str, Any]:
    """Resolved global state. Drives the UI's settings page banner."""
    cfg = get_forge_config()
    eff = get_effective_state()
    counts = count_tools_by_status()
    total = total_tool_count()
    return {
        "env": {
            "enabled": cfg.enabled,
            "require_human_promotion": cfg.require_human_promotion,
            "max_tools": cfg.max_tools,
            "max_calls_per_tool_per_hour": cfg.max_calls_per_tool_per_hour,
            "max_tools_per_plan": cfg.max_tools_per_plan,
            "audit_llm": cfg.audit_llm,
            "shadow_runs_required": cfg.shadow_runs_required,
            "dry_run": cfg.dry_run,
            "composition_risk_threshold": cfg.composition_risk_threshold,
            "blocked_domains": cfg.blocked_domains,
            "allowed_domains": cfg.allowed_domains,
        },
        "effective": {
            "env_enabled": eff.env_enabled,
            "runtime_enabled": eff.runtime_enabled,
            "enabled": eff.effective_enabled,
            "dry_run": eff.dry_run,
            "explanation": eff.explanation,
        },
        "counts": counts,
        "total_tools": total,
        "registry_full": total >= cfg.max_tools,
    }


@router.get("/tools")
def get_tools(
    status: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    rows = list_tools(status=status, limit=limit)
    return {"tools": rows, "count": len(rows)}


@router.get("/tools/{tool_id}")
def get_tool_detail(tool_id: str) -> dict[str, Any]:
    row = get_tool(tool_id)
    if not row:
        raise HTTPException(status_code=404, detail="tool not found")
    invocations = list_invocations(tool_id, limit=50)
    audit = list_audit_log(tool_id=tool_id, limit=200)
    return {
        "tool": row,
        "invocations": invocations,
        "audit_log": audit,
    }


@router.post("/tools")
def register_new_tool(req: RegisterToolRequest) -> dict[str, Any]:
    """Register a tool and run static + semantic audits.

    Runs even if forge is disabled — registration without execution is safe
    and lets the UI show what would have been generated. Execution is gated
    elsewhere by ``is_invocation_allowed``.
    """
    cfg = get_forge_config()
    if total_tool_count() >= cfg.max_tools:
        raise HTTPException(status_code=409, detail="forge registry full")

    declared = []
    for c in req.capabilities:
        try:
            declared.append(Capability(c))
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"unknown capability: {c}",
            )

    tool_id = compute_tool_id(
        name=req.name,
        source_type=req.source_type,
        source_code=req.source_code,
        capabilities=req.capabilities,
    )

    gen_meta = GeneratorMetadata(**req.generator) if req.generator else GeneratorMetadata()

    manifest = ToolManifest(
        tool_id=tool_id,
        name=req.name,
        description=req.description,
        source_type=req.source_type,
        capabilities=declared,
        parameters=req.parameters,
        returns=req.returns,
        domain_allowlist=req.domain_allowlist,
        generator=gen_meta,
    )

    register_tool(manifest, source_code=req.source_code, actor=gen_meta.agent or "api")
    run_registration_audits(tool_id)

    return {"tool_id": tool_id, "status": get_tool(tool_id)["status"]}


@router.post("/tools/{tool_id}/kill")
def kill_endpoint(tool_id: str, req: KillRequest) -> dict[str, Any]:
    if not get_tool(tool_id):
        raise HTTPException(status_code=404, detail="tool not found")
    ok = kill_tool(tool_id, actor="ui", reason=req.reason or "manual kill via UI")
    invalidate_cache()
    return {"killed": ok}


@router.post("/tools/{tool_id}/audit/rerun")
def rerun_audit(tool_id: str) -> dict[str, Any]:
    if not get_tool(tool_id):
        raise HTTPException(status_code=404, detail="tool not found")
    run_registration_audits(tool_id)
    return {"tool_id": tool_id, "status": get_tool(tool_id)["status"]}


@router.post("/settings/override")
def set_override(req: OverrideRequest) -> dict[str, Any]:
    if req.enabled is not None:
        set_setting("forge_runtime_enabled", req.enabled, actor="ui")
    if req.dry_run is not None:
        set_setting("forge_runtime_dry_run", req.dry_run, actor="ui")
    invalidate_cache()
    return {"effective": get_effective_state().__dict__}


@router.post("/tools/{tool_id}/promote")
def promote_tool(tool_id: str, req: PromoteRequest) -> dict[str, Any]:
    row = get_tool(tool_id)
    if not row:
        raise HTTPException(status_code=404, detail="tool not found")
    target = ToolStatus(req.target)
    ok = transition(
        tool_id, target, actor="ui",
        reason=req.reason or f"manual promote to {target.value}",
    )
    if not ok:
        raise HTTPException(
            status_code=409,
            detail=f"cannot promote {row['status']} → {target.value} (state machine)",
        )
    invalidate_cache()
    return {"tool_id": tool_id, "status": get_tool(tool_id)["status"]}


@router.post("/tools/{tool_id}/demote")
def demote_tool(tool_id: str, req: DemoteRequest) -> dict[str, Any]:
    row = get_tool(tool_id)
    if not row:
        raise HTTPException(status_code=404, detail="tool not found")
    target = ToolStatus(req.target)
    ok = transition(
        tool_id, target, actor="ui",
        reason=req.reason or f"manual demote to {target.value}",
    )
    if not ok:
        raise HTTPException(
            status_code=409,
            detail=f"cannot demote {row['status']} → {target.value} (state machine)",
        )
    invalidate_cache()
    return {"tool_id": tool_id, "status": get_tool(tool_id)["status"]}


@router.post("/composition/audit")
def composition_audit_endpoint(req: CompositionAuditRequest) -> dict[str, Any]:
    return audit_composition(req.composition_id, req.tool_ids, req.call_graph)


@router.get("/compositions")
def get_compositions(limit: int = Query(default=100, ge=1, le=500)):
    rows = list_compositions(limit=limit)
    return {"compositions": rows, "count": len(rows)}


@router.post("/tools/{tool_id}/invoke")
def invoke_endpoint(tool_id: str, req: InvokeRequest) -> dict[str, Any]:
    """Invoke a forged tool. Killswitch + budget + capability guards apply."""
    if not get_tool(tool_id):
        raise HTTPException(status_code=404, detail="tool not found")
    return invoke_tool(
        tool_id,
        req.params,
        caller_crew_id=req.caller_crew_id,
        caller_agent=req.caller_agent,
        request_id=req.request_id,
        composition_id=req.composition_id,
    )


@router.post("/maintenance/run/{job}")
def run_maintenance(job: Literal["periodic", "anomaly", "integrity"]) -> dict[str, Any]:
    """Run a forge maintenance job on demand. Same code paths the cron uses."""
    if job == "periodic":
        from app.forge.audit.periodic import run_periodic_reaudit
        return {"job": "periodic", "result": run_periodic_reaudit()}
    if job == "anomaly":
        from app.forge.anomaly import run_anomaly_check
        return {"job": "anomaly", "result": run_anomaly_check()}
    if job == "integrity":
        from app.forge.integrity import verify_audit_chain
        return {"job": "integrity", "result": verify_audit_chain()}
    raise HTTPException(status_code=400, detail="unknown job")


@router.get("/maintenance/integrity")
def get_integrity() -> dict[str, Any]:
    from app.forge.integrity import verify_audit_chain
    return verify_audit_chain()


@router.get("/audit-log")
def get_audit_log_global(limit: int = Query(default=200, ge=1, le=1000)):
    rows = list_audit_log(tool_id=None, limit=limit)
    return {"entries": rows, "count": len(rows)}


@router.get("/audit-log/{tool_id}")
def get_audit_log_for_tool(tool_id: str, limit: int = Query(default=200, ge=1, le=1000)):
    rows = list_audit_log(tool_id=tool_id, limit=limit)
    return {"entries": rows, "count": len(rows)}
