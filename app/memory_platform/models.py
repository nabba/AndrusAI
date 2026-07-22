"""Core value types for the typed memory boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping, Sequence

EMBEDDING_DIMENSION = 768


class Durability(StrEnum):
    """Lifecycle and failure-domain classification for a memory space."""

    DURABLE = "durable"
    OPERATIONAL = "operational"
    GOVERNANCE = "governance"


class EpistemicClass(StrEnum):
    """The interpretation a caller must attach to retrieved content."""

    FACTUAL = "factual"
    THEORETICAL = "theoretical"
    SUBJECTIVE = "subjective"
    EPISODIC = "episodic"
    NARRATIVE = "narrative"
    AFFECTIVE = "affective"
    BELIEF = "belief"
    PROCEDURAL = "procedural"
    FICTIONAL = "fictional"
    EVALUATIVE = "evaluative"
    DIALECTICAL = "dialectical"
    TENANT_CONTEXT = "tenant_context"
    OPERATIONAL = "operational"


class ActorRole(StrEnum):
    """Caller's cognitive or infrastructure role.

    These are authorization roles, not CrewAI job titles alone.  Privileged
    infrastructure services use narrow roles such as ``RETROSPECTIVE`` instead
    of a universal ``system`` bypass.
    """

    COMMANDER = "commander"
    RESEARCHER = "researcher"
    CODER = "coder"
    WRITER = "writer"
    CRITIC = "critic"
    SELF_IMPROVER = "self_improver"
    MEMORY_KERNEL = "memory_kernel"
    SELF_REFLECTION = "self_reflection"
    RETROSPECTIVE = "retrospective"
    KNOWLEDGE_INGESTER = "knowledge_ingester"
    RECONCILER = "reconciler"
    AUDITOR = "auditor"


class AccessAction(StrEnum):
    READ = "read"
    WRITE = "write"
    PROMOTE = "promote"
    ADMIN = "admin"


@dataclass(frozen=True, slots=True)
class Principal:
    """Authorization and ownership context attached to a memory operation."""

    role: ActorRole
    actor_id: str
    tenant_id: str | None = None
    workspace_id: str | None = None


@dataclass(frozen=True, slots=True)
class LegacyLocation:
    """Physical legacy route used during backfill and shadow operation."""

    backend: str
    kb_name: str | None = None
    collection: str | None = None
    table: str | None = None
    source_kind: str = "projection"


@dataclass(frozen=True, slots=True)
class MemorySpace:
    """Machine-readable contract for one independently governed index."""

    key: str
    schema: str
    table: str
    durability: Durability
    epistemic_class: EpistemicClass
    readers: frozenset[ActorRole]
    writers: frozenset[ActorRole]
    legacy: tuple[LegacyLocation, ...] = ()
    tenant_scoped: bool = False
    spontaneous_eligible: bool = False
    retention_days: int | None = None
    description: str = ""

    @property
    def qualified_table(self) -> str:
        """Return the trusted SQL identifier registered for this space."""

        return f"{self.schema}.{self.table}"

    def permits(self, principal: Principal, action: AccessAction) -> bool:
        """Return whether ``principal`` may perform ``action``."""

        if action is AccessAction.READ:
            allowed = principal.role in self.readers
        elif action is AccessAction.WRITE:
            allowed = principal.role in self.writers
        elif action is AccessAction.PROMOTE:
            allowed = principal.role in {
                ActorRole.RETROSPECTIVE,
                ActorRole.RECONCILER,
            }
        else:
            allowed = principal.role is ActorRole.AUDITOR
        if self.tenant_scoped and not principal.tenant_id:
            return False
        return allowed


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """Canonical record exchanged between the broker and storage adapters."""

    memory_id: str
    space: str
    content: str
    source_uri: str
    source_record_id: str
    content_sha256: str
    epistemic_class: EpistemicClass
    provenance: Mapping[str, Any]
    embedding: Sequence[float] | None = None
    owner_agent_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None
    event_time: datetime | None = None
    confidence: float | None = None
    salience: float | None = None
    significance: float | None = None
    valence: float | None = None
    status: str = "active"
    schema_version: int = 1
    attributes: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


@dataclass(frozen=True, slots=True)
class RecallResult:
    """A retrieved record with explicit origin and ranking information."""

    record: MemoryRecord
    score: float
    backend: str
    bridge: str | None = None
