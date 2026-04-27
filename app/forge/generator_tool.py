"""``forge_create_tool`` — CrewAI BaseTool that lets an agent register a new
forged tool through the standard pipeline.

Gating layers (any one denies):
  - TOOL_FORGE_ENABLED must be true
  - TOOL_FORGE_AGENT_GENERATION_ENABLED must be true (separate from invocation)
  - per-tool registry cap (TOOL_FORGE_MAX_TOOLS)
  - capability list must not include any FORBIDDEN_CAPABILITIES (ToolManifest
    pydantic validation handles this implicitly via the enum)

The tool always lands in DRAFT, then is moved by the audit pipeline to
QUARANTINED → SHADOW based on outcomes. Promotion past SHADOW remains
manual (UI button) regardless of agent permissions — the boundary that
matters most: the agent can produce a tool but not unilaterally run it
in production.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Type

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class _ForgeCreateToolInputs(BaseModel):
    """Schema CrewAI uses to validate the agent's invocation."""
    name: str = Field(
        description="Short identifier for the tool (snake_case).",
    )
    description: str = Field(
        default="",
        description="One-line description of what the tool does.",
    )
    source_type: str = Field(
        description="Either 'declarative' (JSON HTTP recipe) or 'python_sandbox'.",
    )
    source_code: str = Field(
        description=(
            "For 'declarative': a JSON object string describing method/url_template/"
            "headers/timeout_seconds/output_jsonpath. For 'python_sandbox': "
            "Python source defining def run(**params): ..."
        ),
    )
    capabilities: list[str] = Field(
        default_factory=list,
        description=(
            "Capability tokens the tool needs. Choices: http.lan, "
            "http.internet.https_get, http.internet.https_post, "
            "fs.workspace.read, fs.workspace.write, exec.sandbox, "
            "mcp.call, signal.send_to_owner."
        ),
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON-schema-ish dict mapping param name → {type: ...}.",
    )
    returns: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON-schema-ish dict describing the return shape.",
    )
    domain_allowlist: list[str] = Field(
        default_factory=list,
        description=(
            "For HTTP capabilities: hostnames the tool may reach. "
            "Empty means no allowlist (capability-token enforcement still applies)."
        ),
    )


def _agent_generation_allowed() -> tuple[bool, str]:
    enabled_env = os.environ.get("TOOL_FORGE_ENABLED", "").lower()
    if enabled_env not in ("1", "true", "yes", "on"):
        return False, "TOOL_FORGE_ENABLED must be true to register tools"
    agent_env = os.environ.get("TOOL_FORGE_AGENT_GENERATION_ENABLED", "").lower()
    if agent_env not in ("1", "true", "yes", "on"):
        return False, "TOOL_FORGE_AGENT_GENERATION_ENABLED must be true"
    return True, ""


def _try_build_tool() -> Any | None:
    """Construct a CrewAI BaseTool subclass. Returns None if CrewAI is missing
    or the gating layer disallows agent generation right now (the wrapper is
    rebuilt per-call to pick up env changes without restart).
    """
    try:
        from crewai.tools import BaseTool
    except Exception:
        return None

    class ForgeCreateTool(BaseTool):
        name: str = "forge_create_tool"
        description: str = (
            "Register a new sandboxed tool through the Forge audit pipeline. "
            "The tool lands in DRAFT, runs static + semantic audits, then sits "
            "in SHADOW (or QUARANTINED if any phase blocks it). Promotion past "
            "SHADOW requires manual human approval. Use this when there's a "
            "recurring need that an existing tool cannot satisfy — declarative "
            "JSON recipes are preferred for HTTP integrations; Python sandbox "
            "tools are pure-compute (no I/O) in this phase."
        )
        args_schema: Type[BaseModel] = _ForgeCreateToolInputs

        def _run(
            self,
            name: str,
            source_type: str,
            source_code: str,
            description: str = "",
            capabilities: list[str] | None = None,
            parameters: dict[str, Any] | None = None,
            returns: dict[str, Any] | None = None,
            domain_allowlist: list[str] | None = None,
        ) -> str:
            # Re-check the env flags on every call so an operator can flip
            # the kill switch without restarting the gateway.
            allowed, reason = _agent_generation_allowed()
            if not allowed:
                return json.dumps({"ok": False, "reason": reason})

            # Lazy import — avoid circular dependency with app.forge.api.
            from app.forge.audit.pipeline import run_registration_audits
            from app.forge.config import get_forge_config
            from app.forge.manifest import (
                Capability, GeneratorMetadata, ToolManifest, compute_tool_id,
            )
            from app.forge.registry import (
                get_tool, register_tool, total_tool_count,
            )

            cfg = get_forge_config()
            if total_tool_count() >= cfg.max_tools:
                return json.dumps({
                    "ok": False,
                    "reason": f"forge registry full ({cfg.max_tools} tools)",
                })

            if source_type not in ("declarative", "python_sandbox"):
                return json.dumps({
                    "ok": False,
                    "reason": f"source_type must be 'declarative' or 'python_sandbox', got {source_type!r}",
                })

            caps = capabilities or []
            try:
                declared = [Capability(c) for c in caps]
            except ValueError as exc:
                return json.dumps({"ok": False, "reason": f"unknown capability: {exc}"})

            tool_id = compute_tool_id(
                name=name, source_type=source_type,
                source_code=source_code, capabilities=caps,
            )
            generator = GeneratorMetadata(
                agent="coder",  # The agent calling this tool. Could be more specific.
                model="(unknown)",
                originating_request_text=f"agent-generated via forge_create_tool: {description or name}",
            )
            manifest = ToolManifest(
                tool_id=tool_id, name=name, description=description,
                source_type=source_type, capabilities=declared,
                parameters=parameters or {}, returns=returns or {},
                domain_allowlist=domain_allowlist or [],
                generator=generator,
            )

            try:
                register_tool(manifest, source_code=source_code, actor="agent.coder")
                run_registration_audits(tool_id)
            except Exception as exc:
                logger.exception("forge_create_tool: pipeline failed for %s", tool_id)
                return json.dumps({
                    "ok": False, "reason": f"pipeline error: {type(exc).__name__}: {exc}",
                })

            row = get_tool(tool_id) or {}
            return json.dumps({
                "ok": True,
                "tool_id": tool_id,
                "status": row.get("status"),
                "note": (
                    "Tool registered and audited. Status reflects audit outcome. "
                    "Promotion past SHADOW requires manual approval — the agent "
                    "cannot promote its own tools."
                ),
            })

    return ForgeCreateTool()


def get_forge_generator_tool() -> Any | None:
    """Public entry point — returns a tool instance or None if disallowed.

    Coder agent (and any future generator-capable agent) calls this at
    construction time. Returning None means the tool is silently absent
    from the agent's toolset.
    """
    allowed, reason = _agent_generation_allowed()
    if not allowed:
        logger.debug("forge_generator_tool: not exposed (%s)", reason)
        return None
    return _try_build_tool()
