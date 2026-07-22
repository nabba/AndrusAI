"""Typed, permission-enforcing broker for all memory access.

The broker is not wired into legacy call sites yet.  It is the compatibility
layer used for backfill and shadow validation before an operator-approved
cutover.  Returning the primary result while observing a shadow backend makes
read migration reversible per memory space.
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import replace
from enum import StrEnum
from typing import Mapping, Protocol, Sequence
from uuid import NAMESPACE_URL, uuid5

from app.memory_platform.models import (
    EMBEDDING_DIMENSION,
    AccessAction,
    ActorRole,
    MemoryRecord,
    MemorySpace,
    Principal,
    RecallResult,
)
from app.memory_platform.registry import get_memory_bridge, get_memory_space

logger = logging.getLogger(__name__)


class MemoryAccessDenied(PermissionError):
    """Raised when a principal crosses a declared memory boundary."""


class MemoryRouteError(RuntimeError):
    """Raised for incomplete or unsafe backend routing."""


class ReadRoute(StrEnum):
    LEGACY = "legacy"
    TARGET = "target"
    SHADOW = "shadow"


class MemoryBackend(Protocol):
    """Storage adapter contract used by the broker."""

    name: str

    def search(
        self,
        *,
        space: MemorySpace,
        principal: Principal,
        query: str,
        embedding: Sequence[float] | None,
        limit: int,
        filters: Mapping[str, object],
    ) -> list[RecallResult]: ...

    def put(
        self,
        *,
        space: MemorySpace,
        principal: Principal,
        record: MemoryRecord,
    ) -> MemoryRecord: ...


class ShadowObserver(Protocol):
    """Receives paired primary/shadow rankings without changing output."""

    def observe(
        self,
        *,
        space: str,
        query: str,
        primary: Sequence[RecallResult],
        shadow: Sequence[RecallResult],
    ) -> None: ...


def validate_embedding(embedding: Sequence[float] | None) -> None:
    """Reject malformed embeddings before they reach either database."""

    if embedding is None:
        return
    if len(embedding) != EMBEDDING_DIMENSION:
        raise ValueError(
            f"embedding must have {EMBEDDING_DIMENSION} dimensions, got {len(embedding)}"
        )
    if not all(math.isfinite(float(value)) for value in embedding):
        raise ValueError("embedding contains a non-finite value")


def stable_memory_id(space: str, source_record_id: str, content: str) -> tuple[str, str]:
    """Return deterministic memory ID and SHA-256 for idempotent migration."""

    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    # Identity follows the stable source record, while the digest versions its
    # content.  Including the digest in the UUID would turn every source update
    # into a new logical memory and prevent deterministic retraction.
    value = f"{space}\x1f{source_record_id}"
    return str(uuid5(NAMESPACE_URL, value)), digest


def new_memory_record(
    *,
    space: str,
    content: str,
    source_uri: str,
    source_record_id: str,
    provenance: Mapping[str, object],
    embedding: Sequence[float] | None = None,
    owner_agent_id: str | None = None,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    attributes: Mapping[str, object] | None = None,
) -> MemoryRecord:
    """Construct a canonical record with stable identity and required labels."""

    spec = get_memory_space(space)
    validate_embedding(embedding)
    memory_id, digest = stable_memory_id(space, source_record_id, content)
    return MemoryRecord(
        memory_id=memory_id,
        space=space,
        content=content,
        source_uri=source_uri,
        source_record_id=source_record_id,
        content_sha256=digest,
        epistemic_class=spec.epistemic_class,
        provenance=dict(provenance),
        embedding=embedding,
        owner_agent_id=owner_agent_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        attributes=dict(attributes or {}),
    )


class MemoryBroker:
    """Authorise, route, and label memory operations."""

    def __init__(
        self,
        *,
        legacy_backend: MemoryBackend,
        target_backend: MemoryBackend,
        routes: Mapping[str, ReadRoute] | None = None,
        shadow_observer: ShadowObserver | None = None,
    ) -> None:
        self._legacy = legacy_backend
        self._target = target_backend
        self._routes = dict(routes or {})
        self._shadow_observer = shadow_observer

    def route_for(self, space: MemorySpace) -> ReadRoute:
        """Return the explicit route, defaulting safely to an existing legacy source."""

        configured = self._routes.get(space.key)
        if configured is not None:
            return ReadRoute(configured)
        if space.legacy:
            return ReadRoute.LEGACY
        return ReadRoute.TARGET

    def set_route(self, space_key: str, route: ReadRoute) -> None:
        """Change an in-memory route; durable cutover state is handled separately."""

        get_memory_space(space_key)
        self._routes[space_key] = ReadRoute(route)

    def recall(
        self,
        *,
        space_key: str,
        principal: Principal,
        query: str,
        embedding: Sequence[float] | None = None,
        limit: int = 5,
        filters: Mapping[str, object] | None = None,
    ) -> list[RecallResult]:
        """Recall from one space after both broker and ownership checks."""

        space = get_memory_space(space_key)
        self._authorize(space, principal, AccessAction.READ)
        validate_embedding(embedding)
        safe_limit = max(1, min(int(limit), 100))
        safe_filters = dict(filters or {})
        if space.tenant_scoped:
            safe_filters["tenant_id"] = principal.tenant_id
        if space.key == "operational.agent_private":
            safe_filters["owner_agent_id"] = principal.actor_id

        route = self.route_for(space)
        if route is ReadRoute.TARGET:
            return self._search(
                self._target, space, principal, query, embedding, safe_limit, safe_filters
            )
        primary = self._search(
            self._legacy, space, principal, query, embedding, safe_limit, safe_filters
        )
        if route is ReadRoute.SHADOW:
            try:
                shadow = self._search(
                    self._target, space, principal, query, embedding, safe_limit, safe_filters
                )
                if self._shadow_observer is not None:
                    self._shadow_observer.observe(
                        space=space.key,
                        query=query,
                        primary=primary,
                        shadow=shadow,
                    )
            except Exception:
                # A pre-cutover shadow is observational.  It must never take
                # down the authoritative legacy recall path.
                logger.warning("memory shadow read failed for %s", space.key, exc_info=True)
        return primary

    def recall_bridge(
        self,
        *,
        bridge_key: str,
        principal: Principal,
        query: str,
        embedding: Sequence[float] | None = None,
        limit_per_space: int = 3,
        total_limit: int = 10,
    ) -> list[RecallResult]:
        """Use an explicit cross-space bridge while preserving every source label."""

        bridge = get_memory_bridge(bridge_key)
        if principal.role not in bridge.readers:
            raise MemoryAccessDenied(
                f"role {principal.role} may not use memory bridge {bridge_key}"
            )
        combined: list[RecallResult] = []
        for space_key in bridge.spaces:
            results = self.recall(
                space_key=space_key,
                principal=principal,
                query=query,
                embedding=embedding,
                limit=limit_per_space,
            )
            combined.extend(replace(result, bridge=bridge_key) for result in results)
        combined.sort(key=lambda item: item.score, reverse=True)
        return combined[: max(1, min(int(total_limit), 100))]

    def remember(
        self,
        *,
        principal: Principal,
        record: MemoryRecord,
    ) -> MemoryRecord:
        """Write to the target canonical store after invariant checks."""

        space = get_memory_space(record.space)
        self._authorize(space, principal, AccessAction.WRITE)
        if record.epistemic_class is not space.epistemic_class:
            raise ValueError(
                f"record epistemic class {record.epistemic_class} does not match {space.epistemic_class}"
            )
        if record.tenant_id != principal.tenant_id and space.tenant_scoped:
            raise MemoryAccessDenied("record tenant does not match principal tenant")
        if (
            space.key == "operational.agent_private"
            and record.owner_agent_id != principal.actor_id
        ):
            raise MemoryAccessDenied("agent-private memory owner mismatch")
        validate_embedding(record.embedding)
        return self._target.put(space=space, principal=principal, record=record)

    @staticmethod
    def _authorize(
        space: MemorySpace,
        principal: Principal,
        action: AccessAction,
    ) -> None:
        if not space.permits(principal, action):
            raise MemoryAccessDenied(
                f"role {principal.role} may not {action} memory space {space.key}"
            )

    @staticmethod
    def _search(
        backend: MemoryBackend,
        space: MemorySpace,
        principal: Principal,
        query: str,
        embedding: Sequence[float] | None,
        limit: int,
        filters: Mapping[str, object],
    ) -> list[RecallResult]:
        results = backend.search(
            space=space,
            principal=principal,
            query=query,
            embedding=embedding,
            limit=limit,
            filters=filters,
        )
        for result in results:
            if result.record.space != space.key:
                raise MemoryRouteError(
                    f"backend {backend.name} returned {result.record.space} for {space.key}"
                )
            if result.record.epistemic_class is not space.epistemic_class:
                raise MemoryRouteError(
                    f"backend {backend.name} changed epistemic label for {space.key}"
                )
            if not result.record.source_uri or not result.record.provenance:
                raise MemoryRouteError(
                    f"backend {backend.name} returned a record without provenance"
                )
            if space.tenant_scoped and result.record.tenant_id != principal.tenant_id:
                raise MemoryAccessDenied("backend returned a record from another tenant")
            if (
                space.key == "operational.agent_private"
                and result.record.owner_agent_id != principal.actor_id
            ):
                raise MemoryAccessDenied("backend returned another agent's private memory")
        return results


class InMemoryBackend:
    """Deterministic backend for tests, dry-runs, and broker demonstrations."""

    def __init__(self, name: str = "memory") -> None:
        self.name = name
        self.records: dict[str, dict[str, MemoryRecord]] = {}

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
        query_tokens = set(query.casefold().split())
        rows: list[RecallResult] = []
        for record in self.records.get(space.key, {}).values():
            if any(getattr(record, key, None) != value for key, value in filters.items()):
                continue
            content_tokens = set(record.content.casefold().split())
            score = len(query_tokens & content_tokens) / max(1, len(query_tokens))
            rows.append(RecallResult(record=record, score=score, backend=self.name))
        rows.sort(key=lambda item: (-item.score, item.record.memory_id))
        return rows[:limit]

    def put(
        self,
        *,
        space: MemorySpace,
        principal: Principal,
        record: MemoryRecord,
    ) -> MemoryRecord:
        self.records.setdefault(space.key, {})[record.memory_id] = record
        return record
