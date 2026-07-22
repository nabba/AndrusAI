"""Derive reversible broker routes from operator-visible migration state."""

from __future__ import annotations

from app.memory_platform.broker import ReadRoute
from app.memory_platform.migration_state import MigrationPhase, MigrationStateStore
from app.memory_platform.registry import MEMORY_SPACES

_PHASE_ROUTES: dict[MigrationPhase, ReadRoute] = {
    MigrationPhase.DISCOVERED: ReadRoute.LEGACY,
    MigrationPhase.SCHEMA_READY: ReadRoute.LEGACY,
    MigrationPhase.BACKFILLED: ReadRoute.LEGACY,
    MigrationPhase.DUAL_WRITE: ReadRoute.LEGACY,
    MigrationPhase.SHADOW_READ: ReadRoute.SHADOW,
    MigrationPhase.READY: ReadRoute.SHADOW,
    MigrationPhase.CUTOVER: ReadRoute.TARGET,
    MigrationPhase.SOAK: ReadRoute.TARGET,
    MigrationPhase.RETIRED: ReadRoute.TARGET,
    MigrationPhase.ABORTED: ReadRoute.LEGACY,
}


def route_for_phase(phase: MigrationPhase) -> ReadRoute:
    """Map a gated state to the only safe active read route."""

    return _PHASE_ROUTES[MigrationPhase(phase)]


def load_routes(store: MigrationStateStore) -> dict[str, ReadRoute]:
    """Load all per-space routes; missing state files remain legacy-safe."""

    return {
        key: route_for_phase(store.load(key).phase)
        for key, space in MEMORY_SPACES.items()
        if space.legacy
    }
