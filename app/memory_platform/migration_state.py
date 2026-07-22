"""Per-space migration state, quality gates, and shadow-read telemetry."""

from __future__ import annotations

import json
import math
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Sequence

from app.memory_platform.models import RecallResult
from app.memory_platform.registry import get_memory_space


class MigrationPhase(StrEnum):
    DISCOVERED = "discovered"
    SCHEMA_READY = "schema_ready"
    BACKFILLED = "backfilled"
    DUAL_WRITE = "dual_write"
    SHADOW_READ = "shadow_read"
    READY = "ready"
    CUTOVER = "cutover"
    SOAK = "soak"
    RETIRED = "retired"
    ABORTED = "aborted"


_ALLOWED_TRANSITIONS: dict[MigrationPhase, frozenset[MigrationPhase]] = {
    MigrationPhase.DISCOVERED: frozenset({MigrationPhase.SCHEMA_READY, MigrationPhase.ABORTED}),
    MigrationPhase.SCHEMA_READY: frozenset({MigrationPhase.BACKFILLED, MigrationPhase.ABORTED}),
    MigrationPhase.BACKFILLED: frozenset({MigrationPhase.DUAL_WRITE, MigrationPhase.ABORTED}),
    MigrationPhase.DUAL_WRITE: frozenset({MigrationPhase.SHADOW_READ, MigrationPhase.ABORTED}),
    MigrationPhase.SHADOW_READ: frozenset(
        {MigrationPhase.READY, MigrationPhase.DUAL_WRITE, MigrationPhase.ABORTED}
    ),
    MigrationPhase.READY: frozenset(
        {MigrationPhase.CUTOVER, MigrationPhase.SHADOW_READ, MigrationPhase.ABORTED}
    ),
    MigrationPhase.CUTOVER: frozenset(
        {MigrationPhase.SOAK, MigrationPhase.READY, MigrationPhase.ABORTED}
    ),
    MigrationPhase.SOAK: frozenset(
        {MigrationPhase.RETIRED, MigrationPhase.CUTOVER, MigrationPhase.ABORTED}
    ),
    MigrationPhase.RETIRED: frozenset(),
    MigrationPhase.ABORTED: frozenset({MigrationPhase.DISCOVERED}),
}


class MigrationStateError(RuntimeError):
    """A state transition bypassed a required phase or approval gate."""


@dataclass(slots=True)
class ShadowMetrics:
    query_count: int = 0
    ndcg_sum: float = 0.0
    provenance_complete_count: int = 0
    permission_violations: int = 0
    primary_record_count: int = 0
    shadow_record_count: int = 0
    expected_write_count: int = 0
    matched_write_count: int = 0
    unresolved_outbox: int = 0
    outbox_lag_seconds: float = 0.0
    first_observed_at: str | None = None
    last_observed_at: str | None = None

    @property
    def mean_ndcg_at_10(self) -> float:
        return self.ndcg_sum / self.query_count if self.query_count else 0.0

    @property
    def provenance_completeness(self) -> float:
        if self.shadow_record_count:
            return self.provenance_complete_count / self.shadow_record_count
        return 1.0 if self.query_count else 0.0

    @property
    def write_parity(self) -> float:
        return (
            self.matched_write_count / self.expected_write_count
            if self.expected_write_count
            else 1.0
        )

    @property
    def observation_days(self) -> float:
        if not self.first_observed_at or not self.last_observed_at:
            return 0.0
        first = datetime.fromisoformat(self.first_observed_at)
        last = datetime.fromisoformat(self.last_observed_at)
        return max(0.0, (last - first).total_seconds() / 86_400.0)


@dataclass(slots=True)
class ReadinessPolicy:
    min_shadow_queries: int = 500
    min_observation_days: float = 7.0
    min_mean_ndcg_at_10: float = 0.90
    min_provenance_completeness: float = 1.0
    min_write_parity: float = 1.0
    max_outbox_lag_seconds: float = 300.0


@dataclass(slots=True)
class MemorySpaceMigration:
    space: str
    phase: MigrationPhase = MigrationPhase.DISCOVERED
    source_checkpoint: str | None = None
    expected_records: int | None = None
    migrated_records: int = 0
    metrics: ShadowMetrics = field(default_factory=ShadowMetrics)
    operator_approval_id: str | None = None
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    history: list[dict[str, str]] = field(default_factory=list)


class MigrationStateStore:
    """Atomic JSON persistence for operator-visible per-space state."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._lock = threading.RLock()

    def load(self, space: str) -> MemorySpaceMigration:
        get_memory_space(space)
        path = self._path(space)
        if not path.exists():
            return MemorySpaceMigration(space=space)
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["phase"] = MigrationPhase(raw["phase"])
        raw["metrics"] = ShadowMetrics(**raw.get("metrics", {}))
        return MemorySpaceMigration(**raw)

    def save(self, state: MemorySpaceMigration) -> None:
        get_memory_space(state.space)
        with self._lock:
            self._root.mkdir(parents=True, exist_ok=True)
            path = self._path(state.space)
            temp = path.with_suffix(".json.tmp")
            payload = asdict(state)
            payload["phase"] = state.phase.value
            temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            temp.replace(path)

    def transition(
        self,
        *,
        space: str,
        target: MigrationPhase,
        reason: str,
        operator_approval_id: str | None = None,
    ) -> MemorySpaceMigration:
        """Advance one state only; CUTOVER and RETIRED require approval IDs."""

        with self._lock:
            state = self.load(space)
            target = MigrationPhase(target)
            if target not in _ALLOWED_TRANSITIONS[state.phase]:
                raise MigrationStateError(
                    f"illegal migration transition for {space}: {state.phase} -> {target}"
                )
            if target in {MigrationPhase.CUTOVER, MigrationPhase.RETIRED} and not operator_approval_id:
                raise MigrationStateError(f"{target} requires an operator approval id")
            now = datetime.now(timezone.utc).isoformat()
            state.history.append(
                {
                    "from": state.phase.value,
                    "to": target.value,
                    "reason": reason,
                    "at": now,
                    "operator_approval_id": operator_approval_id or "",
                }
            )
            state.phase = target
            state.updated_at = now
            if operator_approval_id:
                state.operator_approval_id = operator_approval_id
            self.save(state)
            return state

    def record_backfill(
        self,
        *,
        space: str,
        expected_records: int,
        migrated_records: int,
        source_checkpoint: str,
    ) -> MemorySpaceMigration:
        """Record a verified snapshot and enter BACKFILLED only at exact parity."""

        if expected_records < 0 or migrated_records < 0:
            raise MigrationStateError("backfill record counts cannot be negative")
        if expected_records != migrated_records:
            raise MigrationStateError(
                f"backfill parity failed for {space}: "
                f"expected {expected_records}, migrated {migrated_records}"
            )
        if not source_checkpoint.strip():
            raise MigrationStateError("backfill requires a source checkpoint")

        with self._lock:
            state = self.load(space)
            if state.phase is not MigrationPhase.SCHEMA_READY:
                raise MigrationStateError(
                    f"backfill can only be recorded from schema_ready, found {state.phase}"
                )
            state.expected_records = expected_records
            state.migrated_records = migrated_records
            state.source_checkpoint = source_checkpoint
            self.save(state)
            return self.transition(
                space=space,
                target=MigrationPhase.BACKFILLED,
                reason=(
                    "verified backfill parity: "
                    f"{migrated_records}/{expected_records} records at {source_checkpoint}"
                ),
            )

    def _path(self, space: str) -> Path:
        return self._root / f"{space.replace('.', '__')}.json"


def ndcg_at_10(primary_ids: Sequence[str], shadow_ids: Sequence[str]) -> float:
    """Binary-relevance NDCG@10 using the legacy ranking as the reference."""

    relevant = set(primary_ids[:10])
    if not relevant:
        return 1.0 if not shadow_ids else 0.0
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, memory_id in enumerate(shadow_ids[:10], start=1)
        if memory_id in relevant
    )
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, len(relevant) + 1))
    return dcg / ideal if ideal else 1.0


def readiness_failures(metrics: ShadowMetrics, policy: ReadinessPolicy) -> list[str]:
    """Return every unsatisfied cutover gate instead of only the first."""

    failures: list[str] = []
    if metrics.query_count < policy.min_shadow_queries:
        failures.append(f"shadow_queries {metrics.query_count} < {policy.min_shadow_queries}")
    if metrics.observation_days < policy.min_observation_days:
        failures.append(
            f"observation_days {metrics.observation_days:.2f} < {policy.min_observation_days:.2f}"
        )
    if metrics.mean_ndcg_at_10 < policy.min_mean_ndcg_at_10:
        failures.append(
            f"mean_ndcg_at_10 {metrics.mean_ndcg_at_10:.4f} < {policy.min_mean_ndcg_at_10:.4f}"
        )
    if metrics.provenance_completeness < policy.min_provenance_completeness:
        failures.append(
            "provenance_completeness "
            f"{metrics.provenance_completeness:.4f} < {policy.min_provenance_completeness:.4f}"
        )
    if metrics.permission_violations:
        failures.append(f"permission_violations {metrics.permission_violations} != 0")
    if metrics.write_parity < policy.min_write_parity:
        failures.append(f"write_parity {metrics.write_parity:.4f} < {policy.min_write_parity:.4f}")
    if metrics.unresolved_outbox:
        failures.append(f"unresolved_outbox {metrics.unresolved_outbox} != 0")
    if metrics.outbox_lag_seconds > policy.max_outbox_lag_seconds:
        failures.append(
            f"outbox_lag_seconds {metrics.outbox_lag_seconds:.2f} > {policy.max_outbox_lag_seconds:.2f}"
        )
    return failures


class RecordingShadowObserver:
    """Persist paired-ranking telemetry for the broker's SHADOW route."""

    def __init__(self, store: MigrationStateStore) -> None:
        self._store = store
        self._lock = threading.Lock()

    def observe(
        self,
        *,
        space: str,
        query: str,
        primary: Sequence[RecallResult],
        shadow: Sequence[RecallResult],
    ) -> None:
        del query  # telemetry stores rankings, not sensitive query text
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            state = self._store.load(space)
            metrics = state.metrics
            metrics.query_count += 1
            metrics.ndcg_sum += ndcg_at_10(
                [item.record.source_record_id for item in primary],
                [item.record.source_record_id for item in shadow],
            )
            metrics.primary_record_count += len(primary)
            metrics.shadow_record_count += len(shadow)
            metrics.provenance_complete_count += sum(
                bool(item.record.source_uri and item.record.provenance) for item in shadow
            )
            metrics.first_observed_at = metrics.first_observed_at or now
            metrics.last_observed_at = now
            state.updated_at = now
            self._store.save(state)
