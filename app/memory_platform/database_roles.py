"""Reviewed mapping between cognitive roles and narrow PostgreSQL read roles."""

from __future__ import annotations

from app.memory_platform.models import ActorRole, Durability
from app.memory_platform.registry import MEMORY_SPACES

DB_ROLE_SPACES: dict[str, frozenset[str]] = {
    "memory_factual_reader": frozenset(
        {
            "knowledge.episteme",
            "knowledge.philosophy",
            "knowledge.philosophy_counterclaims",
            "knowledge.enterprise",
        }
    ),
    "memory_curated_reader": frozenset(
        {
            "autobiographical.experiential",
            "autobiographical.episodic_curated",
            "autobiographical.narrative",
            "autobiographical.self_reports",
        }
    ),
    "memory_deep_recall_reader": frozenset(
        {
            "autobiographical.episodic_full",
            "autobiographical.episodic_curated",
            "autobiographical.narrative",
            "autobiographical.affect",
        }
    ),
    "memory_affect_reader": frozenset({"autobiographical.affect"}),
    "memory_belief_reader": frozenset({"identity.beliefs", "identity.world_model"}),
    "memory_private_identity_reader": frozenset(
        {
            "identity.predictions",
            "identity.prediction_errors",
            "identity.self_knowledge",
            "identity.ecology",
        }
    ),
    "memory_procedural_reader": frozenset(
        key for key in MEMORY_SPACES if key.startswith("procedural.")
    ),
    "memory_fiction_reader": frozenset({"creative.fiction"}),
    "memory_creative_evaluative_reader": frozenset(
        {"creative.aesthetics", "creative.tensions", "creative.ideas"}
    ),
    "memory_tenant_reader": frozenset(
        key for key in MEMORY_SPACES if key.startswith("tenant.")
    ),
}


_FACTUAL = {"memory_factual_reader", "memory_belief_reader", "memory_procedural_reader"}
_CREATIVE = {"memory_fiction_reader", "memory_creative_evaluative_reader"}
_CURATED_SELF = {
    "memory_curated_reader",
    "memory_belief_reader",
    "memory_private_identity_reader",
}


ACTOR_DATABASE_ROLES: dict[ActorRole, frozenset[str]] = {
    ActorRole.COMMANDER: frozenset(_FACTUAL | _CREATIVE | _CURATED_SELF | {"memory_tenant_reader"}),
    ActorRole.RESEARCHER: frozenset(_FACTUAL | {"memory_tenant_reader"}),
    ActorRole.CODER: frozenset(_FACTUAL | _CREATIVE | {"memory_tenant_reader"}),
    ActorRole.WRITER: frozenset(_FACTUAL | _CREATIVE | {"memory_tenant_reader"}),
    ActorRole.CRITIC: frozenset(_FACTUAL | {"memory_tenant_reader"}),
    ActorRole.SELF_IMPROVER: frozenset(_FACTUAL | {"memory_tenant_reader"}),
    ActorRole.MEMORY_KERNEL: frozenset(
        _FACTUAL | _CREATIVE | _CURATED_SELF | {"memory_affect_reader"}
    ),
    ActorRole.SELF_REFLECTION: frozenset(
        _FACTUAL | _CURATED_SELF | {"memory_creative_evaluative_reader"}
    ),
    ActorRole.RETROSPECTIVE: frozenset(
        _FACTUAL
        | _CURATED_SELF
        | {"memory_deep_recall_reader", "memory_creative_evaluative_reader"}
    ),
    ActorRole.AUDITOR: frozenset(DB_ROLE_SPACES),
}


def database_roles_for(actor_role: ActorRole) -> frozenset[str]:
    """Return the reviewed PostgreSQL group memberships for a reader."""

    return ACTOR_DATABASE_ROLES.get(ActorRole(actor_role), frozenset())


def validate_database_role_mapping() -> list[str]:
    """Ensure database grants neither omit nor exceed broker read permissions."""

    errors: list[str] = []
    durable_spaces = {
        key: space
        for key, space in MEMORY_SPACES.items()
        if space.durability is Durability.DURABLE
    }
    for actor, memberships in ACTOR_DATABASE_ROLES.items():
        granted_spaces = set().union(*(DB_ROLE_SPACES[role] for role in memberships))
        broker_spaces = {key for key, space in durable_spaces.items() if actor in space.readers}
        overreach = granted_spaces - broker_spaces
        missing = broker_spaces - granted_spaces
        if overreach:
            errors.append(f"{actor} database grants exceed broker ACL: {sorted(overreach)}")
        if missing:
            errors.append(f"{actor} database grants miss broker ACL: {sorted(missing)}")
    return errors


_MAPPING_ERRORS = validate_database_role_mapping()
if _MAPPING_ERRORS:
    raise RuntimeError("invalid memory database-role mapping: " + "; ".join(_MAPPING_ERRORS))
