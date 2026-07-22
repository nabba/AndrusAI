from dataclasses import replace

import pytest

from app.memory_platform.broker import (
    InMemoryBackend,
    MemoryAccessDenied,
    MemoryBroker,
    MemoryRouteError,
    ReadRoute,
    new_memory_record,
)
from app.memory_platform.models import ActorRole, Principal, RecallResult
from app.memory_platform.registry import get_memory_space


def principal(role: ActorRole, actor: str = "agent", tenant: str | None = None) -> Principal:
    return Principal(role=role, actor_id=actor, tenant_id=tenant)


def record(space: str, text: str = "memory about graceful systems", **kwargs):
    return new_memory_record(
        space=space,
        content=text,
        source_uri="source://test",
        source_record_id=f"source-{space}-{text}",
        provenance={"test": True},
        **kwargs,
    )


def seed(backend: InMemoryBackend, item, role: ActorRole = ActorRole.RECONCILER) -> None:
    backend.put(
        space=get_memory_space(item.space),
        principal=principal(role),
        record=item,
    )


def test_existing_spaces_default_to_legacy_route() -> None:
    legacy = InMemoryBackend("legacy")
    target = InMemoryBackend("target")
    seed(legacy, record("knowledge.episteme"))
    broker = MemoryBroker(legacy_backend=legacy, target_backend=target)
    results = broker.recall(
        space_key="knowledge.episteme",
        principal=principal(ActorRole.RESEARCHER),
        query="graceful systems",
    )
    assert [result.backend for result in results] == ["legacy"]


def test_per_space_target_cutover_is_independent() -> None:
    legacy = InMemoryBackend("legacy")
    target = InMemoryBackend("target")
    seed(target, record("creative.aesthetics"))
    broker = MemoryBroker(
        legacy_backend=legacy,
        target_backend=target,
        routes={"creative.aesthetics": ReadRoute.TARGET},
    )
    results = broker.recall(
        space_key="creative.aesthetics",
        principal=principal(ActorRole.WRITER),
        query="graceful systems",
    )
    assert [result.backend for result in results] == ["target"]
    assert broker.route_for(get_memory_space("knowledge.episteme")) is ReadRoute.LEGACY


def test_researcher_cannot_read_fiction() -> None:
    broker = MemoryBroker(
        legacy_backend=InMemoryBackend("legacy"),
        target_backend=InMemoryBackend("target"),
    )
    with pytest.raises(MemoryAccessDenied):
        broker.recall(
            space_key="creative.fiction",
            principal=principal(ActorRole.RESEARCHER),
            query="invent",
        )


def test_ordinary_self_reflection_cannot_read_full_episodes() -> None:
    broker = MemoryBroker(
        legacy_backend=InMemoryBackend("legacy"),
        target_backend=InMemoryBackend("target"),
    )
    with pytest.raises(MemoryAccessDenied):
        broker.recall(
            space_key="autobiographical.episodic_full",
            principal=principal(ActorRole.SELF_REFLECTION),
            query="past",
        )


def test_tenant_context_is_required_and_enforced() -> None:
    target = InMemoryBackend("target")
    tenant_record = record("tenant.documents", tenant_id="tenant-a")
    seed(target, tenant_record)
    broker = MemoryBroker(
        legacy_backend=InMemoryBackend("legacy"),
        target_backend=target,
        routes={"tenant.documents": ReadRoute.TARGET},
    )
    with pytest.raises(MemoryAccessDenied):
        broker.recall(
            space_key="tenant.documents",
            principal=principal(ActorRole.RESEARCHER),
            query="systems",
        )
    assert broker.recall(
        space_key="tenant.documents",
        principal=principal(ActorRole.RESEARCHER, tenant="tenant-a"),
        query="systems",
    )
    assert not broker.recall(
        space_key="tenant.documents",
        principal=principal(ActorRole.RESEARCHER, tenant="tenant-b"),
        query="systems",
    )


def test_bridge_results_retain_source_and_bridge_labels() -> None:
    target = InMemoryBackend("target")
    seed(target, record("knowledge.episteme", "systems evidence"))
    seed(target, record("creative.fiction", "systems imagined"))
    seed(target, record("creative.aesthetics", "systems elegant"))
    seed(target, record("creative.tensions", "systems contradictory"))
    seed(target, record("knowledge.philosophy", "systems theoretical"))
    broker = MemoryBroker(
        legacy_backend=InMemoryBackend("legacy"),
        target_backend=target,
        routes={space: ReadRoute.TARGET for space in (
            "knowledge.episteme", "knowledge.philosophy", "creative.fiction",
            "creative.aesthetics", "creative.tensions",
        )},
    )
    results = broker.recall_bridge(
        bridge_key="creative_blend",
        principal=principal(ActorRole.WRITER),
        query="systems",
    )
    assert results
    assert all(result.bridge == "creative_blend" for result in results)
    assert {result.record.space for result in results} >= {"creative.fiction", "knowledge.episteme"}
    assert all(result.record.source_uri and result.record.provenance for result in results)


def test_backend_cannot_relabel_a_record() -> None:
    class BadBackend(InMemoryBackend):
        def search(self, **kwargs):
            good = record("knowledge.episteme")
            bad = replace(good, space="creative.fiction")
            return [RecallResult(record=bad, score=1.0, backend=self.name)]

    broker = MemoryBroker(
        legacy_backend=BadBackend("bad"),
        target_backend=InMemoryBackend("target"),
    )
    with pytest.raises(MemoryRouteError):
        broker.recall(
            space_key="knowledge.episteme",
            principal=principal(ActorRole.RESEARCHER),
            query="systems",
        )


def test_remember_rejects_wrong_epistemic_label() -> None:
    broker = MemoryBroker(
        legacy_backend=InMemoryBackend("legacy"),
        target_backend=InMemoryBackend("target"),
    )
    item = record("creative.fiction")
    item = replace(item, epistemic_class=get_memory_space("knowledge.episteme").epistemic_class)
    with pytest.raises(ValueError):
        broker.remember(principal=principal(ActorRole.RECONCILER), record=item)


def test_shadow_failure_does_not_break_authoritative_legacy_read() -> None:
    class FailedTarget(InMemoryBackend):
        def search(self, **kwargs):
            raise RuntimeError("target unavailable")

    legacy = InMemoryBackend("legacy")
    seed(legacy, record("knowledge.episteme"))
    broker = MemoryBroker(
        legacy_backend=legacy,
        target_backend=FailedTarget("failed-target"),
        routes={"knowledge.episteme": ReadRoute.SHADOW},
    )
    results = broker.recall(
        space_key="knowledge.episteme",
        principal=principal(ActorRole.RESEARCHER),
        query="graceful systems",
    )
    assert [result.backend for result in results] == ["legacy"]


def test_cutover_does_not_fall_back_to_stale_legacy_on_target_failure() -> None:
    class FailedTarget(InMemoryBackend):
        def search(self, **kwargs):
            raise RuntimeError("target unavailable")

    broker = MemoryBroker(
        legacy_backend=InMemoryBackend("legacy"),
        target_backend=FailedTarget("failed-target"),
        routes={"knowledge.episteme": ReadRoute.TARGET},
    )
    with pytest.raises(RuntimeError, match="target unavailable"):
        broker.recall(
            space_key="knowledge.episteme",
            principal=principal(ActorRole.RESEARCHER),
            query="systems",
        )
