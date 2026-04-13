"""
The SubjectivityKernel — the single persistent runtime state.

Seven components, one dataclass. Atomic by design: the kernel is
always in a consistent state, serialized to wiki/self/kernel-state.md
after each CIL loop and loaded on startup.

This module contains ONLY the data model. Behavior — salience scoring,
homeostatic computation, prediction, consolidation — lives in the
sibling subpackages (scene/, homeostasis/, prediction/, memory/).

References:
  - SubIA Part I §3 (data model)
  - SubIA Part II §19 (wiki page formats)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


# ── Scene ────────────────────────────────────────────────────────────

@dataclass
class SceneItem:
    """A single item in the current scene.

    Tiers: 'focal' receives full CIL processing (affect, prediction,
    ownership binding); 'peripheral' receives metadata-only listing.
    """
    id: str
    source: str                  # 'wiki' | 'mem0' | 'firecrawl' | 'agent' | 'internal' | 'memory'
    content_ref: str             # Path or identifier to source content
    summary: str                 # One-line human-readable summary
    salience: float              # 0.0–1.0 composite score
    entered_at: str              # ISO 8601 UTC
    ownership: str = "self"      # 'self' | 'external' | 'shared'
    valence: float = 0.0         # -1.0 to +1.0 (homeostatic approach/avoid)
    dominant_affect: str = "neutral"
    conflicts_with: list = field(default_factory=list)
    action_options: list = field(default_factory=list)
    tier: str = "focal"          # 'focal' | 'peripheral'
    # Phase 12 — Boundary Sense (Proposal 5): phenomenological origin.
    # Optional and defaulted None for backward-compat; populated by
    # `app.subia.boundary.classifier.classify_scene_item()` at scene
    # admission. Downstream consumers (consolidator, homeostasis, value
    # resonance) treat None as "unclassified" and route conservatively.
    processing_mode: Optional[str] = None
    # Phase 12 — Wonder Register (Proposal 4): per-item depth signal.
    # Set by understanding+wonder pipeline; freezes salience decay when
    # > WONDER_FREEZE_THRESHOLD.
    wonder_intensity: float = 0.0


# ── Self-state ───────────────────────────────────────────────────────

@dataclass
class Commitment:
    """An active commitment the system owns."""
    id: str
    description: str
    venture: str                 # 'plg' | 'archibal' | 'kaicart' | 'meta' | 'self'
    created_at: str
    deadline: Optional[str] = None
    status: str = "active"       # 'active' | 'fulfilled' | 'broken' | 'deferred'
    related_wiki_pages: list = field(default_factory=list)
    homeostatic_impact: dict = field(default_factory=dict)


@dataclass
class SelfState:
    """The persistent subject token — what 'the system' refers to."""
    identity: dict = field(default_factory=lambda: {
        "name": "AndrusAI",
        "architecture": "crewai-team multi-agent system",
        "continuity_marker": None,   # Hash of previous state for chain verification
    })
    active_commitments: list = field(default_factory=list)
    capabilities: dict = field(default_factory=dict)
    limitations: dict = field(default_factory=dict)
    current_goals: list = field(default_factory=list)
    social_roles: dict = field(default_factory=lambda: {
        "andrus": "strategic partner and principal",
    })
    autobiographical_pointers: list = field(default_factory=list)
    agency_log: list = field(default_factory=list)
    # Phase 12 — Shadow Self (Proposal 3) writes here. Distinct from
    # `limitations` so the discovered-via-behavior layer is structurally
    # separated from declared/known limitations. Append-only at the
    # bridge level (see app.subia.connections.six_proposals_bridges).
    discovered_limitations: list = field(default_factory=list)


# ── Homeostasis ──────────────────────────────────────────────────────

@dataclass
class HomeostaticState:
    """Digital interoceptive state with PDS-derived set-points."""
    variables: dict = field(default_factory=dict)          # var → 0.0–1.0
    set_points: dict = field(default_factory=dict)         # var → 0.0–1.0 (immutable to agents)
    deviations: dict = field(default_factory=dict)         # var → signed deviation
    restoration_queue: list = field(default_factory=list)  # vars ordered by |deviation|
    last_updated: str = ""
    # Phase 14 — Temporal Synchronization (Proposal §3.1).
    # Per-variable trajectory: {var: {"direction": "rising"|"falling"|"stable",
    #                                  "rate": float, "previous": float}}.
    # Computed by app.subia.temporal.momentum.update_momentum() after each
    # FEEL/UPDATE step. Empty until first call.
    momentum: dict = field(default_factory=dict)


# ── Prediction ───────────────────────────────────────────────────────

@dataclass
class Prediction:
    """A single counterfactual prediction about an upcoming operation."""
    id: str
    operation: str
    predicted_outcome: dict                     # Expected world changes
    predicted_self_change: dict                 # Expected self-state changes
    predicted_homeostatic_effect: dict          # Expected variable shifts
    confidence: float                           # 0.0–1.0
    created_at: str
    resolved: bool = False
    actual_outcome: Optional[dict] = None
    prediction_error: Optional[float] = None
    cached: bool = False                        # True if from prediction template cache


# ── Social model ─────────────────────────────────────────────────────

@dataclass
class SocialModelEntry:
    """Theory-of-Mind model of a specific human or agent."""
    entity_id: str                              # 'andrus', 'commander', 'researcher', …
    entity_type: str                            # 'human' | 'agent'
    inferred_focus: list = field(default_factory=list)
    inferred_expectations: list = field(default_factory=list)
    inferred_priorities: list = field(default_factory=list)
    trust_level: float = 0.7
    last_interaction: str = ""
    divergences: list = field(default_factory=list)


# ── Meta-monitor ─────────────────────────────────────────────────────

@dataclass
class MetaMonitorState:
    """Higher-order representation of the system's cognitive state."""
    confidence: float = 0.5
    uncertainty_sources: list = field(default_factory=list)
    known_unknowns: list = field(default_factory=list)
    attention_justification: dict = field(default_factory=dict)  # item_id → reason
    active_prediction_mismatches: list = field(default_factory=list)
    agent_conflicts: list = field(default_factory=list)
    missing_information: list = field(default_factory=list)


# ── Consolidation buffer ─────────────────────────────────────────────

@dataclass
class ConsolidationBuffer:
    """Pending writes staged during CIL step 10."""
    pending_episodes: list = field(default_factory=list)
    pending_relations: list = field(default_factory=list)
    pending_self_updates: list = field(default_factory=list)
    pending_domain_updates: list = field(default_factory=list)


# ── The kernel ───────────────────────────────────────────────────────

@dataclass
class SubjectivityKernel:
    """Unified runtime state of the Subjective Integration Architecture.

    One dataclass, seven components. Persists across operations
    (serialized to wiki/self/kernel-state.md) and across sessions
    (loaded on startup, with hot.md providing session-specific overlay).

    Behavior lives in sibling modules — this dataclass is pure data.
    """
    # Seven kernel components
    scene: list = field(default_factory=list)                    # List[SceneItem] (focal + peripheral)
    self_state: SelfState = field(default_factory=SelfState)
    homeostasis: HomeostaticState = field(default_factory=HomeostaticState)
    meta_monitor: MetaMonitorState = field(default_factory=MetaMonitorState)
    predictions: list = field(default_factory=list)              # List[Prediction]
    social_models: dict = field(default_factory=dict)            # entity_id → SocialModelEntry
    consolidation_buffer: ConsolidationBuffer = field(default_factory=ConsolidationBuffer)

    # Loop metadata
    loop_count: int = 0
    last_loop_at: str = ""
    session_id: str = ""

    # Phase 14 — Temporal Synchronization.
    # SpeciousPresent is a SubjectivityKernel attribute (not a separate
    # store) because it must be SIMULTANEOUSLY present with the current
    # moment, not loaded on demand (Proposal §3.1, §7 "from sequence to
    # duration"). Defaults to None for backward-compat; populated by
    # `app.subia.temporal.specious_present.update_specious_present()` on
    # each loop entry.
    #
    # Type is `Any` to avoid a forward-reference cycle with the temporal
    # subpackage (kernel.py is imported very early; temporal/ depends on
    # kernel). Concrete type is `app.subia.temporal.SpeciousPresent`.
    specious_present: Any = None
    # TemporalContext: clock + circadian mode + processing density +
    # discovered external rhythms. Populated by the same temporal hook.
    temporal_context: Any = None

    def touch(self) -> None:
        """Mark the kernel as updated now."""
        self.last_loop_at = datetime.now(timezone.utc).isoformat()

    def focal_scene(self) -> list:
        """Return only focal-tier scene items."""
        return [i for i in self.scene if getattr(i, "tier", "focal") == "focal"]

    def peripheral_scene(self) -> list:
        """Return only peripheral-tier scene items."""
        return [i for i in self.scene if getattr(i, "tier", "focal") == "peripheral"]
