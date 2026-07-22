from datetime import datetime, timezone

import pytest

from app.memory_platform.backfill import (
    BackfillRunner,
    BeliefPostgresExporter,
    SourceSnapshot,
)
from app.memory_platform.broker import new_memory_record


class Target:
    def __init__(self) -> None:
        self.batches = []

    def put_many(self, **kwargs):
        records = list(kwargs["records"])
        self.batches.append(records)
        return len(records)


def records(count: int = 3):
    for index in range(count):
        yield new_memory_record(
            space="creative.aesthetics",
            content=f"pattern {index}",
            source_uri="source://patterns",
            source_record_id=str(index),
            provenance={"test": True},
        )


def test_dry_run_validates_without_writing() -> None:
    target = Target()
    report = BackfillRunner(target).run(
        space_key="creative.aesthetics",
        records=records(),
        snapshots=[SourceSnapshot("chroma", "aesthetics/aesthetic_patterns", 3)],
        batch_size=2,
        dry_run=True,
    )
    assert report.validated_records == 3
    assert report.written_records == 0
    assert report.batches == 2
    assert target.batches == []


def test_live_backfill_writes_bounded_batches() -> None:
    target = Target()
    report = BackfillRunner(target).run(
        space_key="creative.aesthetics",
        records=records(5),
        batch_size=2,
        dry_run=False,
    )
    assert report.written_records == 5
    assert [len(batch) for batch in target.batches] == [2, 2, 1]


def test_backfill_rejects_cross_space_record() -> None:
    wrong = new_memory_record(
        space="creative.fiction",
        content="imaginary",
        source_uri="source://fiction",
        source_record_id="1",
        provenance={"test": True},
    )
    with pytest.raises(ValueError, match="expected creative.aesthetics"):
        BackfillRunner(Target()).run(
            space_key="creative.aesthetics",
            records=[wrong],
        )


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, sql, params=None):
        assert "FROM beliefs" in sql

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False

    def cursor(self):
        return Cursor(self.rows)

    def close(self):
        self.closed = True


def test_belief_exporter_uses_canonical_postgres_fields() -> None:
    now = datetime.now(timezone.utc)
    row = (
        "00000000-0000-0000-0000-000000000001",
        "The world is revisable",
        "[" + ",".join(["0"] * 768) + "]",
        "world_model",
        0.8,
        [{"source": "test"}],
        now,
        now,
        now,
        [],
        [],
        "ACTIVE",
        None,
    )
    connection = Connection([row])
    exporter = BeliefPostgresExporter(lambda: connection)
    exported = list(exporter.records())
    assert len(exported) == 1
    assert exported[0].space == "identity.beliefs"
    assert exported[0].confidence == 0.8
    assert exported[0].provenance["source_kind"] == "canonical"
    assert len(exported[0].embedding or []) == 768
    assert connection.closed is True
