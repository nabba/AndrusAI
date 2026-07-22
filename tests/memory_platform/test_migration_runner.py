from pathlib import Path

import pytest

from scripts.apply_memory_platform_migrations import (
    MigrationError,
    MigrationRunner,
    discover_migrations,
)


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, sql, params=None):
        self.connection.executed.append((sql, params))
        if "FROM platform_migrations.schema_migrations" in sql:
            self.rows = list(self.connection.existing)
        if self.connection.fail_on and self.connection.fail_on in sql:
            raise RuntimeError("synthetic SQL failure")

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, existing=(), fail_on=None):
        self.existing = existing
        self.fail_on = fail_on
        self.executed = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def migration_dir(tmp_path: Path, sql: str = "SELECT 1;") -> Path:
    directory = tmp_path / "migrations"
    directory.mkdir()
    (directory / "001_first.sql").write_text(sql)
    return directory


def test_discovery_is_strict_and_checksummed(tmp_path) -> None:
    migrations = discover_migrations(migration_dir(tmp_path))
    assert migrations[0].version == "001"
    assert len(migrations[0].sha256) == 64


def test_invalid_filename_is_refused(tmp_path) -> None:
    directory = tmp_path / "migrations"
    directory.mkdir()
    (directory / "bad.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError, match="invalid migration filename"):
        discover_migrations(directory)


def test_runner_applies_and_records_in_one_stream(tmp_path) -> None:
    connection = FakeConnection()
    result = MigrationRunner(lambda dsn: connection).apply(
        target="durable",
        dsn="secret-dsn",
        migrations=discover_migrations(migration_dir(tmp_path)),
    )
    assert result.applied == ("001_first.sql",)
    assert any("INSERT INTO platform_migrations.schema_migrations" in sql for sql, _ in connection.executed)
    assert connection.commits >= 2
    assert connection.closed is True


def test_applied_checksum_drift_is_refused(tmp_path) -> None:
    migration = discover_migrations(migration_dir(tmp_path))[0]
    connection = FakeConnection(existing=[("001", migration.name, "0" * 64)])
    with pytest.raises(MigrationError, match="checksum/name drift"):
        MigrationRunner(lambda dsn: connection).apply(
            target="durable",
            dsn="secret-dsn",
            migrations=[migration],
        )


def test_failed_migration_rolls_back_and_names_file(tmp_path) -> None:
    connection = FakeConnection(fail_on="BROKEN")
    with pytest.raises(MigrationError, match="001_first.sql"):
        MigrationRunner(lambda dsn: connection).apply(
            target="durable",
            dsn="secret-dsn",
            migrations=discover_migrations(migration_dir(tmp_path, "BROKEN;")),
        )
    assert connection.rollbacks >= 1
