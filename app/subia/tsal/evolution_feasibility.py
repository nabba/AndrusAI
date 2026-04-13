"""Evolution feasibility checker — Self-Improver hard-gate (TSAL §8).

Pure functions. Given a proposal + a TechnicalSelfModel, return a
FeasibilityReport whose `passes` flag tells the Self-Improver whether
the change can actually be implemented on the current host.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .self_model import TechnicalSelfModel


@dataclass
class EvolutionProposal:
    description: str
    modules_affected: list = field(default_factory=list)
    estimated_ram_impact_gb: float = 0.0
    estimated_disk_impact_gb: float = 0.0
    requires_new_dependency: bool = False
    requires_model_change: bool = False
    estimated_implementation_tokens: int = 0


@dataclass
class FeasibilityCheck:
    name: str
    passed: bool
    detail: str


@dataclass
class FeasibilityReport:
    proposal_description: str
    checks: list = field(default_factory=list)
    downstream_modules: list = field(default_factory=list)

    @property
    def passes(self) -> bool:
        return all(c.passed for c in self.checks)


def check_evolution_feasibility(
    proposal: EvolutionProposal,
    model: TechnicalSelfModel,
    *,
    recent_change_set: Optional[set] = None,
    max_downstream: int = 10,
) -> FeasibilityReport:
    rep = FeasibilityReport(proposal_description=proposal.description)
    res = model.resources
    cb = model.codebase

    # RAM
    available = res.available_for_inference_gb
    rep.checks.append(FeasibilityCheck(
        name="ram",
        passed=proposal.estimated_ram_impact_gb < max(0.1, available * 0.5),
        detail=f"need {proposal.estimated_ram_impact_gb}GB; available {available}GB",
    ))
    # Disk
    rep.checks.append(FeasibilityCheck(
        name="disk",
        passed=proposal.estimated_disk_impact_gb < max(0.1, res.disk_available_gb * 0.1),
        detail=f"need {proposal.estimated_disk_impact_gb}GB; free {res.disk_available_gb}GB",
    ))
    # Compute headroom
    rep.checks.append(FeasibilityCheck(
        name="compute_headroom",
        passed=res.compute_pressure < 0.6,
        detail=f"compute_pressure={res.compute_pressure}",
    ))
    # Module stability (no overlap with recent changes)
    affected = set(proposal.modules_affected)
    overlap = affected & (recent_change_set or set())
    rep.checks.append(FeasibilityCheck(
        name="module_stability",
        passed=not overlap,
        detail=f"recently-changed modules in scope: {sorted(overlap)}",
    ))
    # Downstream blast radius
    downstream: set = set()
    for affected_mod in proposal.modules_affected:
        for dep_name, imports in cb.dependencies.items():
            if affected_mod in imports:
                downstream.add(dep_name)
    rep.downstream_modules = sorted(downstream)
    rep.checks.append(FeasibilityCheck(
        name="downstream_impact",
        passed=len(downstream) < max_downstream,
        detail=f"{len(downstream)} downstream module(s) would be affected",
    ))
    # Local-inference reality check if model change requested
    if proposal.requires_model_change:
        max_b = model.host.max_local_model_params_b
        rep.checks.append(FeasibilityCheck(
            name="model_size",
            passed=max_b >= 7.0,
            detail=f"host max local model ~{max_b}B parameters",
        ))
    return rep
