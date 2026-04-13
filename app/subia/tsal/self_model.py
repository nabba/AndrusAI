"""TechnicalSelfModel — aggregate of all TSAL discoveries.

Single dataclass passed to generators, the SubIA bridge, and the
evolution-feasibility checker. Instances are produced by `assemble()`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .probers import HostProfile, ResourceState
from .inspectors import CodebaseProfile, ComponentInventory


@dataclass
class TechnicalSelfModel:
    host: HostProfile = field(default_factory=HostProfile)
    resources: ResourceState = field(default_factory=ResourceState)
    codebase: CodebaseProfile = field(default_factory=CodebaseProfile)
    components: ComponentInventory = field(default_factory=ComponentInventory)
    operating_principles: str = ""
    assembled_at: str = ""

    @classmethod
    def assemble(
        cls,
        *,
        host: Optional[HostProfile] = None,
        resources: Optional[ResourceState] = None,
        codebase: Optional[CodebaseProfile] = None,
        components: Optional[ComponentInventory] = None,
        operating_principles: str = "",
    ) -> "TechnicalSelfModel":
        m = cls(
            host=host or HostProfile(),
            resources=resources or ResourceState(),
            codebase=codebase or CodebaseProfile(),
            components=components or ComponentInventory(),
            operating_principles=operating_principles,
        )
        m.assembled_at = datetime.now(timezone.utc).isoformat()
        return m

    def is_complete(self) -> bool:
        """All four discovery engines successfully ran at least once."""
        return all((
            self.host.probed_at,
            self.resources.probed_at,
            self.codebase.analyzed_at,
            self.components.discovered_at,
        ))
