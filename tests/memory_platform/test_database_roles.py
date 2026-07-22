from app.memory_platform.database_roles import (
    ACTOR_DATABASE_ROLES,
    database_roles_for,
    validate_database_role_mapping,
)
from app.memory_platform.models import ActorRole


def test_database_roles_exactly_match_broker_acl() -> None:
    assert validate_database_role_mapping() == []


def test_researcher_and_self_improver_never_receive_fiction_role() -> None:
    assert "memory_fiction_reader" not in database_roles_for(ActorRole.RESEARCHER)
    assert "memory_fiction_reader" not in database_roles_for(ActorRole.SELF_IMPROVER)


def test_self_reflection_gets_evaluative_but_not_fiction_or_deep_recall() -> None:
    roles = database_roles_for(ActorRole.SELF_REFLECTION)
    assert "memory_creative_evaluative_reader" in roles
    assert "memory_fiction_reader" not in roles
    assert "memory_deep_recall_reader" not in roles


def test_only_retrospective_and_auditor_get_deep_recall() -> None:
    holders = {
        actor for actor, roles in ACTOR_DATABASE_ROLES.items() if "memory_deep_recall_reader" in roles
    }
    assert holders == {ActorRole.RETROSPECTIVE, ActorRole.AUDITOR}
