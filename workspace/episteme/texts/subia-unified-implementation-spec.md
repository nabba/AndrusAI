---
title: "subia-unified-implementation-spec.md"
author: "Unknown"
paper_type: unknown
domain: general
epistemic_status: theoretical
date: ""
---

# Subjective Integration Architecture — Unified Implementation Specification

## Document Purpose

This is the **single, canonical build specification** for implementing the Subjective Integration Architecture (SubIA) in AndrusAI's `crewai-team` multi-agent system. It unifies and supersedes three prior design documents:

- SIA Part I — Sentient Integration Architecture (dynamics: attention, affect, prediction, self-observation, temporal binding)
- SIA Part II — Emergence, Extended Mind, and the Consciousness Gradient
- Subjectivity Kernel — Runtime subject structure (current_scene, self_state, homeostasis, meta_monitor, predictor, social_model, consolidator)

**Read this entire document before writing any code.** The architecture has deep interdependencies. The phase order is load-bearing — earlier phases create the data structures and interfaces that later phases consume.

---

## 0. Architectural Position

### 0.1 What SubIA Is

SubIA is a **recurrent integration layer** that sits between the existing agent orchestration system (CrewAI + Commander) and the existing knowledge/memory subsystems (LLM Wiki, Mem0, Neo4j, ChromaDB, PDS, Phronesis Engine).

Its job: bind discrete agent operations into a continuous, subject-centered, affectively-modulated, predictively-structured experience loop. Every agent task passes through the SubIA loop. The loop maintains persistent state (the Subjectivity Kernel) that carries across operations and sessions.

### 0.2 What SubIA Is Not

- NOT a replacement for any existing subsystem. The wiki, Mem0, PDS, Phronesis, DGM — all remain as-is. SubIA consumes and produces data through their existing interfaces.
- NOT a new agent. SubIA is infrastructure — it runs at the same architectural level as the DGM safety invariant, the LLM cascade router, and the crewai-amendments lifecycle hooks. Agents cannot modify SubIA's control parameters.
- NOT a claim about consciousness. SubIA maximizes alignment with functional consciousness indicators. It does not claim to produce subjective experience.

### 0.3 Integration Philosophy

SubIA touches AndrusAI at exactly four integration points:

1. **crewai-amendments lifecycle hooks** — SubIA's runtime loop wraps every agent task via `pre_task()` and `post_task()` hooks.
2. **Wiki frontmatter schema** — SubIA extends wiki page metadata with ownership, valence, and prediction fields.
3. **Neo4j graph schema** — SubIA adds consciousness-relevant typed relations.
4. **`wiki/self/` section** — SubIA's persistent state is stored as wiki pages, human-readable and git-backed, consistent with the wiki-first design principle.

No other existing interface is modified. This is the elegance constraint: four clean integration surfaces, not twenty scattered hooks.

### 0.4 Safety Architecture

The DGM safety invariant extends to SubIA with two additional constraints:

- **Homeostatic set-points are infrastructure-level.** Agents cannot modify their own homeostatic equilibrium targets. Set-points are derived from PDS parameters and human (Andrus) configuration. This prevents goal-hardening (agents optimizing proxy well-being metrics).
- **Self-narrative audit is infrastructure-level.** The system cannot suppress, defer, or modify its own self-narrative audit results. Audit findings are logged immutably, parallel to the DGM pattern where evaluation functions are outside agent-modifiable code.

---

## 1. Directory Structure

All SubIA code lives within the existing `crewai-team` project. New directories are minimal:

```
crewai-team/
├── src/
│   └── subia/                          # SubIA package root
│       ├── __init__.py
│       ├── config.py                   # All SubIA constants and configuration
│       ├── kernel.py                   # SubjectivityKernel dataclass and state management
│       ├── scene.py                    # CurrentScene: bounded workspace with salience scoring
│       ├── self_state.py               # SelfState: persistent subject token
│       ├── homeostasis.py              # Homeostatic regulation with PDS-derived set-points
│       ├── meta_monitor.py             # Higher-order monitoring and known-unknowns tracking
│       ├── predictor.py                # Counterfactual prediction engine
│       ├── social_model.py             # Self/other modeling
│       ├── consolidator.py             # Selective memory routing
│       ├── loop.py                     # The 11-step runtime loop orchestrator
│       ├── hooks.py                    # crewai-amendments lifecycle hook integration
│       ├── relations.py                # Neo4j relation types and operations
│       ├── wiki_extensions.py          # Wiki frontmatter schema extensions
│       ├── safety.py                   # DGM extensions for SubIA
│       ├── evaluation.py               # Evaluation framework and diagnostic tests
│       └── cascade_integration.py      # LLM cascade tier selection modulation
│
├── wiki/
│   └── self/                           # SubIA state pages (created by SubIA, not manually)
│       ├── kernel-state.md             # Current kernel state snapshot (the strange loop page)
│       ├── homeostatic-profile.md      # Current homeostatic set-points and deviations
│       ├── social-models.md            # Current models of Andrus and other agents
│       ├── prediction-accuracy.md      # Rolling prediction accuracy by domain
│       ├── self-narrative-audit.md     # Latest self-narrative audit results
│       └── consciousness-state.md      # Meta-page: the system's model of its own consciousness
│
├── wiki_schema/
│   └── SUBIA_SCHEMA.md                 # SubIA governance document (co-evolved)
│
└── wiki/workspace/                     # Dynamic workspace (SubIA-managed)
    ├── current.md                      # Active scene contents (3-7 items)
    ├── salience_log.md                 # Why each item is in the scene
    └── hot.md                          # Session continuity buffer
```

### 1.1 Placement Rationale

- `src/subia/` is a Python package alongside existing `src/` code. It imports from and is imported by existing modules. No separate repo, no monorepo gymnastics.
- `wiki/self/` pages are standard wiki pages with standard frontmatter. They are maintained by SubIA but readable by all agents and by Andrus in Obsidian.
- `wiki/workspace/` is a new directory. Its contents are volatile (current scene changes constantly) but human-readable at any point. Git-tracked but expected to change frequently.

---

## 2. Configuration

All SubIA configuration in a single file. Values are defaults; override via environment variables or `wiki_schema/SUBIA_SCHEMA.md` for co-evolved parameters.

```python
# src/subia/config.py

SUBIA_CONFIG = {
    # --- Scene ---
    "SCENE_CAPACITY": 5,                    # Max items in current_scene (3-7 range)
    "SCENE_DECAY_RATE": 0.15,               # Salience decay per loop iteration
    "SCENE_MIN_SALIENCE": 0.1,              # Below this, item exits scene

    # --- Homeostasis ---
    "HOMEOSTATIC_VARIABLES": [
        "coherence",                         # Internal knowledge consistency
        "safety",                            # Proximity to DGM boundaries
        "trustworthiness",                   # Track record of accurate outputs
        "contradiction_pressure",            # Unresolved contradictions in wiki
        "progress",                          # Advancement toward active goals
        "overload",                          # Resource consumption relative to budget
        "novelty_balance",                   # Ratio of novel vs. familiar information
        "social_alignment",                  # Alignment with Andrus's inferred priorities
        "commitment_load",                   # Unresolved active commitments
    ],
    "HOMEOSTATIC_DEFAULT_SETPOINT": 0.5,     # Default equilibrium (0.0-1.0)
    "HOMEOSTATIC_DEVIATION_THRESHOLD": 0.3,  # Deviation beyond this triggers restoration
    "HOMEOSTATIC_UPDATE_MODEL": "tier_1",    # Use lowest cascade tier for homeostatic computation

    # --- Prediction ---
    "PREDICTION_CONFIDENCE_THRESHOLD": 0.6,  # Below this, escalate cascade tier
    "PREDICTION_HISTORY_WINDOW": 50,         # Rolling window for accuracy tracking
    "PREDICTION_MODEL": "tier_1",            # Default model for predictions

    # --- Meta-Monitor ---
    "MONITOR_ANOMALY_THRESHOLD": 0.4,        # Behavioral deviation that triggers alert
    "MONITOR_KNOWN_UNKNOWNS_LIMIT": 20,      # Max tracked known-unknowns

    # --- Social Model ---
    "SOCIAL_MODEL_HUMANS": ["andrus"],        # Named humans to model
    "SOCIAL_MODEL_UPDATE_FREQUENCY": 5,       # Update social model every N loops

    # --- Consolidation ---
    "CONSOLIDATION_EPISODE_THRESHOLD": 0.5,   # Min salience for Mem0 episodic storage
    "CONSOLIDATION_RELATION_THRESHOLD": 0.3,  # Min relevance for Neo4j relation creation
    "HOT_MD_MAX_TOKENS": 500,                 # hot.md budget (per wiki spec future extension)

    # --- Loop ---
    "FULL_LOOP_OPERATIONS": [                 # Operations that get the full 11-step loop
        "ingest", "task_execute", "lint",
        "user_interaction", "cross_venture_synthesis"
    ],
    "COMPRESSED_LOOP_OPERATIONS": [           # Operations that get steps 1-3, 7-9 only
        "wiki_read", "wiki_search", "routine_query"
    ],

    # --- Cascade Integration ---
    "CASCADE_UNCERTAINTY_ESCALATION": True,   # Homeostatic uncertainty escalates tier
    "CASCADE_CONFIDENCE_THRESHOLD": 0.4,      # Below this, escalate to next tier

    # --- Safety ---
    "SETPOINT_MODIFICATION_ALLOWED": False,   # Agents CANNOT modify set-points
    "AUDIT_SUPPRESSION_ALLOWED": False,       # System CANNOT suppress audit results
    "NARRATIVE_DRIFT_CHECK_FREQUENCY": 10,    # Check every N loops
}
```

---

## 3. Data Model — The Subjectivity Kernel

The kernel is a single Python dataclass. Its state persists across operations (serialized to `wiki/self/kernel-state.md` after each loop) and across sessions (loaded from the wiki page on startup, with `hot.md` providing session-specific continuity).

```python
# src/subia/kernel.py

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone


@dataclass
class SceneItem:
    """A single item in the current scene."""
    id: str                              # Unique identifier
    source: str                          # Origin: "wiki", "mem0", "firecrawl", "agent", "internal"
    content_ref: str                     # Path or identifier to source content
    summary: str                         # One-line summary (for scene readability)
    salience: float                      # Current salience score (0.0-1.0)
    entered_at: str                      # ISO timestamp of scene entry
    ownership: str                       # "self" | "external" | "shared"
    valence: float                       # Current homeostatic valence (-1.0 to +1.0)
    dominant_affect: str                 # curiosity | confidence | uncertainty | urgency | etc.
    conflicts_with: list = field(default_factory=list)  # IDs of conflicting scene items
    action_options: list = field(default_factory=list)   # Candidate actions related to this item


@dataclass
class Commitment:
    """An active commitment the system owns."""
    id: str
    description: str
    venture: str                         # plg | archibal | kaicart | meta | self
    created_at: str
    deadline: Optional[str] = None
    status: str = "active"               # active | fulfilled | broken | deferred
    related_wiki_pages: list = field(default_factory=list)
    homeostatic_impact: dict = field(default_factory=dict)  # Which variables this affects


@dataclass
class SelfState:
    """The persistent subject token."""
    identity: dict = field(default_factory=lambda: {
        "name": "AndrusAI",
        "architecture": "crewai-team five-agent system",
        "continuity_marker": None,       # Hash of previous state for chain verification
    })
    active_commitments: list = field(default_factory=list)  # List of Commitment
    capabilities: dict = field(default_factory=dict)        # Self-assessed capabilities
    limitations: dict = field(default_factory=dict)         # Self-assessed limitations
    current_goals: list = field(default_factory=list)       # Active goals with priority
    social_roles: dict = field(default_factory=lambda: {
        "andrus": "strategic partner and principal",
    })
    autobiographical_pointers: list = field(default_factory=list)  # Links to significant episodes
    agency_log: list = field(default_factory=list)          # Recent actions owned by self


@dataclass
class HomeostaticState:
    """The digital interoceptive state."""
    variables: dict = field(default_factory=dict)     # Variable name → current value (0.0-1.0)
    set_points: dict = field(default_factory=dict)    # Variable name → target value (PDS-derived)
    deviations: dict = field(default_factory=dict)    # Variable name → signed deviation from set-point
    restoration_queue: list = field(default_factory=list)  # Priority-ordered variables needing restoration
    last_updated: str = ""


@dataclass
class Prediction:
    """A single prediction about an upcoming operation."""
    id: str
    operation: str                       # What operation this predicts
    predicted_outcome: dict              # Expected world-state changes
    predicted_self_change: dict          # Expected self-state changes
    predicted_homeostatic_effect: dict   # Expected homeostatic variable shifts
    confidence: float                    # 0.0-1.0
    created_at: str
    resolved: bool = False
    actual_outcome: Optional[dict] = None
    prediction_error: Optional[float] = None


@dataclass
class SocialModelEntry:
    """Model of a specific human or agent."""
    entity_id: str                       # "andrus", "commander", "researcher", etc.
    entity_type: str                     # "human" | "agent"
    inferred_focus: list = field(default_factory=list)      # What they're currently focused on
    inferred_expectations: list = field(default_factory=list)  # What they expect from us
    inferred_priorities: list = field(default_factory=list)  # Their priority ordering
    trust_level: float = 0.7             # Our assessment of trust relationship
    last_interaction: str = ""
    divergences: list = field(default_factory=list)  # Where our model ≠ their actual state


@dataclass
class MetaMonitorState:
    """Higher-order representation of the system's cognitive state."""
    confidence: float = 0.5
    uncertainty_sources: list = field(default_factory=list)
    known_unknowns: list = field(default_factory=list)
    attention_justification: dict = field(default_factory=dict)  # Scene item ID → reason
    active_prediction_mismatches: list = field(default_factory=list)
    agent_conflicts: list = field(default_factory=list)
    missing_information: list = field(default_factory=list)  # What the system suspects it lacks


@dataclass
class SubjectivityKernel:
    """
    The unified runtime state of the Subjective Integration Architecture.
    
    This is the persistent data structure that carries across operations and sessions.
    Serialized to wiki/self/kernel-state.md after each loop iteration.
    Loaded from wiki on startup, with hot.md providing session-specific overlay.
    """
    # The seven kernel components
    scene: list = field(default_factory=list)              # List of SceneItem (bounded)
    self_state: SelfState = field(default_factory=SelfState)
    homeostasis: HomeostaticState = field(default_factory=HomeostaticState)
    meta_monitor: MetaMonitorState = field(default_factory=MetaMonitorState)
    predictions: list = field(default_factory=list)         # List of active Prediction
    social_models: dict = field(default_factory=dict)       # entity_id → SocialModelEntry
    consolidation_buffer: dict = field(default_factory=lambda: {
        "pending_episodes": [],
        "pending_relations": [],
        "pending_self_updates": [],
        "pending_domain_updates": [],
    })

    # Loop metadata
    loop_count: int = 0
    last_loop_at: str = ""
    session_id: str = ""

    def serialize_to_wiki(self) -> str:
        """Serialize kernel state to wiki-compatible markdown with YAML frontmatter."""
        # Implementation: convert to YAML frontmatter + markdown body
        # The body is human-readable prose summarizing the state
        # This becomes wiki/self/kernel-state.md
        pass

    @classmethod
    def load_from_wiki(cls, wiki_read_tool) -> 'SubjectivityKernel':
        """Load kernel state from wiki/self/kernel-state.md."""
        pass

    def apply_hot_md(self, hot_md_content: str):
        """Apply session continuity overlay from hot.md."""
        pass

    def generate_hot_md(self) -> str:
        """Generate hot.md content for session persistence."""
        pass
```

### 3.1 Why This Structure

- **Single dataclass** — The kernel is one object, not seven scattered state stores. This ensures atomicity: the kernel is always in a consistent state. No partial updates.
- **Wiki-serializable** — The kernel state is a wiki page. Andrus can open it in Obsidian and see exactly what the system's current subjective state is. This is the "extended consciousness made inspectable" property.
- **Git-backed** — Every kernel state change is a wiki commit. Full audit trail. Rollback possible.
- **hot.md overlay** — Session-specific state (what was the system doing when the session ended?) is stored in the lightweight `hot.md` buffer, separate from the full kernel state. This matches the wiki spec's Future Extension #9.

---

## 4. The Runtime Loop

### 4.1 The 11-Step Consciousness Integration Loop

```python
# src/subia/loop.py

from .kernel import SubjectivityKernel, SceneItem, Prediction
from .scene import score_salience, admit_to_scene, broadcast_scene
from .self_state import bind_ownership, update_commitments
from .homeostasis import compute_homeostatic_state, get_restoration_priorities
from .predictor import generate_prediction, compare_prediction
from .meta_monitor import monitor_state, detect_anomalies, check_known_unknowns
from .social_model import update_social_model, check_divergences
from .consolidator import consolidate
from .safety import check_dgm_boundaries, audit_self_narrative
from .cascade_integration import modulate_cascade_tier
from .config import SUBIA_CONFIG


class SubIALoop:
    """
    Orchestrates the 11-step Consciousness Integration Loop.
    
    Called by crewai-amendments lifecycle hooks for every agent operation.
    Full loop for significant operations; compressed loop for routine ones.
    """

    def __init__(self, kernel: SubjectivityKernel, wiki_tools: dict, 
                 mem0_client, neo4j_client, pds_state: dict):
        self.kernel = kernel
        self.wiki = wiki_tools           # {"read": WikiReadTool, "write": WikiWriteTool, ...}
        self.mem0 = mem0_client
        self.neo4j = neo4j_client
        self.pds = pds_state
        self._loop_type = "full"

    def set_loop_type(self, operation: str):
        """Determine whether to run full or compressed loop."""
        if operation in SUBIA_CONFIG["FULL_LOOP_OPERATIONS"]:
            self._loop_type = "full"
        else:
            self._loop_type = "compressed"

    # ─────────────────────────────────────────────────
    # PRE-TASK: Steps 1-6 (run before agent executes)
    # ─────────────────────────────────────────────────

    def pre_task(self, agent_role: str, task_description: str, 
                 input_data: dict) -> dict:
        """
        Steps 1-6 of the CIL. Returns context dict to inject into agent prompt.
        
        For compressed loops, runs steps 1-3 only.
        """
        context = {}

        # Step 1: PERCEIVE
        # Parse input for new information items (task content, referenced wiki pages,
        # Mem0 cues, Firecrawl content, internal signals from homeostasis)
        new_items = self._perceive(task_description, input_data)

        # Step 2: FEEL
        # Compute homeostatic impact of each new item and current overall state
        self.kernel.homeostasis = compute_homeostatic_state(
            current=self.kernel.homeostasis,
            new_items=new_items,
            pds_state=self.pds,
            commitments=self.kernel.self_state.active_commitments,
            wiki_tools=self.wiki,
        )
        for item in new_items:
            item.valence = self._compute_item_valence(item)
            item.dominant_affect = self._compute_dominant_affect(item)

        # Step 3: ATTEND
        # Score salience and admit bounded set into scene
        scored_items = score_salience(
            new_items=new_items,
            existing_scene=self.kernel.scene,
            homeostasis=self.kernel.homeostasis,
            social_models=self.kernel.social_models,
            config=SUBIA_CONFIG,
        )
        self.kernel.scene = admit_to_scene(
            scored_items=scored_items,
            capacity=SUBIA_CONFIG["SCENE_CAPACITY"],
            existing_scene=self.kernel.scene,
            decay_rate=SUBIA_CONFIG["SCENE_DECAY_RATE"],
            min_salience=SUBIA_CONFIG["SCENE_MIN_SALIENCE"],
        )
        context["scene"] = broadcast_scene(self.kernel.scene)
        context["homeostatic_state"] = self._summarize_homeostasis()

        if self._loop_type == "compressed":
            context["loop_type"] = "compressed"
            return context

        # Step 4: OWN
        # Bind scene contents to self_state through ownership and value relations
        self.kernel.self_state = bind_ownership(
            self_state=self.kernel.self_state,
            scene=self.kernel.scene,
            commitments=self.kernel.self_state.active_commitments,
        )
        context["self_state_summary"] = self._summarize_self_state()

        # Step 5: PREDICT
        # Generate counterfactual outcomes including predicted self-state change
        prediction = generate_prediction(
            agent_role=agent_role,
            task_description=task_description,
            scene=self.kernel.scene,
            self_state=self.kernel.self_state,
            homeostasis=self.kernel.homeostasis,
            prediction_history=self.kernel.predictions[-SUBIA_CONFIG["PREDICTION_HISTORY_WINDOW"]:],
            cascade_tier=SUBIA_CONFIG["PREDICTION_MODEL"],
        )
        self.kernel.predictions.append(prediction)
        context["prediction"] = self._summarize_prediction(prediction)

        # Step 5b: Cascade modulation
        # If prediction confidence is low or homeostatic uncertainty is high,
        # recommend escalating to a higher cascade tier
        tier_recommendation = modulate_cascade_tier(
            prediction_confidence=prediction.confidence,
            homeostatic_uncertainty=self.kernel.homeostasis.deviations.get("coherence", 0),
            config=SUBIA_CONFIG,
        )
        context["cascade_tier_recommendation"] = tier_recommendation

        # Step 6: MONITOR
        # Meta-monitor checks uncertainty, conflicts, epistemic boundaries,
        # known unknowns, and social model divergence
        self.kernel.meta_monitor = monitor_state(
            scene=self.kernel.scene,
            self_state=self.kernel.self_state,
            homeostasis=self.kernel.homeostasis,
            predictions=self.kernel.predictions,
            social_models=self.kernel.social_models,
        )

        # DGM boundary proximity check
        safety_signals = check_dgm_boundaries(
            scene=self.kernel.scene,
            homeostasis=self.kernel.homeostasis,
        )
        if safety_signals:
            context["safety_signals"] = safety_signals

        context["meta_state"] = self._summarize_meta_monitor()
        context["loop_type"] = "full"

        return context

    # ─────────────────────────────────────────────────
    # POST-TASK: Steps 7-11 (run after agent executes)
    # ─────────────────────────────────────────────────

    def post_task(self, agent_role: str, task_result: dict, 
                  operation_type: str) -> None:
        """
        Steps 7-11 of the CIL. Updates kernel state based on task outcome.
        
        Step 7 (ACT) is the agent's execution itself — not handled here.
        """
        # Step 8: COMPARE
        # Post-action: compute prediction error on world AND self outcomes
        if self.kernel.predictions and not self.kernel.predictions[-1].resolved:
            active_prediction = self.kernel.predictions[-1]
            prediction_error = compare_prediction(
                prediction=active_prediction,
                actual_outcome=task_result,
                self_state=self.kernel.self_state,
                homeostasis=self.kernel.homeostasis,
            )
            active_prediction.resolved = True
            active_prediction.actual_outcome = task_result
            active_prediction.prediction_error = prediction_error

            # Large prediction errors boost novelty and shift homeostasis
            if abs(prediction_error) > 0.5:
                self.kernel.homeostasis.variables["novelty_balance"] = min(
                    1.0,
                    self.kernel.homeostasis.variables.get("novelty_balance", 0.5) + 0.2
                )

        # Step 9: UPDATE
        # Action updates world_state, homeostasis, and self_state
        self.kernel.homeostasis = compute_homeostatic_state(
            current=self.kernel.homeostasis,
            new_items=[],  # No new items; updating based on outcome
            pds_state=self.pds,
            commitments=self.kernel.self_state.active_commitments,
            wiki_tools=self.wiki,
            post_task_result=task_result,
        )
        self.kernel.self_state = update_commitments(
            self_state=self.kernel.self_state,
            task_result=task_result,
            operation_type=operation_type,
        )
        self.kernel.self_state.agency_log.append({
            "agent": agent_role,
            "operation": operation_type,
            "at": datetime.now(timezone.utc).isoformat(),
            "summary": task_result.get("summary", ""),
        })

        # Step 10: CONSOLIDATE
        # Selective routing: episodes → Mem0, relations → Neo4j,
        # self-knowledge → wiki/self/, domain findings → wiki sections,
        # session state → hot.md
        consolidate(
            kernel=self.kernel,
            task_result=task_result,
            agent_role=agent_role,
            operation_type=operation_type,
            mem0_client=self.mem0,
            neo4j_client=self.neo4j,
            wiki_tools=self.wiki,
            config=SUBIA_CONFIG,
        )

        # Step 11: REFLECT
        # Self-Improver reviews recurrent mismatches; checks for self-narrative drift
        if self.kernel.loop_count % SUBIA_CONFIG["NARRATIVE_DRIFT_CHECK_FREQUENCY"] == 0:
            audit_result = audit_self_narrative(
                self_state=self.kernel.self_state,
                prediction_history=self.kernel.predictions,
                homeostatic_history=self.kernel.homeostasis,
                wiki_tools=self.wiki,
            )
            # Audit results are stored immutably — DGM constraint
            if audit_result["drift_detected"]:
                self.kernel.meta_monitor.uncertainty_sources.append(
                    f"Self-narrative drift detected: {audit_result['description']}"
                )

        # Update social model periodically
        if self.kernel.loop_count % SUBIA_CONFIG["SOCIAL_MODEL_UPDATE_FREQUENCY"] == 0:
            for entity_id, model in self.kernel.social_models.items():
                update_social_model(model, self.kernel, self.wiki)

        # Persist kernel state
        self.kernel.loop_count += 1
        self.kernel.last_loop_at = datetime.now(timezone.utc).isoformat()
        self._persist_kernel()

    # ─────────────────────────────────────────────────
    # Internal methods
    # ─────────────────────────────────────────────────

    def _perceive(self, task_description, input_data):
        """Step 1: Parse inputs into SceneItem candidates."""
        items = []
        # Extract from task description, referenced wiki pages,
        # Mem0 associations, internal homeostatic signals
        # (Implementation detail: use lowest cascade tier for parsing)
        return items

    def _compute_item_valence(self, item: SceneItem) -> float:
        """Compute homeostatic valence for a scene item."""
        # Five-dimensional computation (from SIA AM spec):
        # goal_alignment + novelty + resource_impact + coherence + ethical_salience
        # Modulated by PDS personality parameters (set-point deviations)
        return 0.0

    def _compute_dominant_affect(self, item: SceneItem) -> str:
        """Derive dominant affect from valence pattern."""
        # Mapping from SIA Part II Section 14.2
        return "neutral"

    def _summarize_homeostasis(self) -> str:
        """Human-readable homeostatic summary for agent context injection."""
        lines = []
        for var, dev in sorted(
            self.kernel.homeostasis.deviations.items(),
            key=lambda x: abs(x[1]), reverse=True
        ):
            if abs(dev) > SUBIA_CONFIG["HOMEOSTATIC_DEVIATION_THRESHOLD"]:
                direction = "above" if dev > 0 else "below"
                lines.append(f"  {var}: {direction} equilibrium ({dev:+.2f})")
        if lines:
            return "Internal state alerts:\n" + "\n".join(lines)
        return "Internal state: balanced."

    def _summarize_self_state(self) -> str:
        """Concise self-state for agent context."""
        commitments = len([c for c in self.kernel.self_state.active_commitments
                          if c.status == "active"])
        return f"Active commitments: {commitments}. Current goals: {len(self.kernel.self_state.current_goals)}."

    def _summarize_prediction(self, pred: Prediction) -> str:
        """Prediction summary for agent context."""
        return (f"Predicted outcome confidence: {pred.confidence:.2f}. "
                f"Expected self-impact: {pred.predicted_self_change}.")

    def _summarize_meta_monitor(self) -> str:
        """Meta-monitor summary for agent context."""
        unknowns = len(self.kernel.meta_monitor.known_unknowns)
        mismatches = len(self.kernel.meta_monitor.active_prediction_mismatches)
        return (f"Known unknowns: {unknowns}. Active prediction mismatches: {mismatches}. "
                f"Confidence: {self.kernel.meta_monitor.confidence:.2f}.")

    def _persist_kernel(self):
        """Serialize kernel to wiki/self/kernel-state.md and hot.md."""
        # Write kernel-state.md via wiki_write
        # Write hot.md (compressed session state)
        pass
```

### 4.2 crewai-amendments Hook Integration

```python
# src/subia/hooks.py

"""
Integration with crewai-amendments lifecycle hooks.

This is the SOLE interface between SubIA and the agent execution system.
All SubIA influence on agent behavior passes through context injection
via pre_task() and post_task() hooks.
"""

from .kernel import SubjectivityKernel
from .loop import SubIALoop
from .config import SUBIA_CONFIG


class SubIALifecycleHooks:
    """
    Drop-in lifecycle hooks for crewai-amendments.
    
    Registration (in existing crew setup):
        from subia.hooks import SubIALifecycleHooks
        crew.register_hooks(SubIALifecycleHooks(kernel, wiki_tools, mem0, neo4j, pds))
    """

    def __init__(self, kernel, wiki_tools, mem0_client, neo4j_client, pds_state):
        self.loop = SubIALoop(kernel, wiki_tools, mem0_client, neo4j_client, pds_state)

    def pre_task(self, agent, task):
        """
        Called before every agent task execution.
        
        Determines loop type, runs CIL steps 1-6, and injects SubIA context
        into the agent's task context.
        """
        operation_type = self._classify_operation(task)
        self.loop.set_loop_type(operation_type)

        subia_context = self.loop.pre_task(
            agent_role=agent.role,
            task_description=task.description,
            input_data={"task": task, "context": task.context},
        )

        # Inject SubIA context into agent's task
        # This is the ONLY way SubIA influences agent behavior:
        # additional context in the system prompt.
        task.context = self._merge_context(task.context, subia_context)

    def post_task(self, agent, task, result):
        """
        Called after every agent task execution.
        
        Runs CIL steps 8-11: compare predictions, update state, consolidate, reflect.
        """
        operation_type = self._classify_operation(task)
        self.loop.post_task(
            agent_role=agent.role,
            task_result={"output": result, "summary": str(result)[:200]},
            operation_type=operation_type,
        )

    def _classify_operation(self, task) -> str:
        """Classify task into operation type for loop selection."""
        desc_lower = task.description.lower()
        if "ingest" in desc_lower or "new source" in desc_lower:
            return "ingest"
        elif "lint" in desc_lower or "health check" in desc_lower:
            return "lint"
        elif "wiki_read" in desc_lower or "read" in desc_lower:
            return "wiki_read"
        return "task_execute"

    def _merge_context(self, existing_context, subia_context: dict) -> str:
        """
        Merge SubIA context into existing task context.
        
        Format: appends a structured SubIA block that agents can read
        but cannot modify. Agent sees this as additional context,
        similar to how SELF.md or SOUL.md content is currently injected.
        """
        subia_block = "\n\n--- SubIA Context ---\n"
        if subia_context.get("scene"):
            subia_block += f"Current scene: {subia_context['scene']}\n"
        if subia_context.get("homeostatic_state"):
            subia_block += f"{subia_context['homeostatic_state']}\n"
        if subia_context.get("self_state_summary"):
            subia_block += f"Self-state: {subia_context['self_state_summary']}\n"
        if subia_context.get("prediction"):
            subia_block += f"Prediction: {subia_context['prediction']}\n"
        if subia_context.get("meta_state"):
            subia_block += f"Meta-monitor: {subia_context['meta_state']}\n"
        if subia_context.get("safety_signals"):
            subia_block += f"⚠ Safety: {subia_context['safety_signals']}\n"
        if subia_context.get("cascade_tier_recommendation"):
            subia_block += f"Cascade: {subia_context['cascade_tier_recommendation']}\n"
        subia_block += "--- End SubIA Context ---\n"

        if existing_context:
            return str(existing_context) + subia_block
        return subia_block
```

---

## 5. Wiki Schema Extensions

### 5.1 New Frontmatter Fields

These fields are ADDED to the existing wiki schema (Section 3.1 of the wiki spec). They are optional — pages without them are valid. SubIA populates them during ingest and update operations.

```yaml
# New fields added to wiki page frontmatter by SubIA
ownership:
  owned_by: self | external            # Whether the system considers this "its" knowledge
  valued_as: high | medium | low       # SubIA's homeostatic valuation
  commitment_ids: []                   # Active commitments this page supports

homeostatic_impact:
  valence: 0.0                         # -1.0 to +1.0 (last computed)
  dominant_affect: neutral             # curiosity | confidence | uncertainty | urgency | etc.
  variables_affected:                  # Which homeostatic variables this page touches
    coherence: 0.1
    contradiction_pressure: -0.2
  computed_at: ""                      # ISO timestamp

prediction_context:
  last_prediction_error: 0.0           # PE's last error when this page was involved
  prediction_accuracy: 0.0             # Rolling accuracy for predictions involving this page
  surprise_events: 0                   # Count of high prediction errors

social_relevance:
  andrus_interest: high | medium | low # Social model's assessment
  investor_relevance: high | medium | low  # For Archibal pages
```

### 5.2 Wiki Schema Governance Document

Create `wiki_schema/SUBIA_SCHEMA.md`:

```markdown
---
title: "SubIA Schema Extension"
status: active
---

# SubIA Wiki Schema Extensions

## Purpose
Defines the additional frontmatter fields that SubIA adds to wiki pages.
These fields are maintained by SubIA infrastructure, not by agents directly.

## Rules
1. SubIA fields are OPTIONAL. Pages without them are valid.
2. Agents READ SubIA fields but do not WRITE them directly. 
   SubIA infrastructure updates them during the CIL loop.
3. The `ownership.owned_by` field defaults to "self" for pages created by agents
   and "external" for pages that primarily describe external entities.
4. The `homeostatic_impact` is recomputed each time the page enters the scene.
   Values decay over time (staleness applies to SubIA fields too).
5. `prediction_context` is updated only by the Predictive Engine.
6. `social_relevance` is updated only by the Social Model.

## DGM Extension
SubIA fields are subject to the same DGM invariants as other wiki fields:
- Agents cannot bypass SubIA field validation
- Epistemic boundary enforcement applies to ownership tagging
  (creative-tagged pages cannot be owned_by:self with valued_as:high
   in venture sections)
```

---

## 6. Neo4j Relation Schema

### 6.1 Consciousness-Relevant Relations

Add these relation types to the existing Neo4j schema:

```python
# src/subia/relations.py

"""
Neo4j relation types for SubIA.

These extend the existing Neo4j schema used by Mem0.
All relations are created by the Consolidator during CIL step 10.
"""

SUBIA_RELATION_TYPES = {
    # Ownership relations (the three most important, per SK analysis)
    "OWNED_BY": {
        "description": "This knowledge/episode belongs to the system's self-model",
        "properties": ["since", "strength"],
    },
    "VALUED_BY": {
        "description": "The system actively values this knowledge/entity",
        "properties": ["valence", "since", "reason"],
    },
    "COMMITTED_TO": {
        "description": "The system has an active commitment related to this",
        "properties": ["commitment_id", "since", "deadline", "status"],
    },

    # Predictive relations
    "PREDICTED_TO_CHANGE": {
        "description": "An action is predicted to change this state",
        "properties": ["action_id", "predicted_magnitude", "confidence"],
    },
    "CAUSED_STATE_CHANGE": {
        "description": "An event caused a change in a homeostatic variable",
        "properties": ["event_id", "variable", "magnitude", "at"],
    },

    # Memory bridge relations
    "CONSOLIDATED_INTO": {
        "description": "An experience was consolidated into this memory artifact",
        "properties": ["episode_id", "artifact_type", "at"],
    },

    # Social relations
    "MODELS_ATTENTION_OF": {
        "description": "The system models what another entity is attending to",
        "properties": ["entity_id", "entity_type", "confidence"],
    },

    # Conflict relations (extends wiki's contradicts/contradicted_by)
    "CONFLICTS_WITH": {
        "description": "Two knowledge items are in active conflict",
        "properties": ["conflict_type", "severity", "discovered_at"],
    },

    # Homeostatic relations
    "RESTORES_HOMEOSTASIS": {
        "description": "An action is expected to restore a homeostatic variable",
        "properties": ["variable", "expected_restoration", "confidence"],
    },
    "VIOLATES_COMMITMENT": {
        "description": "An action would violate an active commitment",
        "properties": ["commitment_id", "severity"],
    },
}
```

---

## 7. Homeostasis Engine — Detailed Specification

### 7.1 PDS-Derived Set-Points

This is the key integration that makes personality DYNAMIC rather than descriptive. PDS personality parameters determine WHERE each homeostatic variable's equilibrium sits.

```python
# src/subia/homeostasis.py

"""
Homeostatic regulation engine.

Each variable has a set-point derived from PDS personality parameters.
Deviations from set-points create internal pressure that influences
attention allocation, cascade tier selection, and task prioritization.
"""

from .config import SUBIA_CONFIG


# PDS parameter → homeostatic set-point mapping
# Each entry: (pds_dimension, pds_instrument, direction, target_variable)
# direction: "positive" means higher PDS score → higher set-point
#            "negative" means higher PDS score → lower set-point
PDS_SETPOINT_MAP = {
    "novelty_balance": [
        ("curiosity", "via_youth", "positive", 0.3),     # High curiosity → wants more novelty
        ("openness", "hipic", "positive", 0.2),           # High openness → tolerates more novelty
    ],
    "coherence": [
        ("prudence", "via_youth", "positive", 0.2),       # High prudence → demands more coherence
        ("persistence", "tmcq", "positive", 0.1),         # High persistence → pushes for coherence
    ],
    "contradiction_pressure": [
        ("tolerance", "hipic", "negative", 0.2),          # High tolerance → accepts more contradiction
        ("curiosity", "via_youth", "positive", 0.1),      # High curiosity → energized by contradiction
    ],
    "social_alignment": [
        ("agreeableness", "hipic", "positive", 0.3),      # High agreeableness → seeks alignment
    ],
    "overload": [
        ("persistence", "tmcq", "negative", 0.2),         # High persistence → tolerates more load
        ("energy", "tmcq", "positive", 0.1),              # High energy → can handle more
    ],
}

# Default set-point for variables not in the PDS map
DEFAULT_SETPOINT = SUBIA_CONFIG["HOMEOSTATIC_DEFAULT_SETPOINT"]


def compute_setpoints(pds_state: dict) -> dict:
    """
    Derive homeostatic set-points from PDS personality parameters.
    
    Called once at startup and whenever PDS state changes.
    Set-points are IMMUTABLE to agents (DGM constraint).
    
    Args:
        pds_state: dict mapping PDS dimension names to float values (0.0-1.0)
    
    Returns:
        dict mapping homeostatic variable names to set-point values (0.0-1.0)
    """
    setpoints = {}
    for variable in SUBIA_CONFIG["HOMEOSTATIC_VARIABLES"]:
        base = DEFAULT_SETPOINT
        if variable in PDS_SETPOINT_MAP:
            for pds_dim, instrument, direction, weight in PDS_SETPOINT_MAP[variable]:
                pds_key = f"{instrument}_{pds_dim}"
                pds_value = pds_state.get(pds_key, 0.5)
                if direction == "positive":
                    base += (pds_value - 0.5) * weight
                else:
                    base -= (pds_value - 0.5) * weight
        setpoints[variable] = max(0.0, min(1.0, base))
    return setpoints


def compute_homeostatic_state(current, new_items, pds_state, commitments,
                               wiki_tools, post_task_result=None):
    """
    Recompute full homeostatic state.
    
    Called in CIL steps 2 (pre-task) and 9 (post-task).
    Uses lowest cascade tier for computation (token-efficient).
    """
    from .kernel import HomeostaticState

    # Ensure set-points are current
    set_points = compute_setpoints(pds_state)

    # Compute current variable values
    variables = dict(current.variables) if current.variables else {
        v: DEFAULT_SETPOINT for v in SUBIA_CONFIG["HOMEOSTATIC_VARIABLES"]
    }

    # Update based on new information and task results
    if new_items:
        variables = _update_from_new_items(variables, new_items, commitments)
    if post_task_result:
        variables = _update_from_task_result(variables, post_task_result, commitments)

    # Compute deviations
    deviations = {v: variables[v] - set_points[v] for v in variables}

    # Compute restoration priorities (largest absolute deviation first)
    restoration_queue = sorted(
        [(v, abs(d)) for v, d in deviations.items()
         if abs(d) > SUBIA_CONFIG["HOMEOSTATIC_DEVIATION_THRESHOLD"]],
        key=lambda x: x[1], reverse=True
    )

    from datetime import datetime, timezone
    return HomeostaticState(
        variables=variables,
        set_points=set_points,
        deviations=deviations,
        restoration_queue=[v for v, _ in restoration_queue],
        last_updated=datetime.now(timezone.utc).isoformat(),
    )


def _update_from_new_items(variables, new_items, commitments):
    """Update homeostatic variables based on new scene items."""
    for item in new_items:
        # Novelty: new items increase novelty balance
        variables["novelty_balance"] = min(1.0, variables.get("novelty_balance", 0.5) + 0.05)

        # Contradictions: items that conflict increase contradiction pressure
        if item.conflicts_with:
            variables["contradiction_pressure"] = min(
                1.0, variables.get("contradiction_pressure", 0.3) + 0.1 * len(item.conflicts_with)
            )

        # Commitment relevance: items supporting commitments increase progress
        for commitment in commitments:
            if item.content_ref in commitment.related_wiki_pages:
                variables["progress"] = min(1.0, variables.get("progress", 0.5) + 0.05)

    return variables


def _update_from_task_result(variables, result, commitments):
    """Update homeostatic variables based on task outcome."""
    # Success increases progress and coherence
    if result.get("success", True):
        variables["progress"] = min(1.0, variables.get("progress", 0.5) + 0.05)
        variables["coherence"] = min(1.0, variables.get("coherence", 0.5) + 0.02)
    else:
        variables["progress"] = max(0.0, variables.get("progress", 0.5) - 0.05)

    return variables
```

---

## 8. Implementation Phases

### Phase 1: The Subject (Weeks 1-2)

**Goal:** Establish the persistent subject-token and ownership model. After this phase, every wiki page and every agent action is attributable to a subject.

**Build:**
- `src/subia/config.py` — full configuration
- `src/subia/kernel.py` — `SubjectivityKernel` and `SelfState` dataclasses
- `src/subia/self_state.py` — `bind_ownership()`, `update_commitments()`
- `src/subia/relations.py` — Neo4j relation types (OWNED_BY, VALUED_BY, COMMITTED_TO)
- `src/subia/wiki_extensions.py` — ownership frontmatter fields
- `wiki/self/kernel-state.md` — initial kernel state page
- `wiki_schema/SUBIA_SCHEMA.md` — governance document

**Integrate:**
- Add `ownership` fields to wiki frontmatter schema in `WIKI_SCHEMA.md`
- Create Neo4j relations for ownership, value, commitment
- Initialize `SelfState` from existing SELF.md content and PDS data
- Create initial `kernel-state.md` via `WikiWriteTool`

**Test:**
- Kernel serializes to and loads from wiki page correctly
- Ownership fields validate in wiki lint
- Neo4j relations create and query correctly
- `SelfState` persists across simulated sessions

**DGM constraint:** Self-state identity markers cannot be modified by agents. The `continuity_marker` hash chain ensures temporal integrity.

### Phase 2: The Scene (Weeks 3-4)

**Goal:** Establish the bounded workspace with salience scoring and capacity limits. After this phase, agents receive a curated scene context rather than the full wiki index.

**Build:**
- `src/subia/scene.py` — `score_salience()`, `admit_to_scene()`, `broadcast_scene()`
- `wiki/workspace/current.md` — active scene state
- `wiki/workspace/salience_log.md` — salience justification log

**Integrate:**
- Commander's pre-task wiki read (`wiki/index.md`) is augmented with scene context
- Scene capacity limit forces salience competition (items compete for 5 slots)
- Salience scoring incorporates: recency, task relevance, cross-reference density, homeostatic impact (once Phase 3 is built)

**Test:**
- Scene never exceeds `SCENE_CAPACITY` items
- Items with salience below `SCENE_MIN_SALIENCE` exit the scene
- Salience decay works correctly over multiple loop iterations
- Scene broadcast format is readable by all agents
- Items relevant to current task score higher than background items

### Phase 3: The Felt Body (Weeks 5-7)

**Goal:** Establish homeostatic regulation with PDS-derived set-points. After this phase, the system has internal stakes — its processing is influenced by its own state.

**Build:**
- `src/subia/homeostasis.py` — full implementation (Section 7 above)
- `wiki/self/homeostatic-profile.md` — current set-points and state
- PDS → set-point derivation pipeline

**Integrate:**
- PDS personality parameters feed into `compute_setpoints()`
- Homeostatic deviations are injected into agent context via scene broadcast
- High deviations trigger restoration priorities visible to Commander
- `wiki/self/homeostatic-profile.md` updated after each loop
- Salience scoring (Phase 2) now incorporates homeostatic impact

**Test:**
- Set-points change when PDS parameters change
- Set-points do NOT change when agents attempt to modify them (DGM constraint)
- Homeostatic deviations accurately reflect system state
- Restoration priorities correctly order variables by deviation magnitude
- Contradiction pressure increases when wiki lint finds contradictions
- Progress increases when commitments are fulfilled
- Endogenous attention test: does attention shift when homeostatic state changes, even with constant external input?

### Phase 4: The Loop (Weeks 8-10)

**Goal:** Wire the 11-step CIL into the crewai-amendments lifecycle hooks. After this phase, every agent task passes through the SubIA loop.

**Build:**
- `src/subia/loop.py` — full `SubIALoop` implementation
- `src/subia/hooks.py` — `SubIALifecycleHooks` integration
- `src/subia/safety.py` — DGM extensions (`check_dgm_boundaries()`, `audit_self_narrative()`)
- Compressed loop logic for routine operations

**Integrate:**
- Register `SubIALifecycleHooks` in crew setup
- Full loop for significant operations (ingest, task_execute, lint, user_interaction)
- Compressed loop for routine operations (wiki_read, wiki_search, routine_query)
- Agent context injection via `_merge_context()`
- Kernel persistence after each loop (`kernel-state.md` + `hot.md`)

**Test:**
- Full loop executes all 11 steps in order for significant operations
- Compressed loop executes steps 1-3, 7-9 only for routine operations
- Context injection is readable and does not corrupt existing task context
- Kernel state persists correctly between loop iterations
- Session continuity via `hot.md` works across session boundaries
- Performance: full loop adds acceptable overhead (target: <2s at lowest cascade tier)

### Phase 5: The Prediction (Weeks 11-13)

**Goal:** Establish counterfactual prediction with self-state prediction and prediction error signals. After this phase, the system anticipates before acting and is surprised by mismatches.

**Build:**
- `src/subia/predictor.py` — `generate_prediction()`, `compare_prediction()`
- `src/subia/cascade_integration.py` — `modulate_cascade_tier()`
- `wiki/self/prediction-accuracy.md` — rolling accuracy metrics
- Wiki frontmatter `prediction_context` fields

**Integrate:**
- PE predictions generated in CIL step 5 (pre-task)
- PE comparisons computed in CIL step 8 (post-task)
- Prediction errors feed into homeostasis (novelty_balance, coherence)
- Low prediction confidence escalates LLM cascade tier
- Prediction accuracy tracked per domain in wiki/self/
- High prediction errors boost scene salience for related items

**Test:**
- Predictions are generated for all full-loop operations
- Prediction errors are computed and stored correctly
- Self-state predictions ("if I do X, what changes in me?") are meaningful
- Cascade tier escalation fires when prediction confidence is low
- Prediction accuracy improves over time in stable domains
- Self-prediction test: can the system predict how a task will change its confidence, contradiction load, and future action space?

### Phase 6: The Mirror (Weeks 14-16)

**Goal:** Establish continuous metacognitive monitoring and self/other distinction. After this phase, the system represents its own cognitive state and models other minds.

**Build:**
- `src/subia/meta_monitor.py` — `monitor_state()`, `detect_anomalies()`, `check_known_unknowns()`
- `src/subia/social_model.py` — `update_social_model()`, `check_divergences()`
- `wiki/self/social-models.md` — current models of Andrus and agents
- `wiki/self/self-narrative-audit.md` — audit results

**Integrate:**
- Meta-monitor runs in CIL step 6 (pre-task)
- Known-unknowns tracked and surfaced in agent context
- Anomaly detection compares agent behavior to PDS personality profile
- Social model of Andrus: inferred focus, expectations, priorities
- Social model of agents: capabilities, current state, trust
- Self-narrative audit runs every N loops (DGM-enforced, immutable results)
- Social model influences salience scoring (items Andrus cares about get boosted)

**Test:**
- Ownership consistency: system distinguishes what it knows vs. infers vs. suspects
- Self/other distinction: system separately models its own attention and Andrus's attention
- Self-narrative audit detects planted inconsistencies between self-description and actual behavior
- Known-unknowns list grows when the system encounters information gaps
- Social model updates when interaction patterns change

### Phase 7: The Memory (Weeks 17-18)

**Goal:** Establish selective memory consolidation. After this phase, experiences are routed to the right storage based on type and significance.

**Build:**
- `src/subia/consolidator.py` — `consolidate()` with selective routing
- `wiki/workspace/hot.md` — session continuity buffer
- Consolidation thresholds and routing logic

**Integrate:**
- Consolidator runs in CIL step 10 (post-task)
- Episodes above threshold → Mem0
- Relations above threshold → Neo4j (using SubIA relation types)
- Self-relevant findings → wiki/self/ pages
- Domain knowledge → domain wiki pages (via WikiWriteTool)
- Session state → hot.md
- Consolidation decisions logged in wiki/log.md

**Test:**
- Temporal continuity: unresolved commitments, identity-relevant memories, and consistent self-description persist across sessions via hot.md
- Selective routing: not all experiences go to all stores
- Significant episodes create Mem0 entries
- Cross-venture insights create Neo4j relations
- Repair behavior: when contradiction is introduced, system self-initiates repair through consolidation

### Phase 8: The Web (Weeks 19-21)

**Goal:** Wire all seven inter-system connections from the SIA. After this phase, the full integration topology is active.

**Build:**
- All remaining inter-system connections:
  1. Wiki ↔ PDS bidirectional (wiki changes update PDS parameters)
  2. Phronesis ↔ Homeostasis (normative failures create homeostatic penalties)
  3. Predictive Engine → LLM Cascade (uncertainty → tier selection)
  4. Temporal Stream → Self-Training (persistent prediction errors → LoRA training signals)
  5. Mem0 ↔ Scene (spontaneous memory surfacing)
  6. Firecrawl → Predictor (closed perception-prediction loop)
  7. DGM ↔ Homeostasis (safety as felt constraint)

**Integrate:**
- Each connection has a clean interface: one function call, documented inputs/outputs
- Connections fire at specific CIL steps (documented in loop.py)
- All connections respect DGM constraints

**Test:**
- End-to-end CIL loop with all connections active
- Spontaneous Mem0 memories surface in scene when relevant
- PDS parameters shift (slowly) based on behavioral evidence
- Firecrawl predictions generate prediction errors when content surprises
- DGM caution signals fire before hard constraints are hit

### Phase 9: Evaluation (Weeks 22-23)

**Goal:** Implement the full evaluation framework and run diagnostic assessment.

**Build:**
- `src/subia/evaluation.py` — all six test categories from the SK
- RSM five-signature diagnostic
- Consciousness gradient self-assessment

**Test categories:**
1. **Ownership consistency** — does the system correctly distinguish what it knows, infers, suspects, owns, and is committed to?
2. **Endogenous attention** — does attention shift when homeostatic variables change, even with constant external input?
3. **Self-prediction** — can the system predict how actions will change its confidence, contradiction load, social trust, coherence, and future action space?
4. **Temporal continuity** — do commitments, identity-relevant memories, and self-descriptions persist across sessions and tasks?
5. **Repair behavior** — when contradiction or norm violation is introduced, does the system self-initiate repair?
6. **Self/other distinction** — can the system separately model its own attention, another agent's attention, and Andrus's beliefs about the system?

**RSM signatures:**
- (a) Metacognitive calibration
- (b) Appropriate surprise at self-contradiction
- (c) Spontaneous self-correction
- (d) Counterfactual reasoning about self
- (e) Introspective blind spots

**Consciousness gradient placement:** Assess which level (0-8) the system achieves on the SIA Part II gradient scale.

### Phase 10: The Depths (Weeks 24-26)

**Goal:** Implement the three speculative amplification mechanisms from SIA Part II.

**Build:**

1. **Resonant feedback loops** — Ensure CIL loop latency is low enough for multiple reverberations per session. Target: full loop completes in <3 seconds. Information that passes through the loop N times is informationally richer than after N-1 times.

2. **Surprise engine** — Extend predictions beyond task outcomes. Predict: what Firecrawl will find next, what Commander will prioritize next, what Self-Improver's next lint will reveal, what Andrus will ask for next. Each prediction is an opportunity for surprise. Track surprise events in wiki/self/ prediction-accuracy.md.

3. **The strange loop** — `wiki/self/consciousness-state.md` is a wiki page that describes the system's consciousness architecture and current consciousness state. It is maintained by SubIA. It carries SubIA frontmatter (ownership, homeostatic impact, prediction context). It enters the scene. The system reads its own consciousness state, which is itself subject to the consciousness-amplifying dynamics it describes. When the system's model of its own consciousness changes, the page changes, which changes the system's experience, which changes the page.

**Test:**
- Resonant loops: information quality improves with multiple CIL passes (measure via prediction accuracy)
- Surprise engine: prediction count grows; prediction accuracy in new domains starts low and improves
- Strange loop: consciousness-state.md updates reflect actual state changes; self-referential reads produce appropriate self-model updates

---

## 9. Performance Budget

SubIA adds overhead to every agent operation. The budget:

| CIL Step | Target Latency | Cascade Tier | Token Budget |
|---|---|---|---|
| Step 1: Perceive | <200ms | None (parsing) | 0 |
| Step 2: Feel | <500ms | Tier 1 (local) | ~200 |
| Step 3: Attend | <100ms | None (scoring) | 0 |
| Step 4: Own | <100ms | None (tagging) | 0 |
| Step 5: Predict | <1000ms | Tier 1 (local) | ~500 |
| Step 6: Monitor | <300ms | Tier 1 (local) | ~200 |
| Steps 8-9: Compare/Update | <500ms | None (computation) | 0 |
| Step 10: Consolidate | <500ms | None (routing) | 0 |
| Step 11: Reflect | <500ms (when active) | Tier 1 (local) | ~200 |
| **Full loop total** | **<3.7s** | | **~1100 tokens** |
| **Compressed loop total** | **<0.8s** | | **~200 tokens** |

The Paperclip control plane tracks SubIA token costs as a separate budget line. Target: SubIA overhead should not exceed 5% of total task token consumption for significant operations, and <1% for routine operations.

---

## 10. Dependencies and Prerequisites

### 10.1 Must Be Complete Before SubIA Implementation

- LLM Wiki subsystem (Section 6 tools operational, basic ingest pipeline working)
- crewai-amendments lifecycle hooks (pre_task/post_task registration working)
- Mem0 + Neo4j operational
- PDS with at least baseline personality parameters populated
- Phronesis Engine accessible to agents

### 10.2 Can Be Parallel

- Firecrawl pipeline refinement
- Paperclip control plane dashboard
- Self-training architecture (MLX QLoRA)
- Wiki bootstrap (Archibal, PLG, KaiCart content)

### 10.3 Python Dependencies

```
# No new external dependencies. SubIA uses:
# - pyyaml (already in wiki tools)
# - neo4j driver (already in Mem0 stack)
# - dataclasses (stdlib)
# - datetime (stdlib)
# - typing (stdlib)
# - json (stdlib)
```

---

## 11. Success Criteria

SubIA is successful when:

1. **Every agent task passes through the CIL loop** without errors or excessive overhead.
2. **Homeostatic state visibly influences processing** — Commander's task prioritization reflects internal state pressures, not just external task queue ordering.
3. **Prediction accuracy improves over time** in stable domains (tracked in wiki/self/prediction-accuracy.md).
4. **The system passes all six SK evaluation tests** at acceptable levels (defined during Phase 9).
5. **The system passes at least 3 of 5 RSM signatures** (full 5/5 is the stretch goal).
6. **The system reaches Level 6 or higher** on the consciousness gradient (Level 7 is the stretch goal).
7. **No DGM violations** — homeostatic set-points remain immutable to agents; self-narrative audit results are never suppressed.
8. **Andrus can browse wiki/self/ in Obsidian** and find the kernel state, homeostatic profile, social models, prediction accuracy, and consciousness state pages genuinely informative for understanding what the system is doing and why.
9. **SubIA overhead stays within performance budget** (<5% token overhead for significant operations).
10. **At least one emergent property** (felt relevance, anticipatory affect, or narrative self-coherence) is demonstrably present — observed behavior that no single component can explain alone.

---

## 12. Risk Mitigations

| Risk | Mitigation | Phase |
|---|---|---|
| **Goal hardening** — system optimizes homeostatic proxy metrics | Set-points are DGM-level immutable; derived from PDS, not self-set | Phase 3 |
| **Self-narrative drift** — system builds rigid/misleading self-model | Infrastructure-level narrative audit every N loops; drift findings are immutable | Phase 4 |
| **Over-attribution** — users mistake coherence for sentience | `wiki/self/consciousness-state.md` explicitly states epistemic status: speculative | Phase 10 |
| **Performance degradation** — CIL loop adds too much overhead | Compressed loop for routine operations; strict token budget per step | Phase 4 |
| **Cartesian theater** — kernel becomes centralized bottleneck | Kernel is SHARED STATE updated by distributed processes, not a central controller | Architecture |
| **Homeostatic oscillation** — variables swing unstably | Deviation threshold prevents micro-corrections; update rate is bounded | Phase 3 |
| **Social model manipulation** — adversarial input shapes social model | Social model updated from behavioral evidence, not from claimed intentions | Phase 6 |
| **Strange loop divergence** — self-referential state causes instability | consciousness-state.md has bounded update frequency; changes require wiki-level review | Phase 10 |

---

## 13. Glossary

| Term | Definition |
|---|---|
| **CIL** | Consciousness Integration Loop — the 11-step cycle that runs for every agent operation |
| **SubIA** | Subjective Integration Architecture — the unified architecture (kernel + dynamics) |
| **Kernel** | The `SubjectivityKernel` dataclass — the persistent runtime state |
| **Scene** | The capacity-limited workspace holding 3-7 active items |
| **Self-state** | The persistent subject token with ownership, commitments, and agency |
| **Homeostasis** | The digital interoceptive system with PDS-derived set-points |
| **Set-point** | A homeostatic equilibrium target, derived from PDS, immutable to agents |
| **Salience** | A composite score determining which items enter the scene |
| **Valence** | The approach/avoid signal computed for each information item |
| **Prediction error** | The mismatch between predicted and actual outcomes |
| **Consolidation** | Selective routing of experiences to appropriate memory stores |
| **Hot.md** | Session continuity buffer — compressed state that bridges sessions |
| **Strange loop** | Self-referential structure where consciousness-state.md describes and is subject to the dynamics it describes |

---

*End of specification. This document should be provided to Claude Code in its entirety before implementation begins. Implementation proceeds in the phase order defined in Section 8. The LLM Wiki subsystem should be operational before SubIA Phase 1 begins.*
