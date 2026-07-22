from pathlib import Path

from app.memory_platform.models import Durability
from app.memory_platform.registry import MEMORY_SPACES

ROOT = Path(__file__).resolve().parents[2]


def test_durable_schema_uses_separate_tables_and_indexes() -> None:
    sql = (ROOT / "migrations/memory_platform/001_durable_memory.sql").read_text()
    assert "knowledge.episteme_chunks" in sql
    assert "autobiographical.episodic_full" in sql
    assert "autobiographical.episodic_curated" in sql
    assert "creative.fiction_chunks" in sql
    assert "USING hnsw" in sql
    assert "USING gin" in sql


def test_sql_registry_contains_every_durable_space() -> None:
    sql = (ROOT / "migrations/memory_platform/001_durable_memory.sql").read_text()
    for key, space in MEMORY_SPACES.items():
        if space.durability is Durability.DURABLE:
            assert f"('{key}', '{space.qualified_table}'" in sql


def test_curated_reader_is_not_granted_full_episode_table() -> None:
    sql = (ROOT / "migrations/memory_platform/001_durable_memory.sql").read_text()
    curated_grant = sql.split("GRANT SELECT ON autobiographical.experiential_entries", 1)[1].split(";", 1)[0]
    assert "autobiographical.episodic_curated" in curated_grant
    assert "autobiographical.episodic_full" not in curated_grant


def test_fiction_and_evaluative_database_roles_are_separate() -> None:
    sql = (ROOT / "migrations/memory_platform/001_durable_memory.sql").read_text()
    fiction_grant = sql.split("GRANT SELECT ON creative.fiction_chunks", 1)[1].split(";", 1)[0]
    evaluative_grant = sql.split("GRANT SELECT ON creative.aesthetic_patterns", 1)[1].split(";", 1)[0]
    assert "memory_fiction_reader" in fiction_grant
    assert "memory_creative_evaluative_reader" not in fiction_grant
    assert "memory_creative_evaluative_reader" in evaluative_grant
    assert "creative.fiction_chunks" not in evaluative_grant


def test_tenant_tables_force_rls() -> None:
    sql = (ROOT / "migrations/memory_platform/001_durable_memory.sql").read_text()
    assert sql.count("FORCE ROW LEVEL SECURITY") == 3
    assert "current_setting(''app.tenant_id'', true)" in sql


def test_governance_is_append_only_and_hash_chained() -> None:
    sql = (ROOT / "deploy/governance/migrations/001_governance_boundary.sql").read_text()
    assert "pg_advisory_xact_lock" in sql
    assert "previous_hash" in sql
    assert "event_hash" in sql
    assert "BEFORE UPDATE OR DELETE" in sql
    assert "governance history is append-only" in sql


def test_compose_overlay_has_distinct_internal_volumes_and_no_ports() -> None:
    compose = (ROOT / "deploy/memory-platform.compose.yml").read_text()
    assert "durable_memory_pgdata" in compose
    assert "governance_pgdata" in compose
    assert "networks: [internal]" in compose
    assert "governance_boundary:\n    internal: true" in compose
    governance_db = compose.split("  governance-postgres:", 1)[1].split(
        "  durable-memory-migrate:", 1
    )[0]
    governance_migrator = compose.split("  governance-migrate:", 1)[1].split(
        "  memory-platform-backfill:", 1
    )[0]
    assert "networks: [governance_boundary]" in governance_db
    assert "networks: [governance_boundary]" in governance_migrator
    assert "ports:" not in compose
