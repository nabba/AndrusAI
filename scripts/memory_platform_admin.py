#!/usr/bin/env python3
"""Read-only inventory and gated state administration for memory migration."""

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

from app.memory_platform.inventory import build_inventory, inventory_as_dict
from app.memory_platform.migration_state import (
    MigrationPhase,
    MigrationStateStore,
    ReadinessPolicy,
    readiness_failures,
)
from app.memory_platform.registry import MEMORY_SPACES


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state-root",
        type=Path,
        default=Path(os.environ.get("MEMORY_PLATFORM_STATE_ROOT", "workspace/memory_platform/migrations")),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    inventory = commands.add_parser("inventory", help="inventory ledgers without opening Chroma")
    inventory.add_argument(
        "--workspace-root",
        type=Path,
        default=Path(os.environ.get("WORKSPACE_ROOT", "workspace")),
    )
    inventory.add_argument(
        "--chroma-root",
        type=Path,
        default=Path(os.environ.get("CHROMA_DATA_ROOT", "workspace")),
    )
    inventory.add_argument("--output", type=Path)

    initialise = commands.add_parser("init", help="create DISCOVERED state for spaces")
    initialise.add_argument("spaces", nargs="*", choices=sorted(MEMORY_SPACES))

    status = commands.add_parser("status", help="show one or all per-space states")
    status.add_argument("space", nargs="?", choices=sorted(MEMORY_SPACES))

    advance = commands.add_parser("advance", help="advance one gated migration phase")
    advance.add_argument("space", choices=sorted(MEMORY_SPACES))
    advance.add_argument("phase", choices=[phase.value for phase in MigrationPhase])
    advance.add_argument("--reason", required=True)
    advance.add_argument("--operator-approval-id")

    record_backfill = commands.add_parser(
        "record-backfill",
        help="record exact source/target parity and enter BACKFILLED",
    )
    record_backfill.add_argument("space", choices=sorted(MEMORY_SPACES))
    record_backfill.add_argument("--expected-records", type=int, required=True)
    record_backfill.add_argument("--migrated-records", type=int, required=True)
    record_backfill.add_argument("--source-checkpoint", required=True)

    readiness = commands.add_parser("readiness", help="evaluate shadow-read cutover gates")
    readiness.add_argument("space", choices=sorted(MEMORY_SPACES))
    readiness.add_argument("--min-queries", type=int, default=500)
    readiness.add_argument("--min-days", type=float, default=7.0)
    return parser


def _write_json(payload: object, output: Path | None = None) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if output is None:
        print(text, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(output)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv or sys.argv[1:])
    store = MigrationStateStore(args.state_root.resolve())
    if args.command == "inventory":
        payload = inventory_as_dict(
            build_inventory(args.workspace_root.resolve(), args.chroma_root.resolve())
        )
        _write_json(payload, args.output.resolve() if args.output else None)
        return 1 if payload["summary"]["unclassified_count"] else 0
    if args.command == "init":
        spaces = args.spaces or sorted(MEMORY_SPACES)
        states = []
        for space in spaces:
            state = store.load(space)
            store.save(state)
            states.append(asdict(state))
        _write_json(states)
        return 0
    if args.command == "status":
        spaces = [args.space] if args.space else sorted(MEMORY_SPACES)
        _write_json([asdict(store.load(space)) for space in spaces])
        return 0
    if args.command == "advance":
        state = store.transition(
            space=args.space,
            target=MigrationPhase(args.phase),
            reason=args.reason,
            operator_approval_id=args.operator_approval_id,
        )
        _write_json(asdict(state))
        return 0
    if args.command == "record-backfill":
        state = store.record_backfill(
            space=args.space,
            expected_records=args.expected_records,
            migrated_records=args.migrated_records,
            source_checkpoint=args.source_checkpoint,
        )
        _write_json(asdict(state))
        return 0
    if args.command == "readiness":
        state = store.load(args.space)
        failures = readiness_failures(
            state.metrics,
            ReadinessPolicy(
                min_shadow_queries=args.min_queries,
                min_observation_days=args.min_days,
            ),
        )
        _write_json(
            {
                "space": args.space,
                "phase": state.phase.value,
                "ready": not failures,
                "failures": failures,
                "metrics": asdict(state.metrics),
            }
        )
        return 0 if not failures else 1
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
