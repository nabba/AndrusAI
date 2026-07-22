from app.memory_platform.models import ActorRole, Durability, EpistemicClass
from app.memory_platform.registry import (
    MEMORY_BRIDGES,
    MEMORY_SPACES,
    validate_registry,
)


def test_registry_is_structurally_valid() -> None:
    assert validate_registry() == []


def test_every_durable_space_has_its_own_physical_table() -> None:
    durable = [space for space in MEMORY_SPACES.values() if space.durability is Durability.DURABLE]
    assert len({space.qualified_table for space in durable}) == len(durable)


def test_only_curated_episodes_can_surface_spontaneously() -> None:
    spontaneous = [space.key for space in MEMORY_SPACES.values() if space.spontaneous_eligible]
    assert spontaneous == ["autobiographical.episodic_curated"]


def test_full_and_curated_episodes_have_different_acl_and_table() -> None:
    full = MEMORY_SPACES["autobiographical.episodic_full"]
    curated = MEMORY_SPACES["autobiographical.episodic_curated"]
    assert full.qualified_table != curated.qualified_table
    assert ActorRole.SELF_REFLECTION not in full.readers
    assert ActorRole.SELF_REFLECTION in curated.readers
    assert full.readers == frozenset({ActorRole.RETROSPECTIVE, ActorRole.AUDITOR})


def test_fiction_boundary_preserves_existing_exclusions() -> None:
    fiction = MEMORY_SPACES["creative.fiction"]
    assert fiction.epistemic_class is EpistemicClass.FICTIONAL
    assert ActorRole.WRITER in fiction.readers
    assert ActorRole.CODER in fiction.readers
    assert ActorRole.RESEARCHER not in fiction.readers
    assert ActorRole.CRITIC not in fiction.readers
    assert ActorRole.SELF_IMPROVER not in fiction.readers


def test_self_reflection_bridge_does_not_include_full_or_fiction() -> None:
    spaces = set(MEMORY_BRIDGES["self_reflection"].spaces)
    assert "autobiographical.episodic_curated" in spaces
    assert "autobiographical.episodic_full" not in spaces
    assert "creative.fiction" not in spaces


def test_creative_bridge_is_explicit_and_labeled() -> None:
    bridge = MEMORY_BRIDGES["creative_blend"]
    assert "creative.fiction" in bridge.spaces
    assert "knowledge.episteme" in bridge.spaces
    assert ActorRole.RESEARCHER not in bridge.readers


def test_operational_spaces_are_not_durable_tables() -> None:
    operational = [space for space in MEMORY_SPACES.values() if space.durability is Durability.OPERATIONAL]
    assert operational
    assert all(space.retention_days is not None for space in operational)
