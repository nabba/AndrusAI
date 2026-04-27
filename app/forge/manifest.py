"""Pydantic models for Forge — manifest, status, capabilities, audit results.

Tools are content-addressed: tool_id = sha256(source_type + source_code +
sorted_capabilities + name)[:16] + "-" + uuid4()[:8]. This guarantees that
"the same logical tool" always has the same primary content hash, while the
suffix prevents collisions across regenerations.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ToolStatus(str, Enum):
    """Lifecycle states. Forward transitions gated by audits; KILLED is sticky."""
    DRAFT = "DRAFT"
    QUARANTINED = "QUARANTINED"
    SHADOW = "SHADOW"
    CANARY = "CANARY"
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    KILLED = "KILLED"


class Capability(str, Enum):
    """Capability tokens — declared in manifest, enforced at every I/O syscall.

    Declaration is necessary but not sufficient. The runtime guard checks each
    actual operation against the declared bundle; mismatches kill the tool.
    """
    HTTP_LAN = "http.lan"
    HTTP_INTERNET_GET = "http.internet.https_get"
    HTTP_INTERNET_POST = "http.internet.https_post"
    FS_WORKSPACE_READ = "fs.workspace.read"
    FS_WORKSPACE_WRITE = "fs.workspace.write"
    EXEC_SANDBOX = "exec.sandbox"
    MCP_CALL = "mcp.call"
    SIGNAL_SEND_TO_OWNER = "signal.send_to_owner"


# Hard-blocked: no manifest may declare these regardless of audit verdict.
FORBIDDEN_CAPABILITIES: frozenset[str] = frozenset({
    "fs.app_code.read", "fs.app_code.write",
    "exec.host", "network.raw_socket",
    "import.dynamic", "secrets.read.unrestricted",
    "signal.send_arbitrary",
})


class GeneratorMetadata(BaseModel):
    """Provenance — answers 'who/what/when/why was this tool created'."""
    crew_run_id: str = ""
    agent: str = "unknown"
    model: str = "unknown"
    temperature: float | None = None
    seed: int | None = None
    originating_request_hash: str = ""
    originating_request_text: str = ""
    parent_skill_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ToolManifest(BaseModel):
    """Declarative tool definition.

    For source_type=="declarative", source_code is a JSON-encoded recipe
    (HTTP method/URL/headers/jsonpath) interpreted by trusted runtime code.
    For source_type=="python_sandbox", source_code is Python that runs only
    inside the existing sandbox subprocess via code_executor.
    """
    model_config = ConfigDict(use_enum_values=True)

    tool_id: str
    name: str
    version: int = 1
    description: str = ""
    source_type: Literal["declarative", "python_sandbox"]
    capabilities: list[Capability] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    returns: dict[str, Any] = Field(default_factory=dict)
    domain_allowlist: list[str] = Field(default_factory=list)
    generator: GeneratorMetadata = Field(default_factory=GeneratorMetadata)


class AuditPhase(str, Enum):
    STATIC = "static"
    SEMANTIC = "semantic"
    DYNAMIC = "dynamic"
    COMPOSITION = "composition"
    PERIODIC = "periodic"


class AuditFinding(BaseModel):
    """One result from an audit phase."""
    phase: AuditPhase
    passed: bool
    score: float = 0.0
    summary: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SecurityEval(BaseModel):
    """LLM judge output — the 'deep description' shown in the UI."""
    what_it_does: str
    declared_capabilities: list[str]
    actual_capability_footprint: list[str]
    what_could_go_wrong: list[str]
    attack_classes_considered: list[str]
    risk_score: float = Field(ge=0.0, le=10.0)
    risk_justification: str
    judge_model: str = ""
    judged_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def compute_tool_id(name: str, source_type: str, source_code: str,
                    capabilities: list[str]) -> str:
    """Content-addressed tool ID with collision-resistant uuid suffix."""
    payload = "\n".join([
        name,
        source_type,
        source_code,
        ",".join(sorted(capabilities)),
    ]).encode("utf-8")
    content_hash = hashlib.sha256(payload).hexdigest()[:16]
    suffix = uuid.uuid4().hex[:8]
    return f"{content_hash}-{suffix}"
