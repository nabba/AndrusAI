#!/usr/bin/env python3
"""Backfill one registered memory space without changing its active read route."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.memory_platform.backfill import (
    BackfillRunner,
    BeliefPostgresExporter,
    ChromaSnapshotExporter,
)
from app.memory_platform.models import Durability
from app.memory_platform.postgres_backend import PgVectorBackend
from app.memory_platform.registry import MEMORY_SPACES, get_memory_space


class _DryRunTarget:
    def put_many(self, **kwargs: object) -> int:
        raise AssertionError("dry-run target must never be called")


def _args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("space", choices=sorted(MEMORY_SPACES))
    parser.add_argument("--source", choices=("auto", "chroma", "belief-postgres"), default="auto")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--max-records", type=int)
    parser.add_argument("--tenant-id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--target-dsn-env", default="DURABLE_MEMORY_DATABASE_URL")
    parser.add_argument("--legacy-postgres-dsn-env", default="MEM0_POSTGRES_URL")
    return parser.parse_args(argv)


def _write(payload: dict[str, object], path: Path | None) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path is None:
        print(text, end="")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    args = _args(argv or sys.argv[1:])
    space = get_memory_space(args.space)
    if space.durability is not Durability.DURABLE:
        raise SystemExit(f"backfill refused: {args.space} is not a durable memory space")
    source_kind = args.source
    if source_kind == "auto":
        source_kind = "belief-postgres" if args.space == "identity.beliefs" else "chroma"

    if source_kind == "belief-postgres":
        source_dsn = os.environ.get(args.legacy_postgres_dsn_env, "").strip()
        if not source_dsn:
            raise SystemExit(f"{args.legacy_postgres_dsn_env} is required for belief export")
        import psycopg2

        exporter = BeliefPostgresExporter(lambda: psycopg2.connect(source_dsn))
    else:
        exporter = ChromaSnapshotExporter(args.space)

    if args.dry_run:
        target = _DryRunTarget()
    else:
        target_dsn = os.environ.get(args.target_dsn_env, "").strip()
        if not target_dsn:
            raise SystemExit(f"{args.target_dsn_env} is required unless --dry-run is used")
        import psycopg2

        target = PgVectorBackend(
            lambda: psycopg2.connect(target_dsn),
            suppress_outbox=True,
        )

    report = BackfillRunner(target).run(
        space_key=args.space,
        records=exporter.records(batch_size=max(1, args.batch_size)),
        snapshots=(),
        batch_size=max(1, args.batch_size),
        dry_run=args.dry_run,
        max_records=args.max_records,
        tenant_id=args.tenant_id,
    )
    report.snapshots = list(exporter.snapshots)
    _write(asdict(report), args.report.resolve() if args.report else None)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as exc:
        print(f"memory-space backfill refused: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
