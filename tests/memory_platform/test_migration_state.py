from datetime import datetime, timedelta, timezone

import pytest

from app.memory_platform.broker import ReadRoute
from app.memory_platform.migration_state import (
    MigrationPhase,
    MigrationStateError,
    MigrationStateStore,
    ReadinessPolicy,
    ShadowMetrics,
    ndcg_at_10,
    readiness_failures,
)
from app.memory_platform.routing import route_for_phase


def test_state_machine_requires_every_stage(tmp_path) -> None:
    store = MigrationStateStore(tmp_path)
    space = "creative.aesthetics"
    with pytest.raises(MigrationStateError):
        store.transition(space=space, target=MigrationPhase.BACKFILLED, reason="skip")
    state = store.transition(space=space, target=MigrationPhase.SCHEMA_READY, reason="schema")
    assert state.phase is MigrationPhase.SCHEMA_READY
    assert store.load(space).history[-1]["reason"] == "schema"


def test_cutover_and_retirement_require_operator_approval(tmp_path) -> None:
    store = MigrationStateStore(tmp_path)
    state = store.load("creative.aesthetics")
    for phase in (
        MigrationPhase.SCHEMA_READY,
        MigrationPhase.BACKFILLED,
        MigrationPhase.DUAL_WRITE,
        MigrationPhase.SHADOW_READ,
        MigrationPhase.READY,
    ):
        state = store.transition(space=state.space, target=phase, reason="test")
    with pytest.raises(MigrationStateError, match="operator approval"):
        store.transition(space=state.space, target=MigrationPhase.CUTOVER, reason="unsafe")
    state = store.transition(
        space=state.space,
        target=MigrationPhase.CUTOVER,
        reason="approved",
        operator_approval_id="operator-123",
    )
    assert state.operator_approval_id == "operator-123"


def test_record_backfill_requires_exact_parity_and_checkpoint(tmp_path) -> None:
    store = MigrationStateStore(tmp_path)
    space = "creative.aesthetics"
    store.transition(space=space, target=MigrationPhase.SCHEMA_READY, reason="schema")

    with pytest.raises(MigrationStateError, match="parity failed"):
        store.record_backfill(
            space=space,
            expected_records=2,
            migrated_records=1,
            source_checkpoint="snapshot-1",
        )
    with pytest.raises(MigrationStateError, match="source checkpoint"):
        store.record_backfill(
            space=space,
            expected_records=1,
            migrated_records=1,
            source_checkpoint=" ",
        )

    state = store.record_backfill(
        space=space,
        expected_records=1,
        migrated_records=1,
        source_checkpoint="snapshot-1",
    )
    assert state.phase is MigrationPhase.BACKFILLED
    assert state.expected_records == state.migrated_records == 1
    assert state.source_checkpoint == "snapshot-1"


def test_record_backfill_cannot_bypass_schema_ready(tmp_path) -> None:
    store = MigrationStateStore(tmp_path)
    with pytest.raises(MigrationStateError, match="schema_ready"):
        store.record_backfill(
            space="creative.aesthetics",
            expected_records=0,
            migrated_records=0,
            source_checkpoint="empty-snapshot",
        )


def test_ready_metrics_pass_and_any_boundary_violation_blocks() -> None:
    first = datetime.now(timezone.utc) - timedelta(days=8)
    metrics = ShadowMetrics(
        query_count=500,
        ndcg_sum=475.0,
        provenance_complete_count=1000,
        shadow_record_count=1000,
        expected_write_count=100,
        matched_write_count=100,
        first_observed_at=first.isoformat(),
        last_observed_at=datetime.now(timezone.utc).isoformat(),
    )
    assert readiness_failures(metrics, ReadinessPolicy()) == []
    metrics.permission_violations = 1
    assert any("permission_violations" in failure for failure in readiness_failures(metrics, ReadinessPolicy()))


def test_ndcg_handles_identical_and_disjoint_rankings() -> None:
    assert ndcg_at_10(["a", "b"], ["a", "b"]) == pytest.approx(1.0)
    assert ndcg_at_10(["a", "b"], ["x", "y"]) == 0.0


def test_empty_but_observed_space_has_complete_provenance_and_write_parity() -> None:
    metrics = ShadowMetrics(query_count=1)
    assert metrics.provenance_completeness == 1.0
    assert metrics.write_parity == 1.0


def test_routes_are_derived_from_gated_phase() -> None:
    assert route_for_phase(MigrationPhase.DUAL_WRITE) is ReadRoute.LEGACY
    assert route_for_phase(MigrationPhase.SHADOW_READ) is ReadRoute.SHADOW
    assert route_for_phase(MigrationPhase.READY) is ReadRoute.SHADOW
    assert route_for_phase(MigrationPhase.CUTOVER) is ReadRoute.TARGET
    assert route_for_phase(MigrationPhase.ABORTED) is ReadRoute.LEGACY
