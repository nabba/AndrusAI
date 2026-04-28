"""Types for the Transfer Insight Layer.

TransferEvent — write-time pointer captured at hook points (healing,
evolution, grounding correction, gap resolution). Kept deliberately
small; the compiler rehydrates source data before invoking the Learner.

TransferKind — enumeration of source events. Selects the Learner prompt
template in ``app.transfer_memory.prompts``.

TransferScope — promotion ladder:
    shadow            — audit-only, never injected (Phase 17a default)
    project_local     — only retrievable when active project matches
    same_domain_only  — only retrievable when source_domain matches target
    global_meta       — retrievable across all domains/projects

NegativeTransferTag — post-hoc classification of failures involving
injected transfer insights. Set by ``app.transfer_memory.attribution``
after a trajectory closes with verdict=FAILURE and an injected insight
was implicated.

IMMUTABLE — infrastructure-level types.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TransferKind(str, Enum):
    """Source event kinds the compiler can ingest."""
    HEALING = "healing"
    EVO_SUCCESS = "evo_success"
    EVO_FAILURE = "evo_failure"
    GROUNDING_CORRECTION = "grounding_correction"
    GAP_RESOLVED = "gap_resolved"


class TransferScope(str, Enum):
    """Promotion ladder for a compiled insight.

    Records start at SHADOW. Promotion through the ladder is gated by
    sanitiser pass + measured effectiveness. Demotion follows
    negative-transfer attribution.
    """
    SHADOW = "shadow"
    PROJECT_LOCAL = "project_local"
    SAME_DOMAIN_ONLY = "same_domain_only"
    GLOBAL_META = "global_meta"


class NegativeTransferTag(str, Enum):
    """Post-hoc failure classifications for retrieved transfer insights."""
    DOMAIN_MISMATCHED_ANCHOR = "domain_mismatched_anchor"
    FALSE_VALIDATION_CONFIDENCE = "false_validation_confidence"
    MISAPPLIED_BEST_PRACTICE = "misapplied_best_practice"
    PROJECT_SCOPE_LEAKAGE = "project_scope_leakage"
    SAFETY_BOUNDARY_CONFLICT = "safety_boundary_conflict"
    OVER_ABSTRACTION = "over_abstraction"


_DOMAIN_OF_KIND: dict[TransferKind, str] = {
    TransferKind.HEALING: "healing",
    TransferKind.EVO_SUCCESS: "evolution",
    TransferKind.EVO_FAILURE: "evolution",
    TransferKind.GROUNDING_CORRECTION: "grounding",
    TransferKind.GAP_RESOLVED: "ops",
}


def domain_for_kind(kind: TransferKind) -> str:
    """Default source_domain for a TransferKind. The compiler may override
    based on payload contents (e.g. an evo change_type that touches code
    becomes domain="coding"); this is the heuristic baseline."""
    return _DOMAIN_OF_KIND.get(kind, "ops")


@dataclass
class TransferEvent:
    """A write-time pointer queued for nightly compilation.

    Fields are kept small — the compiler can fetch the full source record
    from the originating store using ``source_id`` plus ``payload``. The
    triggers serialise these to JSONL synchronously, so anything stored
    here pays the disk-I/O cost on the write path.
    """
    event_id: str                                       # deterministic hash
    kind: TransferKind
    source_id: str                                      # key in source store
    summary: str = ""                                   # short blurb for browsing
    project_origin: str = ""                            # "" = project-agnostic
    payload: dict = field(default_factory=dict)
    captured_at: str = field(default_factory=_now_iso)
    attempts: int = 0                                   # retry counter

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value if isinstance(self.kind, TransferKind) else self.kind
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TransferEvent":
        d = dict(d)
        if isinstance(d.get("kind"), str):
            try:
                d["kind"] = TransferKind(d["kind"])
            except ValueError:
                # Unknown kind — let the caller skip this event.
                raise
        valid = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


# Public enumerations for dashboards / tests / external schemas.
TRANSFER_SCOPES_ALL = tuple(s.value for s in TransferScope)
TRANSFER_KINDS_ALL = tuple(k.value for k in TransferKind)
NEGATIVE_TRANSFER_TAGS_ALL = tuple(t.value for t in NegativeTransferTag)
