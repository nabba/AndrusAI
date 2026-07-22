"""Legacy Chroma adapter used for backfill, shadow reads, and operations.

Durable legacy collections are read-only through this adapter.  Only spaces
classified as operational may be written, preserving the existing rule that
durable Chroma indexes are projections rather than a new canonical source.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Sequence, cast

from app.memory_platform.models import (
    Durability,
    LegacyLocation,
    MemoryRecord,
    MemorySpace,
    Principal,
    RecallResult,
)

_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_component(value: str) -> str:
    safe = _SAFE_COMPONENT.sub("_", value.strip()).strip("._-")
    if not safe:
        raise ValueError("empty or unsafe Chroma scope component")
    return safe[:120]


class LegacyChromaBackend:
    """Lazy adapter around existing embedded Chroma clients."""

    name = "legacy_chroma"

    def search(
        self,
        *,
        space: MemorySpace,
        principal: Principal,
        query: str,
        embedding: Sequence[float] | None,
        limit: int,
        filters: Mapping[str, object],
    ) -> list[RecallResult]:
        from app.memory import chromadb_manager
        from app.paths import chroma_kb_dir

        query_embedding = list(embedding) if embedding is not None else chromadb_manager.embed(query)
        results: list[RecallResult] = []
        for location in self._locations(space, principal, filters):
            if location.backend != "chroma" or not location.kb_name or not location.collection:
                continue
            client = cast(
                Any,
                chromadb_manager.get_client_for_path(chroma_kb_dir(location.kb_name)),
            )
            try:
                collection = client.get_collection(location.collection)
            except Exception:
                continue
            where = self._chroma_where(filters)
            kwargs: dict[str, object] = {
                "query_embeddings": [query_embedding],
                "n_results": max(1, min(limit, max(1, collection.count()))),
                "include": ["documents", "metadatas", "distances"],
            }
            if where:
                kwargs["where"] = where
            try:
                raw = collection.query(**kwargs)
            except Exception:
                continue
            ids = (raw.get("ids") or [[]])[0]
            documents = (raw.get("documents") or [[]])[0]
            metadatas = (raw.get("metadatas") or [[]])[0]
            distances = (raw.get("distances") or [[]])[0]
            for index, document in enumerate(documents):
                metadata = dict(metadatas[index] or {}) if index < len(metadatas) else {}
                legacy_id = str(ids[index]) if index < len(ids) else f"rank-{index}"
                text = str(document or "")
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                source_record_id = (
                    f"{location.kb_name}/{location.collection}/"
                    f"{metadata.get('source_record_id') or legacy_id}"
                )
                source_uri = str(
                    metadata.get("source_uri")
                    or metadata.get("source")
                    or f"legacy-chroma://{location.kb_name}/{location.collection}/{legacy_id}"
                )
                record = MemoryRecord(
                    memory_id=legacy_id,
                    space=space.key,
                    content=text,
                    source_uri=source_uri,
                    source_record_id=source_record_id,
                    content_sha256=digest,
                    epistemic_class=space.epistemic_class,
                    provenance={
                        "backend": "chroma",
                        "kb_name": location.kb_name,
                        "collection": location.collection,
                        "legacy_id": legacy_id,
                        "source_kind": location.source_kind,
                    },
                    owner_agent_id=self._optional_str(metadata.get("owner_agent_id") or metadata.get("agent")),
                    tenant_id=self._optional_str(metadata.get("tenant_id")),
                    workspace_id=self._optional_str(metadata.get("workspace_id") or metadata.get("project")),
                    confidence=self._optional_float(metadata.get("confidence")),
                    salience=self._optional_float(metadata.get("salience")),
                    significance=self._optional_float(metadata.get("significance")),
                    valence=self._optional_float(metadata.get("valence")),
                    attributes=metadata,
                )
                distance = float(distances[index]) if index < len(distances) else 1.0
                results.append(
                    RecallResult(
                        record=record,
                        score=max(-1.0, min(1.0, 1.0 - distance)),
                        backend=self.name,
                    )
                )
        results.sort(key=lambda item: item.score, reverse=True)
        return results[:limit]

    def put(
        self,
        *,
        space: MemorySpace,
        principal: Principal,
        record: MemoryRecord,
    ) -> MemoryRecord:
        if space.durability is not Durability.OPERATIONAL:
            raise PermissionError(
                f"legacy Chroma writes are restricted to operational memory: {space.key}"
            )
        from app.memory import chromadb_manager
        from app.memory.source_ledger import append_row
        from app.paths import chroma_kb_dir

        location = self._locations(space, principal, record.attributes)[0]
        assert location.kb_name is not None and location.collection is not None
        client = cast(
            Any,
            chromadb_manager.get_client_for_path(chroma_kb_dir(location.kb_name)),
        )
        collection = client.get_or_create_collection(
            location.collection,
            metadata={"hnsw:space": "cosine"},
        )
        vector = list(record.embedding) if record.embedding is not None else chromadb_manager.embed(record.content)
        metadata = self._serializable_metadata(record)
        collection.upsert(
            ids=[record.memory_id],
            documents=[record.content],
            embeddings=[vector],
            metadatas=[metadata],
        )
        append_row(
            location.kb_name,
            location.collection,
            record.memory_id,
            record.content,
            metadata,
        )
        return record

    @staticmethod
    def _locations(
        space: MemorySpace,
        principal: Principal,
        context: Mapping[str, object],
    ) -> tuple[LegacyLocation, ...]:
        if space.key == "operational.agent_private":
            return (
                LegacyLocation(
                    backend="chroma",
                    kb_name="memory",
                    collection=f"scope_agent_{_safe_component(principal.actor_id)}",
                    source_kind="canonical",
                ),
            )
        if space.key == "operational.blackboard":
            task_id = context.get("task_id")
            if not task_id:
                raise ValueError("operational.blackboard requires task_id")
            return (
                LegacyLocation(
                    backend="chroma",
                    kb_name="memory",
                    collection=f"scope_research_bb--{_safe_component(str(task_id))}",
                    source_kind="canonical",
                ),
            )
        chroma = tuple(location for location in space.legacy if location.backend == "chroma")
        if not chroma:
            raise ValueError(f"memory space has no legacy Chroma route: {space.key}")
        return chroma

    @staticmethod
    def _chroma_where(filters: Mapping[str, object]) -> dict[str, object]:
        supported = {
            key: value
            for key, value in filters.items()
            if key in {"tenant_id", "workspace_id", "owner_agent_id", "task_id", "status"}
            and value is not None
        }
        if len(supported) <= 1:
            return supported
        return {"$and": [{key: value} for key, value in supported.items()]}

    @staticmethod
    def _serializable_metadata(record: MemoryRecord) -> dict[str, str | int | float | bool]:
        metadata: dict[str, str | int | float | bool] = {
            "memory_space": record.space,
            "epistemic_class": record.epistemic_class.value,
            "source_uri": record.source_uri,
            "source_record_id": record.source_record_id,
            "content_sha256": record.content_sha256,
            "schema_version": record.schema_version,
            "status": record.status,
            "provenance_json": json.dumps(dict(record.provenance), sort_keys=True),
        }
        optional = {
            "owner_agent_id": record.owner_agent_id,
            "tenant_id": record.tenant_id,
            "workspace_id": record.workspace_id,
            "confidence": record.confidence,
            "salience": record.salience,
            "significance": record.significance,
            "valence": record.valence,
        }
        for key, value in optional.items():
            if value is not None:
                metadata[key] = value
        for key, value in record.attributes.items():
            if key in metadata or value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                metadata[key] = value
            else:
                metadata[f"attr_{key}_json"] = json.dumps(value, sort_keys=True)
        return metadata

    @staticmethod
    def _optional_str(value: object) -> str | None:
        return str(value) if value not in (None, "") else None

    @staticmethod
    def _optional_float(value: object) -> float | None:
        if not isinstance(value, (str, int, float)):
            return None
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None
