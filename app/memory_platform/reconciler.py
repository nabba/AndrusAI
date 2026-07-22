"""Source-ledger to durable-platform reconciler for tracked dual-write.

This is designed to run inside the existing Chroma single-writer process.  It
must not be launched as a second process against the live Chroma volume.  The
ledger provides the durable change notification; Chroma is queried only to
materialise the current document after partial update rows.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence, cast

from app.memory_platform.broker import new_memory_record
from app.memory_platform.inventory import classify_collection
from app.memory_platform.models import (
    ActorRole,
    Durability,
    MemoryRecord,
    MemorySpace,
    Principal,
)
from app.memory_platform.registry import get_memory_space


class CurrentRecordLookup(Protocol):
    def get(
        self,
        *,
        kb_name: str,
        collection: str,
        doc_id: str,
        space: MemorySpace,
    ) -> MemoryRecord | None: ...


class ReconcileTarget(Protocol):
    def put_many(
        self,
        *,
        space: MemorySpace,
        principal: Principal,
        records: Sequence[MemoryRecord],
    ) -> int: ...

    def set_status(
        self,
        *,
        space: MemorySpace,
        principal: Principal,
        source_record_id: str,
        status: str,
    ) -> int: ...


@dataclass(frozen=True, slots=True)
class ReconcileCheckpoint:
    kb_name: str
    rows_processed: int = 0
    last_hash: str = "0" * 64
    first_row_hash: str | None = None


@dataclass(slots=True)
class ReconcileReport:
    kb_name: str
    rows_seen: int = 0
    durable_upserts: int = 0
    durable_retractions: int = 0
    operational_skipped: int = 0
    last_hash: str = "0" * 64
    warnings: list[str] | None = None

    def __post_init__(self) -> None:
        if self.warnings is None:
            self.warnings = []


class ReconcileError(RuntimeError):
    """The ledger or source projection cannot be reconciled safely."""


class ReconcileCheckpointStore:
    """Atomic checkpoint store separate from the source ledger."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._lock = threading.Lock()

    def load(self, kb_name: str) -> ReconcileCheckpoint:
        path = self._path(kb_name)
        if not path.exists():
            return ReconcileCheckpoint(kb_name=kb_name)
        return ReconcileCheckpoint(**json.loads(path.read_text(encoding="utf-8")))

    def save(self, checkpoint: ReconcileCheckpoint) -> None:
        with self._lock:
            self._root.mkdir(parents=True, exist_ok=True)
            path = self._path(checkpoint.kb_name)
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(asdict(checkpoint), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)

    def _path(self, kb_name: str) -> Path:
        safe = "".join(char if char.isalnum() or char in "_.-" else "_" for char in kb_name)
        return self._root / f"{safe}.json"


class ChromaCurrentRecordLookup:
    """Materialise a current Chroma record from within its owning process."""

    def get(
        self,
        *,
        kb_name: str,
        collection: str,
        doc_id: str,
        space: MemorySpace,
    ) -> MemoryRecord | None:
        from app.memory import chromadb_manager
        from app.paths import chroma_kb_dir

        client = cast(
            Any,
            chromadb_manager.get_client_for_path(chroma_kb_dir(kb_name)),
        )
        try:
            chroma_collection = client.get_collection(collection)
            raw = chroma_collection.get(
                ids=[doc_id],
                include=["documents", "metadatas", "embeddings"],
            )
        except Exception as exc:
            raise ReconcileError(f"cannot read Chroma source {kb_name}/{collection}/{doc_id}") from exc
        ids = list(raw.get("ids") or [])
        if not ids:
            return None
        documents = list(raw.get("documents") or [])
        metadatas = list(raw.get("metadatas") or [])
        embeddings_raw = raw.get("embeddings")
        embeddings = list(embeddings_raw) if embeddings_raw is not None else []
        content = str(documents[0] or "") if documents else ""
        metadata = dict(metadatas[0] or {}) if metadatas else {}
        vector = [float(value) for value in embeddings[0]] if embeddings else None
        source_record_id = f"{kb_name}/{collection}/{doc_id}"
        return new_memory_record(
            space=space.key,
            content=content,
            source_uri=str(
                metadata.get("source_uri")
                or metadata.get("source")
                or f"legacy-chroma://{source_record_id}"
            ),
            source_record_id=source_record_id,
            provenance={
                "backend": "chroma",
                "kb_name": kb_name,
                "collection": collection,
                "legacy_id": doc_id,
                "source_kind": "projection",
            },
            embedding=vector,
            owner_agent_id=_optional_str(metadata.get("owner_agent_id") or metadata.get("agent")),
            tenant_id=_optional_str(metadata.get("tenant_id")),
            workspace_id=_optional_str(metadata.get("workspace_id") or metadata.get("project")),
            attributes=metadata,
        )


class SourceLedgerReconciler:
    """Advance a verified ledger tail only after idempotent target writes."""

    def __init__(
        self,
        *,
        target: ReconcileTarget,
        lookup: CurrentRecordLookup,
        checkpoints: ReconcileCheckpointStore,
    ) -> None:
        self._target = target
        self._lookup = lookup
        self._checkpoints = checkpoints

    def run(self, kb_name: str, *, max_rows: int = 500) -> ReconcileReport:
        from app.memory.source_ledger import read_all, verify_chain_incremental

        verification = verify_chain_incremental(kb_name)
        if not verification.ok:
            raise ReconcileError(
                f"source ledger integrity failure at row {verification.first_bad_row}: "
                f"{verification.first_bad_reason}"
            )
        checkpoint = self._checkpoints.load(kb_name)
        rows = []
        total_rows = 0
        first_row_hash: str | None = None
        for index, row in enumerate(read_all(kb_name)):
            total_rows = index + 1
            if first_row_hash is None:
                first_row_hash = row.hash
            if index < checkpoint.rows_processed:
                continue
            rows.append(row)
            if len(rows) >= max(1, max_rows):
                break
        if checkpoint.rows_processed > total_rows:
            raise ReconcileError(
                f"ledger shrank below checkpoint for {kb_name}: {total_rows} < {checkpoint.rows_processed}"
            )
        if (
            checkpoint.first_row_hash is not None
            and first_row_hash != checkpoint.first_row_hash
        ):
            raise ReconcileError(
                f"ledger genesis changed after compaction for {kb_name}; perform a fresh backfill"
            )
        if not rows:
            return ReconcileReport(kb_name=kb_name, last_hash=checkpoint.last_hash)
        if rows[0].prev_hash != checkpoint.last_hash:
            raise ReconcileError(
                f"checkpoint/ledger mismatch for {kb_name}: expected prev {checkpoint.last_hash[:8]}, "
                f"found {rows[0].prev_hash[:8]}"
            )
        report = reconcile_rows(
            kb_name=kb_name,
            rows=rows,
            target=self._target,
            lookup=self._lookup,
        )
        self._checkpoints.save(
            ReconcileCheckpoint(
                kb_name=kb_name,
                rows_processed=checkpoint.rows_processed + report.rows_seen,
                last_hash=report.last_hash,
                first_row_hash=checkpoint.first_row_hash or first_row_hash,
            )
        )
        return report


def reconcile_rows(
    *,
    kb_name: str,
    rows: Iterable[object],
    target: ReconcileTarget,
    lookup: CurrentRecordLookup,
) -> ReconcileReport:
    """Reconcile an already verified, contiguous ledger suffix."""

    report = ReconcileReport(kb_name=kb_name)
    principal = Principal(role=ActorRole.RECONCILER, actor_id="memory-platform-reconciler")
    final_actions: dict[tuple[str, str], tuple[MemorySpace, str, str, str]] = {}
    last_hash = report.last_hash
    for row in rows:
        collection = str(getattr(row, "collection"))
        doc_id = str(getattr(row, "doc_id"))
        row_hash = str(getattr(row, "hash"))
        space_key = classify_collection(collection)
        if space_key is None:
            raise ReconcileError(f"unclassified collection blocks checkpoint: {kb_name}/{collection}")
        space = get_memory_space(space_key)
        report.rows_seen += 1
        last_hash = row_hash
        if space.durability is Durability.OPERATIONAL:
            report.operational_skipped += 1
            continue
        if space.tenant_scoped:
            raise ReconcileError(
                f"tenant collection requires a tenant-scoped reconciler: {kb_name}/{collection}"
            )
        source_record_id = f"{kb_name}/{collection}/{doc_id}"
        operation = str(getattr(row, "op", "add"))
        final_actions[(space.key, source_record_id)] = (
            space,
            operation,
            collection,
            doc_id,
        )

    pending: dict[str, list[MemoryRecord]] = {}
    retractions: list[tuple[MemorySpace, str]] = []
    for (_, source_record_id), (space, operation, collection, doc_id) in final_actions.items():
        if operation == "delete":
            retractions.append((space, source_record_id))
            continue
        record = lookup.get(
            kb_name=kb_name,
            collection=collection,
            doc_id=doc_id,
            space=space,
        )
        if record is None:
            raise ReconcileError(
                f"ledger row exists but Chroma record is missing: {kb_name}/{collection}/{doc_id}"
            )
        pending.setdefault(space.key, []).append(record)

    for space_key, records in pending.items():
        space = get_memory_space(space_key)
        report.durable_upserts += target.put_many(
            space=space,
            principal=principal,
            records=records,
        )
    for space, source_record_id in retractions:
        report.durable_retractions += target.set_status(
            space=space,
            principal=principal,
            source_record_id=source_record_id,
            status="retracted",
        )
    report.last_hash = last_hash
    return report


def _optional_str(value: object) -> str | None:
    return str(value) if value not in (None, "") else None
