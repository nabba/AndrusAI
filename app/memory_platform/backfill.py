"""Idempotent source exporters and batch backfill runner.

Backfill never changes the active read route.  It copies a stable source
snapshot into the target, suppresses target projection outbox events, and
produces reconciliation counts for the later DUAL_WRITE gate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Callable, Iterable, Iterator, Mapping, Protocol, Sequence, cast

from app.memory_platform.broker import new_memory_record
from app.memory_platform.models import (
    AccessAction,
    ActorRole,
    LegacyLocation,
    MemoryRecord,
    MemorySpace,
    Principal,
)
from app.memory_platform.registry import get_memory_space


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    backend: str
    location: str
    record_count: int


@dataclass(slots=True)
class BackfillReport:
    space: str
    dry_run: bool
    source_records: int = 0
    validated_records: int = 0
    written_records: int = 0
    batches: int = 0
    missing_embeddings: int = 0
    snapshots: list[SourceSnapshot] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class BatchTarget(Protocol):
    def put_many(
        self,
        *,
        space: MemorySpace,
        principal: Principal,
        records: Sequence[MemoryRecord],
    ) -> int: ...


class BackfillRunner:
    """Validate and idempotently copy batches into a target backend."""

    def __init__(self, target: BatchTarget) -> None:
        self._target = target

    def run(
        self,
        *,
        space_key: str,
        records: Iterable[MemoryRecord],
        snapshots: Sequence[SourceSnapshot] = (),
        batch_size: int = 100,
        dry_run: bool = True,
        max_records: int | None = None,
        tenant_id: str | None = None,
    ) -> BackfillReport:
        space = get_memory_space(space_key)
        principal = Principal(
            role=ActorRole.RECONCILER,
            actor_id="memory-platform-backfill",
            tenant_id=tenant_id,
        )
        if not space.permits(principal, AccessAction.WRITE):
            raise PermissionError(f"reconciler may not write {space_key}")
        report = BackfillReport(space=space_key, dry_run=dry_run, snapshots=list(snapshots))
        batch: list[MemoryRecord] = []
        for record in records:
            if max_records is not None and report.source_records >= max_records:
                report.warnings.append(f"stopped at max_records={max_records}")
                break
            report.source_records += 1
            self._validate_record(space, record)
            if space.tenant_scoped and record.tenant_id != tenant_id:
                raise ValueError(
                    f"tenant record {record.memory_id} belongs to {record.tenant_id}, expected {tenant_id}"
                )
            report.validated_records += 1
            if record.embedding is None:
                report.missing_embeddings += 1
            batch.append(record)
            if len(batch) >= max(1, batch_size):
                self._flush(space, principal, batch, report)
                batch = []
        if batch:
            self._flush(space, principal, batch, report)
        return report

    def _flush(
        self,
        space: MemorySpace,
        principal: Principal,
        batch: Sequence[MemoryRecord],
        report: BackfillReport,
    ) -> None:
        report.batches += 1
        if report.dry_run:
            return
        report.written_records += self._target.put_many(
            space=space,
            principal=principal,
            records=batch,
        )

    @staticmethod
    def _validate_record(space: MemorySpace, record: MemoryRecord) -> None:
        if record.space != space.key:
            raise ValueError(f"record belongs to {record.space}, expected {space.key}")
        if record.epistemic_class is not space.epistemic_class:
            raise ValueError(f"epistemic class mismatch for {record.memory_id}")
        if not record.source_uri or not record.source_record_id or not record.provenance:
            raise ValueError(f"record lacks provenance: {record.memory_id}")
        if space.tenant_scoped and not record.tenant_id:
            raise ValueError(f"tenant record lacks tenant_id: {record.memory_id}")


class ChromaSnapshotExporter:
    """Export stable ID snapshots from existing embedded Chroma collections."""

    def __init__(self, space_key: str, *, strict_ledger_parity: bool = True) -> None:
        self.space = get_memory_space(space_key)
        self.strict_ledger_parity = strict_ledger_parity
        canonical = tuple(
            location
            for location in self.space.legacy
            if location.backend == "chroma" and location.source_kind == "canonical"
        )
        self.locations = canonical or tuple(
            location for location in self.space.legacy if location.backend == "chroma"
        )
        self.snapshots: list[SourceSnapshot] = []
        self._ledger_cache: dict[str, dict[str, int]] = {}

    def records(self, batch_size: int = 250) -> Iterator[MemoryRecord]:
        from app.memory import chromadb_manager
        from app.memory_platform.inventory import ledger_counts
        from app.paths import WORKSPACE_ROOT, chroma_kb_dir

        for location in self.locations:
            if location.backend != "chroma" or not location.kb_name or not location.collection:
                continue
            client = cast(
                Any,
                chromadb_manager.get_client_for_path(chroma_kb_dir(location.kb_name)),
            )
            try:
                collection = client.get_collection(location.collection)
            except Exception as exc:
                raise RuntimeError(
                    f"missing legacy collection {location.kb_name}/{location.collection}"
                ) from exc
            id_result = collection.get(include=[])
            ids = sorted(str(value) for value in (id_result.get("ids") or []))
            ledger = WORKSPACE_ROOT / location.kb_name / ".source_ledger.jsonl"
            if ledger.exists():
                if location.kb_name not in self._ledger_cache:
                    _, expected_counts = ledger_counts(ledger)
                    self._ledger_cache[location.kb_name] = expected_counts
                expected_counts = self._ledger_cache[location.kb_name]
                expected = expected_counts.get(location.collection)
                if expected is not None and expected != len(ids):
                    message = (
                        f"source/ledger parity failure for {location.kb_name}/{location.collection}: "
                        f"Chroma has {len(ids)}, ledger resolves to {expected}; use the live CHROMA_DATA_ROOT "
                        "or rebuild before backfill"
                    )
                    if self.strict_ledger_parity:
                        raise RuntimeError(message)
            self.snapshots.append(
                SourceSnapshot(
                    backend="chroma",
                    location=f"{location.kb_name}/{location.collection}",
                    record_count=len(ids),
                )
            )
            for start in range(0, len(ids), max(1, batch_size)):
                page_ids = ids[start : start + batch_size]
                raw = collection.get(
                    ids=page_ids,
                    include=["documents", "metadatas", "embeddings"],
                )
                yield from self._convert_page(location, raw)

    def _convert_page(
        self,
        location: LegacyLocation,
        raw: Mapping[str, Any],
    ) -> Iterator[MemoryRecord]:
        ids = list(raw.get("ids") or [])
        documents = list(raw.get("documents") or [])
        metadatas = list(raw.get("metadatas") or [])
        embeddings_raw = raw.get("embeddings")
        embeddings = list(embeddings_raw) if embeddings_raw is not None else []
        for index, legacy_id_raw in enumerate(ids):
            legacy_id = str(legacy_id_raw)
            content = str(documents[index] or "") if index < len(documents) else ""
            metadata = dict(metadatas[index] or {}) if index < len(metadatas) else {}
            vector_raw = embeddings[index] if index < len(embeddings) else None
            vector = [float(value) for value in vector_raw] if vector_raw is not None else None
            source_record_id = f"{location.kb_name}/{location.collection}/{legacy_id}"
            source_uri = str(
                metadata.get("source_uri")
                or metadata.get("source")
                or f"legacy-chroma://{source_record_id}"
            )
            yield new_memory_record(
                space=self.space.key,
                content=content,
                source_uri=source_uri,
                source_record_id=source_record_id,
                provenance={
                    "backend": "chroma",
                    "kb_name": location.kb_name,
                    "collection": location.collection,
                    "legacy_id": legacy_id,
                    "source_kind": location.source_kind,
                },
                embedding=vector,
                owner_agent_id=_optional_str(metadata.get("owner_agent_id") or metadata.get("agent")),
                tenant_id=_optional_str(metadata.get("tenant_id")),
                workspace_id=_optional_str(metadata.get("workspace_id") or metadata.get("project")),
                attributes=metadata,
            )


class BeliefPostgresExporter:
    """Export the canonical legacy ``public.beliefs`` table, not projections."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory
        self.snapshots: list[SourceSnapshot] = []

    def records(self, batch_size: int = 250) -> Iterator[MemoryRecord]:
        del batch_size
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT belief_id::text, content, content_embedding::text,
                           domain, confidence, evidence_sources, formed_at,
                           last_validated, last_updated, metacognitive_flags,
                           update_history, belief_status, superseded_by::text
                      FROM beliefs
                     ORDER BY belief_id
                    """
                )
                rows = cursor.fetchall()
        finally:
            connection.close()
        self.snapshots.append(
            SourceSnapshot(
                backend="postgres",
                location="public.beliefs",
                record_count=len(rows),
            )
        )
        for row in rows:
            belief_id = str(row[0])
            evidence = _json_value(row[5], [])
            flags = _json_value(row[9], [])
            history = _json_value(row[10], [])
            source_uri = f"legacy-postgres://mem0/public/beliefs/{belief_id}"
            status = str(row[11] or "ACTIVE").casefold()
            canonical_status = {
                "active": "active",
                "suspended": "superseded",
                "retracted": "retracted",
                "superseded": "superseded",
            }.get(status, "active")
            record = new_memory_record(
                space="identity.beliefs",
                content=str(row[1]),
                source_uri=source_uri,
                source_record_id=belief_id,
                provenance={
                    "backend": "postgres",
                    "table": "public.beliefs",
                    "legacy_id": belief_id,
                    "source_kind": "canonical",
                    "evidence_sources": evidence,
                },
                embedding=_parse_vector(row[2]),
                attributes={
                    "domain": row[3],
                    "last_validated": _iso(row[7]),
                    "last_updated": _iso(row[8]),
                    "metacognitive_flags": flags,
                    "update_history": history,
                    "legacy_status": row[11],
                    "superseded_by": row[12],
                    "formed_at": _iso(row[6]),
                    "confidence": float(row[4]) if row[4] is not None else None,
                    "canonical_status": canonical_status,
                },
            )
            yield replace(
                record,
                confidence=float(row[4]) if row[4] is not None else None,
                event_time=row[6],
                status=canonical_status,
            )


def _parse_vector(value: object) -> list[float] | None:
    if value in (None, ""):
        return None
    if isinstance(value, (list, tuple)):
        return [float(item) for item in value]
    text = str(value).strip().strip("[]")
    if not text:
        return None
    return [float(item) for item in text.split(",")]


def _json_value(value: object, default: object) -> object:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return default


def _iso(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value not in (None, "") else None


def _optional_str(value: object) -> str | None:
    return str(value) if value not in (None, "") else None
