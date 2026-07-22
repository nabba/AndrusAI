from dataclasses import dataclass

import pytest

from app.memory_platform.broker import new_memory_record, stable_memory_id
from app.memory_platform.reconciler import ReconcileError, reconcile_rows


@dataclass
class Row:
    collection: str
    doc_id: str
    hash: str
    op: str = "add"


class Lookup:
    def __init__(self) -> None:
        self.calls = []

    def get(self, *, kb_name, collection, doc_id, space):
        self.calls.append((kb_name, collection, doc_id, space.key))
        return new_memory_record(
            space=space.key,
            content=f"current {doc_id}",
            source_uri=f"legacy://{doc_id}",
            source_record_id=f"{kb_name}/{collection}/{doc_id}",
            provenance={"test": True},
        )


class Target:
    def __init__(self) -> None:
        self.upserts = []
        self.retractions = []

    def put_many(self, *, space, principal, records):
        self.upserts.extend((space.key, record.source_record_id) for record in records)
        return len(records)

    def set_status(self, *, space, principal, source_record_id, status):
        self.retractions.append((space.key, source_record_id, status))
        return 1


def test_stable_id_survives_content_revision() -> None:
    first_id, first_hash = stable_memory_id("knowledge.episteme", "source-1", "first")
    second_id, second_hash = stable_memory_id("knowledge.episteme", "source-1", "second")
    assert first_id == second_id
    assert first_hash != second_hash


def test_final_delete_wins_without_reading_missing_chroma_record() -> None:
    target = Target()
    lookup = Lookup()
    report = reconcile_rows(
        kb_name="aesthetics",
        rows=[
            Row("aesthetic_patterns", "x", "1" * 64, "add"),
            Row("aesthetic_patterns", "x", "2" * 64, "delete"),
        ],
        target=target,
        lookup=lookup,
    )
    assert lookup.calls == []
    assert target.upserts == []
    assert target.retractions == [
        ("creative.aesthetics", "aesthetics/aesthetic_patterns/x", "retracted")
    ]
    assert report.last_hash == "2" * 64


def test_final_add_after_delete_wins() -> None:
    target = Target()
    lookup = Lookup()
    reconcile_rows(
        kb_name="aesthetics",
        rows=[
            Row("aesthetic_patterns", "x", "1" * 64, "delete"),
            Row("aesthetic_patterns", "x", "2" * 64, "add"),
        ],
        target=target,
        lookup=lookup,
    )
    assert target.upserts == [("creative.aesthetics", "aesthetics/aesthetic_patterns/x")]
    assert target.retractions == []


def test_operational_rows_are_not_copied_to_durable_platform() -> None:
    target = Target()
    report = reconcile_rows(
        kb_name="memory",
        rows=[Row("scope_tech_radar", "x", "1" * 64)],
        target=target,
        lookup=Lookup(),
    )
    assert report.operational_skipped == 1
    assert target.upserts == []


def test_unclassified_collection_blocks_checkpoint() -> None:
    with pytest.raises(ReconcileError, match="unclassified collection"):
        reconcile_rows(
            kb_name="memory",
            rows=[Row("mystery", "x", "1" * 64)],
            target=Target(),
            lookup=Lookup(),
        )
