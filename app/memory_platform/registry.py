"""Canonical memory-space and cross-space bridge registry.

The registry is intentionally explicit.  Adding a space or bridge is a code
review event; unrecognised collection names do not silently become queryable.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.memory_platform.models import (
    ActorRole,
    Durability,
    EpistemicClass,
    LegacyLocation,
    MemorySpace,
)

_ALL_COGNITIVE = frozenset(
    {
        ActorRole.COMMANDER,
        ActorRole.RESEARCHER,
        ActorRole.CODER,
        ActorRole.WRITER,
        ActorRole.CRITIC,
        ActorRole.SELF_IMPROVER,
        ActorRole.MEMORY_KERNEL,
        ActorRole.SELF_REFLECTION,
        ActorRole.RETROSPECTIVE,
        ActorRole.AUDITOR,
    }
)
_KNOWLEDGE_WRITERS = frozenset(
    {ActorRole.KNOWLEDGE_INGESTER, ActorRole.RECONCILER}
)
_SELF_READERS = frozenset(
    {
        ActorRole.COMMANDER,
        ActorRole.MEMORY_KERNEL,
        ActorRole.SELF_REFLECTION,
        ActorRole.RETROSPECTIVE,
        ActorRole.AUDITOR,
    }
)
_SELF_WRITERS = frozenset({ActorRole.MEMORY_KERNEL, ActorRole.RECONCILER})
_CREATIVE_READERS = frozenset(
    {
        ActorRole.COMMANDER,
        ActorRole.CODER,
        ActorRole.WRITER,
        ActorRole.MEMORY_KERNEL,
        ActorRole.SELF_REFLECTION,
        ActorRole.RETROSPECTIVE,
        ActorRole.AUDITOR,
    }
)
_TENANT_READERS = frozenset(
    {
        ActorRole.COMMANDER,
        ActorRole.RESEARCHER,
        ActorRole.CODER,
        ActorRole.WRITER,
        ActorRole.CRITIC,
        ActorRole.SELF_IMPROVER,
        ActorRole.AUDITOR,
    }
)


def _legacy_chroma(kb: str, collection: str, source_kind: str = "projection") -> tuple[LegacyLocation, ...]:
    return (
        LegacyLocation(
            backend="chroma",
            kb_name=kb,
            collection=collection,
            source_kind=source_kind,
        ),
    )


MEMORY_SPACES: dict[str, MemorySpace] = {
    # Knowledge: each table owns its own HNSW + FTS index.
    "knowledge.episteme": MemorySpace(
        key="knowledge.episteme",
        schema="knowledge",
        table="episteme_chunks",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.FACTUAL,
        readers=_ALL_COGNITIVE,
        writers=_KNOWLEDGE_WRITERS,
        legacy=_legacy_chroma("episteme", "episteme_research"),
        description="Research evidence, methods, and theoretical knowledge.",
    ),
    "knowledge.philosophy": MemorySpace(
        key="knowledge.philosophy",
        schema="knowledge",
        table="philosophy_claims",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.THEORETICAL,
        readers=_ALL_COGNITIVE,
        writers=_KNOWLEDGE_WRITERS,
        legacy=_legacy_chroma("philosophy", "philosophy_humanist"),
        description="Philosophical claims with explicit theoretical status.",
    ),
    "knowledge.philosophy_counterclaims": MemorySpace(
        key="knowledge.philosophy_counterclaims",
        schema="knowledge",
        table="philosophy_counterclaims",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.DIALECTICAL,
        readers=_ALL_COGNITIVE,
        writers=_KNOWLEDGE_WRITERS,
        description="Counterclaims kept independently retrievable for dialectic.",
    ),
    "knowledge.enterprise": MemorySpace(
        key="knowledge.enterprise",
        schema="knowledge",
        table="enterprise_chunks",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.FACTUAL,
        readers=_ALL_COGNITIVE,
        writers=_KNOWLEDGE_WRITERS,
        legacy=(
            LegacyLocation(backend="chroma", kb_name="knowledge", collection="enterprise_knowledge"),
            LegacyLocation(backend="chroma", kb_name="memory", collection="andrusai_wiki_pages"),
        ),
        description="Shared enterprise documents and operational doctrine.",
    ),
    # Autobiographical: full and curated are intentionally different ACLs.
    "autobiographical.experiential": MemorySpace(
        key="autobiographical.experiential",
        schema="autobiographical",
        table="experiential_entries",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.SUBJECTIVE,
        readers=_SELF_READERS,
        writers=_SELF_WRITERS,
        legacy=(
            LegacyLocation(backend="chroma", kb_name="experiential", collection="experiential_journal"),
            *tuple(
                LegacyLocation(backend="chroma", kb_name="memory", collection=name)
                for name in (
                    "reflections_coding",
                    "reflections_creative",
                    "reflections_critic",
                    "reflections_introspector",
                    "reflections_pim",
                    "reflections_repo_analysis",
                    "reflections_research",
                    "reflections_writing",
                )
            ),
        ),
        description="Subjective reflections, explicitly not objective fact.",
    ),
    "autobiographical.episodic_full": MemorySpace(
        key="autobiographical.episodic_full",
        schema="autobiographical",
        table="episodic_full",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.EPISODIC,
        readers=frozenset({ActorRole.RETROSPECTIVE, ActorRole.AUDITOR}),
        writers=_SELF_WRITERS,
        description="High-recall episode stream; never used by ordinary recall.",
    ),
    "autobiographical.episodic_curated": MemorySpace(
        key="autobiographical.episodic_curated",
        schema="autobiographical",
        table="episodic_curated",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.EPISODIC,
        readers=_SELF_READERS,
        writers=frozenset(
            {ActorRole.MEMORY_KERNEL, ActorRole.RETROSPECTIVE, ActorRole.RECONCILER}
        ),
        spontaneous_eligible=True,
        description="Significant episodes available to conscious/spontaneous recall.",
    ),
    "autobiographical.narrative": MemorySpace(
        key="autobiographical.narrative",
        schema="autobiographical",
        table="narrative_chapters",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.NARRATIVE,
        readers=_SELF_READERS,
        writers=_SELF_WRITERS,
        description="Continuity-bearing chapters, arcs, and epochs.",
    ),
    "autobiographical.affect": MemorySpace(
        key="autobiographical.affect",
        schema="autobiographical",
        table="affect_events",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.AFFECTIVE,
        readers=frozenset(
            {ActorRole.MEMORY_KERNEL, ActorRole.RETROSPECTIVE, ActorRole.AUDITOR}
        ),
        writers=_SELF_WRITERS,
        description="Affect trace; exposed to normal cognition only through summaries.",
    ),
    "autobiographical.self_reports": MemorySpace(
        key="autobiographical.self_reports",
        schema="autobiographical",
        table="self_reports",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.SUBJECTIVE,
        readers=_SELF_READERS,
        writers=_SELF_WRITERS,
        legacy=(
            LegacyLocation(backend="chroma", kb_name="memory", collection="self_reports"),
            LegacyLocation(backend="chroma", kb_name="memory", collection="introspector"),
        ),
        description="Explicit self-reports kept distinct from factual self-state.",
    ),
    # Identity is distinct from episodes: beliefs may be revised without
    # rewriting autobiographical history.
    "identity.beliefs": MemorySpace(
        key="identity.beliefs",
        schema="identity_memory",
        table="beliefs",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.BELIEF,
        readers=_ALL_COGNITIVE,
        writers=frozenset({ActorRole.MEMORY_KERNEL, ActorRole.RECONCILER}),
        legacy=(
            LegacyLocation(backend="postgres", table="public.beliefs", source_kind="canonical"),
            LegacyLocation(backend="chroma", kb_name="memory", collection="scope_beliefs"),
            LegacyLocation(backend="chroma", kb_name="memory", collection="beliefs"),
        ),
        description="Revisable beliefs with evidence and contradiction links.",
    ),
    "identity.predictions": MemorySpace(
        key="identity.predictions",
        schema="identity_memory",
        table="predictions",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.BELIEF,
        readers=_SELF_READERS,
        writers=_SELF_WRITERS,
        legacy=_legacy_chroma("memory", "scope_predictions"),
        description="Predictions retained for calibration and self-model continuity.",
    ),
    "identity.prediction_errors": MemorySpace(
        key="identity.prediction_errors",
        schema="identity_memory",
        table="prediction_errors",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.BELIEF,
        readers=_SELF_READERS,
        writers=_SELF_WRITERS,
        description="Observed prediction errors and calibration evidence.",
    ),
    "identity.self_knowledge": MemorySpace(
        key="identity.self_knowledge",
        schema="identity_memory",
        table="self_knowledge",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.BELIEF,
        readers=_SELF_READERS,
        writers=frozenset({ActorRole.MEMORY_KERNEL, ActorRole.RECONCILER}),
        legacy=_legacy_chroma("memory", "self_knowledge"),
        description="Versioned knowledge about the system's own structure and capabilities.",
    ),
    "identity.world_model": MemorySpace(
        key="identity.world_model",
        schema="identity_memory",
        table="world_model",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.BELIEF,
        readers=_ALL_COGNITIVE,
        writers=frozenset({ActorRole.MEMORY_KERNEL, ActorRole.RECONCILER}),
        legacy=_legacy_chroma("memory", "scope_world_model"),
        description="Revisable model of external state; not raw factual evidence.",
    ),
    "identity.ecology": MemorySpace(
        key="identity.ecology",
        schema="identity_memory",
        table="ecology",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.BELIEF,
        readers=_SELF_READERS,
        writers=frozenset({ActorRole.MEMORY_KERNEL, ActorRole.RECONCILER}),
        legacy=_legacy_chroma("memory", "scope_ecology"),
        description="Models of relationships, agents, and social/ecological context.",
    ),
    # Procedural memory.
    "procedural.skills": MemorySpace(
        key="procedural.skills",
        schema="procedural",
        table="skills",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.PROCEDURAL,
        readers=_ALL_COGNITIVE,
        writers=frozenset(
            {ActorRole.MEMORY_KERNEL, ActorRole.SELF_IMPROVER, ActorRole.RECONCILER}
        ),
        legacy=(
            LegacyLocation(backend="chroma", kb_name="memory", collection="skills"),
            LegacyLocation(backend="chroma", kb_name="memory", collection="skill_records"),
        ),
        description="Stable learned procedures and competence descriptions.",
    ),
    "procedural.trajectory_lessons": MemorySpace(
        key="procedural.trajectory_lessons",
        schema="procedural",
        table="trajectory_lessons",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.PROCEDURAL,
        readers=_ALL_COGNITIVE,
        writers=frozenset({ActorRole.MEMORY_KERNEL, ActorRole.RECONCILER}),
        description="Lessons extracted from action trajectories.",
    ),
    "procedural.transfer_insights": MemorySpace(
        key="procedural.transfer_insights",
        schema="procedural",
        table="transfer_insights",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.PROCEDURAL,
        readers=_ALL_COGNITIVE,
        writers=frozenset({ActorRole.MEMORY_KERNEL, ActorRole.RECONCILER}),
        description="Cross-task and cross-workspace reusable procedures.",
    ),
    "procedural.learned_policies": MemorySpace(
        key="procedural.learned_policies",
        schema="procedural",
        table="learned_policies",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.PROCEDURAL,
        readers=_ALL_COGNITIVE,
        writers=frozenset({ActorRole.SELF_IMPROVER, ActorRole.RECONCILER}),
        legacy=_legacy_chroma("memory", "scope_policies"),
        description="Learned tactics; never governance or evaluation criteria.",
    ),
    "procedural.evolution_lessons": MemorySpace(
        key="procedural.evolution_lessons",
        schema="procedural",
        table="evolution_lessons",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.PROCEDURAL,
        readers=_ALL_COGNITIVE,
        writers=frozenset({ActorRole.SELF_IMPROVER, ActorRole.RECONCILER}),
        legacy=tuple(
            LegacyLocation(backend="chroma", kb_name="memory", collection=name)
            for name in ("evo_failures", "evo_successes", "evolution_patterns")
        ),
        description="Outcomes and patterns from governed improvement experiments.",
    ),
    "procedural.learning_gaps": MemorySpace(
        key="procedural.learning_gaps",
        schema="procedural",
        table="learning_gaps",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.PROCEDURAL,
        readers=_ALL_COGNITIVE,
        writers=frozenset({ActorRole.MEMORY_KERNEL, ActorRole.SELF_IMPROVER, ActorRole.RECONCILER}),
        legacy=_legacy_chroma("memory", "learning_gaps"),
        description="Known capability gaps and learning needs.",
    ),
    "procedural.tools": MemorySpace(
        key="procedural.tools",
        schema="procedural",
        table="tool_knowledge",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.PROCEDURAL,
        readers=_ALL_COGNITIVE,
        writers=frozenset({ActorRole.KNOWLEDGE_INGESTER, ActorRole.RECONCILER}),
        legacy=_legacy_chroma("memory", "tool_registry"),
        description="Retrieval projection of the governed tool registry.",
    ),
    # Creative memory.  Fiction keeps the existing selective-access rule.
    "creative.fiction": MemorySpace(
        key="creative.fiction",
        schema="creative",
        table="fiction_chunks",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.FICTIONAL,
        readers=frozenset(
            {
                ActorRole.COMMANDER,
                ActorRole.CODER,
                ActorRole.WRITER,
                ActorRole.MEMORY_KERNEL,
                ActorRole.AUDITOR,
            }
        ),
        writers=_KNOWLEDGE_WRITERS,
        legacy=(
            LegacyLocation(backend="chroma", kb_name="fiction_library", collection="fiction_inspiration"),
            LegacyLocation(backend="chroma", kb_name="literature_library", collection="literature_inspiration"),
        ),
        description="Imaginary material; every result must retain FICTIONAL status.",
    ),
    "creative.aesthetics": MemorySpace(
        key="creative.aesthetics",
        schema="creative",
        table="aesthetic_patterns",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.EVALUATIVE,
        readers=_CREATIVE_READERS,
        writers=frozenset({ActorRole.MEMORY_KERNEL, ActorRole.RECONCILER}),
        legacy=_legacy_chroma("aesthetics", "aesthetic_patterns"),
        description="Taste and pattern judgements kept distinct from facts.",
    ),
    "creative.tensions": MemorySpace(
        key="creative.tensions",
        schema="creative",
        table="unresolved_tensions",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.DIALECTICAL,
        readers=_CREATIVE_READERS,
        writers=frozenset({ActorRole.MEMORY_KERNEL, ActorRole.RECONCILER}),
        legacy=_legacy_chroma("tensions", "unresolved_tensions"),
        description="Unresolved contradictions retained as generative pressure.",
    ),
    "creative.ideas": MemorySpace(
        key="creative.ideas",
        schema="creative",
        table="ideas",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.EVALUATIVE,
        readers=_CREATIVE_READERS,
        writers=frozenset(
            {ActorRole.COMMANDER, ActorRole.CODER, ActorRole.WRITER, ActorRole.MEMORY_KERNEL, ActorRole.RECONCILER}
        ),
        legacy=_legacy_chroma("memory", "companion_ideas"),
        description="Ideas and lineage; source documents remain authoritative.",
    ),
    # Tenant memory requires both role permission and a tenant context.
    "tenant.documents": MemorySpace(
        key="tenant.documents",
        schema="tenant_memory",
        table="project_documents",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.TENANT_CONTEXT,
        readers=_TENANT_READERS,
        writers=frozenset({ActorRole.KNOWLEDGE_INGESTER, ActorRole.RECONCILER}),
        tenant_scoped=True,
        description="Tenant/project documents protected by PostgreSQL RLS.",
    ),
    "tenant.experiences": MemorySpace(
        key="tenant.experiences",
        schema="tenant_memory",
        table="project_experiences",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.TENANT_CONTEXT,
        readers=_TENANT_READERS,
        writers=frozenset({ActorRole.MEMORY_KERNEL, ActorRole.RECONCILER}),
        tenant_scoped=True,
        description="Project-local experience protected by PostgreSQL RLS.",
    ),
    "tenant.lessons": MemorySpace(
        key="tenant.lessons",
        schema="tenant_memory",
        table="project_lessons",
        durability=Durability.DURABLE,
        epistemic_class=EpistemicClass.TENANT_CONTEXT,
        readers=_TENANT_READERS,
        writers=frozenset({ActorRole.MEMORY_KERNEL, ActorRole.RECONCILER}),
        tenant_scoped=True,
        description="Project-local procedures protected by PostgreSQL RLS.",
    ),
    # Operational spaces deliberately have no durable target table.  Schema
    # and table name a logical route so tooling can inventory them uniformly.
    "operational.team": MemorySpace(
        key="operational.team",
        schema="operational",
        table="scope_team",
        durability=Durability.OPERATIONAL,
        epistemic_class=EpistemicClass.OPERATIONAL,
        readers=_ALL_COGNITIVE,
        writers=frozenset(_ALL_COGNITIVE | {ActorRole.RECONCILER}),
        legacy=(
            LegacyLocation(backend="chroma", kb_name="memory", collection="scope_team", source_kind="canonical"),
            LegacyLocation(backend="chroma", kb_name="memory", collection="team_shared", source_kind="canonical"),
        ),
        retention_days=30,
        description="Shared near-term decisions and task state.",
    ),
    "operational.blackboard": MemorySpace(
        key="operational.blackboard",
        schema="operational",
        table="research_blackboards",
        durability=Durability.OPERATIONAL,
        epistemic_class=EpistemicClass.OPERATIONAL,
        readers=_ALL_COGNITIVE,
        writers=frozenset(_ALL_COGNITIVE | {ActorRole.RECONCILER}),
        retention_days=14,
        description="Task-scoped research blackboards; verified items are promoted.",
    ),
    "operational.agent_private": MemorySpace(
        key="operational.agent_private",
        schema="operational",
        table="agent_private",
        durability=Durability.OPERATIONAL,
        epistemic_class=EpistemicClass.OPERATIONAL,
        readers=_ALL_COGNITIVE,
        writers=frozenset(_ALL_COGNITIVE | {ActorRole.RECONCILER}),
        retention_days=30,
        description="Agent-owned working memory; ownership is checked by the broker.",
    ),
    "operational.cache": MemorySpace(
        key="operational.cache",
        schema="operational",
        table="result_cache",
        durability=Durability.OPERATIONAL,
        epistemic_class=EpistemicClass.OPERATIONAL,
        readers=_ALL_COGNITIVE,
        writers=frozenset(_ALL_COGNITIVE | {ActorRole.RECONCILER}),
        legacy=_legacy_chroma("memory", "result_cache", source_kind="canonical"),
        retention_days=7,
        description="Disposable retrieval/tool result cache.",
    ),
    "operational.tech_radar": MemorySpace(
        key="operational.tech_radar",
        schema="operational",
        table="tech_radar",
        durability=Durability.OPERATIONAL,
        epistemic_class=EpistemicClass.OPERATIONAL,
        readers=_ALL_COGNITIVE,
        writers=frozenset(_ALL_COGNITIVE | {ActorRole.RECONCILER}),
        legacy=_legacy_chroma("memory", "scope_tech_radar", source_kind="canonical"),
        retention_days=90,
        description="Time-sensitive technology observations with bounded retention.",
    ),
}


@dataclass(frozen=True, slots=True)
class MemoryBridge:
    """An explicitly authorised cross-space retrieval path."""

    key: str
    spaces: tuple[str, ...]
    readers: frozenset[ActorRole]
    description: str


MEMORY_BRIDGES: dict[str, MemoryBridge] = {
    "factual_context": MemoryBridge(
        key="factual_context",
        spaces=("knowledge.episteme", "knowledge.philosophy", "knowledge.enterprise"),
        readers=_ALL_COGNITIVE,
        description="Evidence-oriented context without fiction or private episodes.",
    ),
    "self_reflection": MemoryBridge(
        key="self_reflection",
        spaces=(
            "autobiographical.episodic_curated",
            "autobiographical.narrative",
            "identity.beliefs",
        ),
        readers=_SELF_READERS,
        description="Curated continuity context for ordinary self-reflection.",
    ),
    "creative_blend": MemoryBridge(
        key="creative_blend",
        spaces=(
            "knowledge.episteme",
            "knowledge.philosophy",
            "creative.fiction",
            "creative.aesthetics",
            "creative.tensions",
        ),
        readers=frozenset(
            {ActorRole.COMMANDER, ActorRole.CODER, ActorRole.WRITER, ActorRole.MEMORY_KERNEL, ActorRole.AUDITOR}
        ),
        description="Controlled associative bridge; source labels remain mandatory.",
    ),
    "retrospective_review": MemoryBridge(
        key="retrospective_review",
        spaces=(
            "autobiographical.episodic_full",
            "autobiographical.episodic_curated",
            "autobiographical.narrative",
            "autobiographical.affect",
        ),
        readers=frozenset({ActorRole.RETROSPECTIVE, ActorRole.AUDITOR}),
        description="Privileged review path that may promote full episodes to curated.",
    ),
    "procedural_transfer": MemoryBridge(
        key="procedural_transfer",
        spaces=(
            "procedural.skills",
            "procedural.trajectory_lessons",
            "procedural.transfer_insights",
            "procedural.learned_policies",
        ),
        readers=_ALL_COGNITIVE,
        description="Reusable methods and lessons, separate from governance policy.",
    ),
}


def get_memory_space(key: str) -> MemorySpace:
    """Return a registered space or reject the untyped route."""

    try:
        return MEMORY_SPACES[key]
    except KeyError as exc:
        raise KeyError(f"unregistered memory space: {key}") from exc


def get_memory_bridge(key: str) -> MemoryBridge:
    """Return a registered bridge or reject implicit cross-space retrieval."""

    try:
        return MEMORY_BRIDGES[key]
    except KeyError as exc:
        raise KeyError(f"unregistered memory bridge: {key}") from exc


def validate_registry() -> list[str]:
    """Return structural errors; an empty list means the registry is sound."""

    errors: list[str] = []
    durable_tables: dict[str, str] = {}
    for key, space in MEMORY_SPACES.items():
        if key != space.key:
            errors.append(f"registry key mismatch: {key} != {space.key}")
        if space.durability is Durability.DURABLE:
            previous = durable_tables.get(space.qualified_table)
            if previous:
                errors.append(
                    f"durable spaces share physical table: {previous}, {key} -> {space.qualified_table}"
                )
            durable_tables[space.qualified_table] = key
        if space.spontaneous_eligible and key != "autobiographical.episodic_curated":
            errors.append(f"only curated episodes may surface spontaneously: {key}")
        if space.epistemic_class is EpistemicClass.FICTIONAL:
            forbidden = {ActorRole.RESEARCHER, ActorRole.SELF_IMPROVER, ActorRole.CRITIC}
            leaked = forbidden & space.readers
            if leaked:
                errors.append(f"fiction readers contain forbidden roles: {sorted(leaked)}")

    for bridge_key, bridge in MEMORY_BRIDGES.items():
        if bridge_key != bridge.key:
            errors.append(f"bridge key mismatch: {bridge_key} != {bridge.key}")
        for space_key in bridge.spaces:
            if space_key not in MEMORY_SPACES:
                errors.append(f"bridge {bridge_key} references unknown space {space_key}")
    return errors


_REGISTRY_ERRORS = validate_registry()
if _REGISTRY_ERRORS:
    raise RuntimeError("invalid memory-space registry: " + "; ".join(_REGISTRY_ERRORS))
