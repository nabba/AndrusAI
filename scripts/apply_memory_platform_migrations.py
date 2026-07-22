#!/usr/bin/env python3
"""Apply checksummed migrations to a dedicated memory-platform database.

This runner intentionally does not scan the repository's historical
``migrations/*.sql`` directory.  The existing Mem0 database was not managed by
a durable migration ledger, and replaying those files is unsafe.  Durable
memory and governance each have a new, explicit migration stream.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Sequence

_MIGRATION_NAME = re.compile(r"^(?P<version>\d{3})_[a-z0-9_]+\.sql$")


@dataclass(frozen=True, slots=True)
class Migration:
    version: str
    name: str
    path: Path
    sha256: str
    sql: str


@dataclass(frozen=True, slots=True)
class MigrationResult:
    target: str
    discovered: tuple[str, ...]
    applied: tuple[str, ...]
    already_applied: tuple[str, ...]
    dry_run: bool


class MigrationError(RuntimeError):
    """A migration is invalid, divergent, or failed to apply."""


def discover_migrations(directory: Path) -> list[Migration]:
    """Load a strictly named, duplicate-free migration stream."""

    if not directory.is_dir():
        raise MigrationError(f"migration directory does not exist: {directory}")
    migrations: list[Migration] = []
    versions: set[str] = set()
    for path in sorted(directory.glob("*.sql")):
        match = _MIGRATION_NAME.fullmatch(path.name)
        if match is None:
            raise MigrationError(f"invalid migration filename: {path.name}")
        version = match.group("version")
        if version in versions:
            raise MigrationError(f"duplicate migration version: {version}")
        versions.add(version)
        sql = path.read_text(encoding="utf-8")
        if not sql.strip():
            raise MigrationError(f"empty migration: {path.name}")
        migrations.append(
            Migration(
                version=version,
                name=path.name,
                path=path,
                sha256=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
                sql=sql,
            )
        )
    if not migrations:
        raise MigrationError(f"no migrations found in {directory}")
    return migrations


class MigrationRunner:
    """Transactional, checksummed migration runner with a database lock."""

    def __init__(self, connector: Callable[[str], object]) -> None:
        self._connector = connector

    def apply(
        self,
        *,
        target: str,
        dsn: str,
        migrations: Sequence[Migration],
        check_only: bool = False,
    ) -> MigrationResult:
        conn = self._connector(dsn)
        applied: list[str] = []
        existing_names: list[str] = []
        try:
            with conn.cursor() as cur:
                cur.execute("CREATE SCHEMA IF NOT EXISTS platform_migrations")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS platform_migrations.schema_migrations (
                        target text NOT NULL,
                        version text NOT NULL,
                        name text NOT NULL,
                        sha256 text NOT NULL CHECK (length(sha256) = 64),
                        applied_at timestamptz NOT NULL DEFAULT now(),
                        PRIMARY KEY (target, version)
                    )
                    """
                )
            conn.commit()

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_lock(hashtext(%s))",
                    [f"botarmy-memory-platform:{target}"],
                )
                cur.execute(
                    """
                    SELECT version, name, sha256
                      FROM platform_migrations.schema_migrations
                     WHERE target = %s
                     ORDER BY version
                    """,
                    [target],
                )
                existing = {str(row[0]): (str(row[1]), str(row[2])) for row in cur.fetchall()}

            for migration in migrations:
                prior = existing.get(migration.version)
                if prior is not None:
                    prior_name, prior_sha = prior
                    if prior_name != migration.name or prior_sha != migration.sha256:
                        raise MigrationError(
                            f"checksum/name drift for applied migration {target}:{migration.version}"
                        )
                    existing_names.append(migration.name)
                    continue
                if check_only:
                    continue
                try:
                    with conn.cursor() as cur:
                        cur.execute(migration.sql)
                        cur.execute(
                            """
                            INSERT INTO platform_migrations.schema_migrations
                                (target, version, name, sha256)
                            VALUES (%s, %s, %s, %s)
                            """,
                            [target, migration.version, migration.name, migration.sha256],
                        )
                    conn.commit()
                except Exception as exc:
                    conn.rollback()
                    raise MigrationError(
                        f"failed migration {target}:{migration.name}: {type(exc).__name__}: {exc}"
                    ) from exc
                applied.append(migration.name)
            return MigrationResult(
                target=target,
                discovered=tuple(migration.name for migration in migrations),
                applied=tuple(applied),
                already_applied=tuple(existing_names),
                dry_run=False,
            )
        finally:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT pg_advisory_unlock(hashtext(%s))",
                        [f"botarmy-memory-platform:{target}"],
                    )
                conn.commit()
            except Exception:
                conn.rollback()
            conn.close()


def _defaults(repo_root: Path, target: str) -> tuple[Path, str]:
    if target == "durable":
        return repo_root / "migrations" / "memory_platform", "DURABLE_MEMORY_DATABASE_URL"
    if target == "governance":
        return repo_root / "deploy" / "governance" / "migrations", "GOVERNANCE_DATABASE_URL"
    raise MigrationError(f"unsupported migration target: {target}")


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("durable", "governance"), required=True)
    parser.add_argument("--dsn-env", help="environment variable containing the DSN")
    parser.add_argument("--migration-dir", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="validate files without connecting")
    parser.add_argument("--check", action="store_true", help="connect and verify drift without applying")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    repo_root = Path(__file__).resolve().parents[1]
    default_dir, default_env = _defaults(repo_root, args.target)
    migration_dir = (args.migration_dir or default_dir).resolve()
    migrations = discover_migrations(migration_dir)
    if args.dry_run:
        result = MigrationResult(
            target=args.target,
            discovered=tuple(migration.name for migration in migrations),
            applied=(),
            already_applied=(),
            dry_run=True,
        )
        print(json.dumps(asdict(result), indent=2, sort_keys=True))
        return 0

    env_name = args.dsn_env or default_env
    dsn = os.environ.get(env_name, "").strip()
    if not dsn:
        raise MigrationError(f"{env_name} is required")
    import psycopg2

    result = MigrationRunner(psycopg2.connect).apply(
        target=args.target,
        dsn=dsn,
        migrations=migrations,
        check_only=args.check,
    )
    print(json.dumps(asdict(result), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MigrationError as exc:
        print(f"memory-platform migration refused: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
