from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_backup_covers_both_physical_boundaries_and_hashes_artifacts() -> None:
    script = (ROOT / "deploy/scripts/memory-platform-backup.sh").read_text()
    assert "durable-memory-postgres" in script
    assert "governance-postgres" in script
    assert script.count("pg_dump") >= 2
    assert script.count("shasum -a 256") == 2
    assert "chmod 600" in script


def test_restore_drill_is_isolated_checksummed_and_self_cleaning() -> None:
    script = (ROOT / "deploy/scripts/memory-platform-restore-drill.sh").read_text()
    assert "botarmy-memory-restore-drill-" in script
    assert "checksum mismatch" in script
    assert script.count("pg_restore") >= 2
    assert "--check" in script
    assert "down -v --remove-orphans" in script


def test_example_requires_distinct_new_secrets() -> None:
    example = (ROOT / ".env.example").read_text()
    assert "DURABLE_MEMORY_POSTGRES_PASSWORD" in example
    assert "GOVERNANCE_POSTGRES_PASSWORD" in example


def test_backfill_wrapper_enforces_single_writer_gate() -> None:
    script = (ROOT / "deploy/scripts/memory-platform-backfill.sh").read_text()
    assert "ps --status running -q gateway" in script
    assert "gateway is running" in script
    assert "memory-platform-backfill" in script


def test_migration_and_backfill_jobs_share_one_reusable_tools_image() -> None:
    compose = (ROOT / "deploy/memory-platform.compose.yml").read_text()
    assert compose.count("MEMORY_PLATFORM_TOOLS_IMAGE") == 3
    assert compose.count("crewai-team-gateway:latest") == 3
    assert "build: ." not in compose
    assert compose.count("apply_memory_platform_migrations.py:ro") == 2
