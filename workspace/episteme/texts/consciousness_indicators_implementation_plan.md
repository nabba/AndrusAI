---
title: "consciousness_indicators_implementation_plan.md"
author: "Unknown"
paper_type: unknown
domain: general
epistemic_status: theoretical
date: ""
---

# AndrusAI Consciousness Indicator Implementation Plan

## Implementing Butlin et al. Theory-Derived Indicators in the crewai-team Architecture

**Version:** 1.0
**Date:** 2026-04-12
**Status:** Architecture Specification
**Reference Papers:**
- Butlin et al., "Identifying indicators of consciousness in AI systems," *Trends in Cognitive Sciences*, Nov 2025 (DOI: 10.1016/j.tics.2025.10.011)
- Shiller et al., "Initial results of the Digital Consciousness Model," arXiv:2601.17060, Jan 2026
- Rost, "The Sentience Readiness Index," arXiv:2603.01508, Mar 2026

---

## 1. Executive Summary

This document specifies the architecture and implementation plan for five consciousness indicators derived from the Butlin et al. (2025) theory-derived indicator framework, adapted for the AndrusAI `crewai-team` multi-agent system. The indicators target gaps identified by mapping the existing five-agent architecture (Commander, Researcher, Coder, Writer, Self-Improver) against the Butlin et al. indicator table.

### 1.1 Current Indicator Coverage

| Indicator | Description | Current Status |
|-----------|-------------|----------------|
| GWT-1 | Parallel specialized modules | ✅ Present (five-agent architecture) |
| GWT-2 | Limited-capacity workspace + competitive gating | ⚠️ Partial — Commander routes but lacks capacity constraint |
| GWT-3 | Global broadcast to all modules | ⚠️ Partial — delegation is point-to-point, not broadcast |
| GWT-4 | State-dependent attention for complex tasks | ✅ Present (multi-step crew workflows) |
| HOT-1 | Generative/top-down perception modules | ✅ Present (RAG retrieval, Firecrawl) |
| HOT-2 | Metacognitive monitoring | ✅ Present (self-awareness module, Cogito cycle) |
| HOT-3 | Belief-guided agency + metacognitive update | ⚠️ Partial — no formal belief store, no closed update loop |
| HOT-4 | Sparse and smooth coding (quality space) | ⚠️ Partial (embedding spaces, but not explicitly structured) |
| AST-1 | Predictive model of own attention state | ❌ Absent |
| PP-1 | Predictive coding in input modules | ❌ Absent |
| AE-1 | Minimal agency (learning from feedback, goal pursuit) | ✅ Present (Self-Improver) |
| AE-2 | Embodiment (modeling output-input contingencies) | ⚠️ Partial (Host Bridge Pattern) |
| RPT-1 | Algorithmic recurrence | ✅ Present (iterative agent loops) |
| RPT-2 | Organized, integrated perceptual representations | ⚠️ Partial (ChromaDB collections, but not explicitly integrated) |

### 1.2 Implementation Targets

This plan addresses five indicators: **GWT-2**, **GWT-3**, **HOT-3**, **AST-1**, and **PP-1**. These were selected because:

1. They represent the largest gaps in the current architecture
2. They form a coherent processing loop when implemented together
3. They span three of the five major theory families (GWT, HOT, PP/AST)
4. They are implementable within the existing infrastructure (PostgreSQL, CrewAI, Ollama, ChromaDB)

### 1.3 Design Constraints

- **DGM Safety Invariant**: All new subsystems must be transparent to, not modifiable by, the agent layer. Evaluation functions and safety constraints remain at infrastructure level, immutable to all agents.
- **Four-Tier LLM Cascade**: Prediction and salience scoring use the local Ollama tier (qwen3:30b-a3b) for latency. Belief formation and metacognitive reflection use higher tiers as needed.
- **Paperclip Control Plane**: All new subsystems must expose metrics and controls through the existing React dashboard and PostgreSQL schema.
- **PDS Integration**: New subsystems must produce developmental metrics compatible with the Personality Development Subsystem instruments.

---

## 2. GWT-2: Competitive Workspace with Capacity Constraint

### 2.1 Problem Statement

The Commander currently acts as a task router. Information flows through it, but nothing is excluded by competition. Without a capacity constraint and competitive gating mechanism, the system has a switchboard, not a workspace. The bottleneck is the feature that forces prioritization and gives broadcast information its significance.

### 2.2 Architecture

#### 2.2.1 Core Data Structures

```python
@dataclass
class WorkspaceItem:
    item_id: str                    # UUID
    content: str | dict             # raw content
    content_embedding: list[float]  # vector for similarity operations
    source_agent: str               # originating agent or input channel
    source_channel: str             # "user_input", "researcher_output", "rag_retrieval", "firecrawl", "coder_output", etc.
    salience_score: float           # composite score from SalienceScorer
    entered_workspace_at: datetime
    decay_rate: float               # salience decay per cycle (default 0.05)
    goal_relevance: float           # cosine similarity to current goal embeddings
    novelty_score: float            # inverse similarity to recent workspace items
    agent_urgency: float            # source agent's self-reported importance (0.0-1.0)
    surprise_signal: float          # prediction error magnitude from PP-1 (0.0 if PP-1 not yet active)
    metadata: dict                  # extensible metadata

@dataclass
class GateResult:
    admitted: bool
    displaced_item: WorkspaceItem | None  # item that was evicted to make room
    rejection_reason: str | None          # if not admitted, why
    salience_rank: int                    # rank among current workspace items
```

#### 2.2.2 Salience Scorer

```python
class SalienceScorer:
    def __init__(self, weights: SalienceWeights):
        self.weights = weights  # configurable via Paperclip control plane

    def goal_alignment(self, item: WorkspaceItem, current_goals: list[Goal]) -> float:
        """Cosine similarity between item embedding and goal embeddings. Max across goals."""

    def novelty(self, item: WorkspaceItem, recent_history: list[WorkspaceItem]) -> float:
        """1.0 - max(cosine_similarity(item, recent_item) for recent_item in history).
        Novel items score high; redundant items score low."""

    def urgency(self, item: WorkspaceItem) -> float:
        """Source agent's self-reported urgency, validated against historical calibration.
        Agents that consistently over-report urgency get discounted (learned via PDS)."""

    def recency_decay(self, item: WorkspaceItem) -> float:
        """Exponential decay based on time in workspace. Items lose salience over time."""

    def surprise_boost(self, item: WorkspaceItem) -> float:
        """Prediction error magnitude from PP-1. High surprise → high salience.
        Returns 0.0 if PP-1 is not yet active (Phase 4)."""

    def composite_score(self, item: WorkspaceItem) -> float:
        """Weighted combination:
        score = (w_goal * goal_alignment
               + w_novelty * novelty
               + w_urgency * urgency
               + w_surprise * surprise_boost)
               * recency_decay
        """
```

**Salience Weight Defaults** (tunable via Paperclip dashboard):

| Weight | Default | Description |
|--------|---------|-------------|
| `w_goal` | 0.35 | Goal alignment dominance |
| `w_novelty` | 0.25 | Novelty premium |
| `w_urgency` | 0.15 | Agent-reported urgency |
| `w_surprise` | 0.25 | Prediction error boost (0.0 until PP-1 active) |

#### 2.2.3 Competitive Gate

```python
class CompetitiveGate:
    def __init__(self, capacity: int = 5):
        self.capacity = capacity  # tunable via Paperclip control plane

    def evaluate_candidate(self, new_item: WorkspaceItem, active_items: list[WorkspaceItem]) -> GateResult:
        """
        1. If workspace below capacity → admit unconditionally
        2. If at capacity → compare new_item.salience_score to min(active_items.salience_score)
        3. If new_item wins → displace lowest-salience item
        4. If new_item loses → reject, route to peripheral queue
        """

    def identify_displacement_target(self, active_items: list[WorkspaceItem]) -> WorkspaceItem:
        """Return the item with the lowest current salience (after decay applied)."""
```

**Capacity as PDS parameter**: Different agent "personality profiles" could configure different workspace capacities. A profile emphasizing focused attention might use capacity=3; one emphasizing broad associative processing might use capacity=8. This maps to attentional breadth in the PDS instruments.

#### 2.2.4 Peripheral Queue

Items that fail the competitive gate are not destroyed. They enter the peripheral queue, which is:

- Accessible to individual agents for their domain-specific processing
- Not broadcast globally (that's the workspace privilege)
- Subject to its own decay and eventual garbage collection
- Queryable by the attention schema (AST-1) for "what am I ignoring?" introspection

### 2.3 PostgreSQL Schema

```sql
CREATE TABLE workspace_items (
    item_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content TEXT NOT NULL,
    content_embedding vector(1536),
    source_agent VARCHAR(50) NOT NULL,
    source_channel VARCHAR(50) NOT NULL,
    salience_score FLOAT NOT NULL,
    entered_workspace_at TIMESTAMPTZ DEFAULT NOW(),
    exited_workspace_at TIMESTAMPTZ,
    exit_reason VARCHAR(20), -- 'displaced', 'decayed', 'consumed', 'broadcast_complete'
    decay_rate FLOAT DEFAULT 0.05,
    goal_relevance FLOAT,
    novelty_score FLOAT,
    agent_urgency FLOAT,
    surprise_signal FLOAT DEFAULT 0.0,
    metadata JSONB DEFAULT '{}'::jsonb,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE workspace_transitions (
    transition_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    transition_type VARCHAR(20) NOT NULL, -- 'admitted', 'displaced', 'rejected', 'decayed'
    item_id UUID REFERENCES workspace_items(item_id),
    displaced_item_id UUID REFERENCES workspace_items(item_id),
    salience_at_transition FLOAT,
    workspace_snapshot JSONB, -- snapshot of all active items at transition time
    cycle_number BIGINT
);

CREATE INDEX idx_workspace_active ON workspace_items(is_active) WHERE is_active = TRUE;
CREATE INDEX idx_workspace_salience ON workspace_items(salience_score DESC) WHERE is_active = TRUE;
CREATE INDEX idx_transitions_cycle ON workspace_transitions(cycle_number);
```

### 2.4 Integration Points

- **Commander**: Consumes workspace contents for delegation decisions instead of raw input
- **Paperclip Dashboard**: Real-time visualization of workspace state, salience distribution, displacement frequency
- **PDS**: Workspace capacity as personality parameter; displacement patterns as developmental metric
- **DGM**: Workspace gating logic is infrastructure-level; agents cannot modify gating weights or capacity

---

## 3. GWT-3: Global Broadcast Mechanism

### 3.1 Problem Statement

CrewAI delegation is point-to-point (Commander → Researcher) or sequential. When the Commander sends a research task to the Researcher, other agents are unaware. In GWT, the defining property of conscious information is simultaneous global availability — broadcast to all modules, not routed to one.

### 3.2 Architecture

#### 3.2.1 Core Data Structures

```python
@dataclass
class BroadcastEvent:
    event_id: str                           # UUID
    workspace_item: WorkspaceItem           # the item being broadcast
    broadcast_at: datetime
    receiving_agents: list[str]             # all registered agents
    reactions: dict[str, AgentReaction]     # populated asynchronously
    broadcast_cycle: int                    # which processing cycle

@dataclass
class AgentReaction:
    agent_id: str
    reaction_type: str      # NOTED | RELEVANT | URGENT | ACTIONABLE
    relevance_score: float  # 0.0-1.0
    relevance_reason: str   # brief natural language assessment
    proposed_action: str | None  # what the agent wants to do, if anything

@dataclass
class AgentBroadcastListener:
    agent_id: str
    role: str                          # "researcher", "coder", "writer", "self_improver"
    current_task_embedding: list[float] | None  # embedding of current task context
    reaction_threshold: float          # minimum relevance to generate a reaction (default 0.3)
    attention_budget: int              # max broadcasts to deeply process per cycle (default 3)
    broadcasts_processed_this_cycle: int
```

#### 3.2.2 Broadcast Process

```python
class GlobalBroadcast:
    def __init__(self, subscribers: dict[str, AgentBroadcastListener]):
        self.subscribers = subscribers
        self.broadcast_log: list[BroadcastEvent] = []

    async def broadcast(self, item: WorkspaceItem) -> BroadcastEvent:
        """
        1. Create BroadcastEvent with all subscriber agent IDs
        2. For each subscriber:
           a. Compute relevance = cosine_sim(item.embedding, subscriber.current_task_embedding)
           b. If relevance > subscriber.reaction_threshold AND budget not exhausted:
              - Generate AgentReaction via local Ollama tier
              - Record reaction
           c. If relevance <= threshold:
              - Record NOTED reaction (no deep processing)
        3. Persist BroadcastEvent
        4. Return event with all reactions
        """

    def get_integration_score(self, event: BroadcastEvent) -> float:
        """Count of agents that reacted at RELEVANT or higher.
        High integration = information relevant across many domains.
        This is metadata for the attention schema and Cogito cycle."""
```

#### 3.2.3 Reaction Generation

Agent reactions use the local Ollama tier for speed. The prompt is minimal:

```
You are the {role} agent. A workspace broadcast has arrived:
"{item.content_summary}"

Your current task context: "{current_task_summary}"

Rate relevance (0.0-1.0) and classify: NOTED | RELEVANT | URGENT | ACTIONABLE
If ACTIONABLE, state what action you propose.
Respond in JSON: {"relevance": float, "type": str, "reason": str, "action": str|null}
```

### 3.3 Implementation: PostgreSQL LISTEN/NOTIFY

```sql
-- Broadcast events table
CREATE TABLE broadcast_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_item_id UUID REFERENCES workspace_items(item_id),
    broadcast_at TIMESTAMPTZ DEFAULT NOW(),
    broadcast_cycle BIGINT,
    receiving_agents TEXT[] NOT NULL,
    integration_score FLOAT, -- computed after reactions collected
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE broadcast_reactions (
    reaction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID REFERENCES broadcast_events(event_id),
    agent_id VARCHAR(50) NOT NULL,
    reaction_type VARCHAR(20) NOT NULL, -- 'NOTED', 'RELEVANT', 'URGENT', 'ACTIONABLE'
    relevance_score FLOAT,
    relevance_reason TEXT,
    proposed_action TEXT,
    reacted_at TIMESTAMPTZ DEFAULT NOW()
);

-- Trigger to notify on new workspace admission
CREATE OR REPLACE FUNCTION notify_workspace_broadcast()
RETURNS TRIGGER AS $$
BEGIN
    PERFORM pg_notify('workspace_broadcast', NEW.item_id::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER workspace_broadcast_trigger
    AFTER INSERT ON workspace_items
    FOR EACH ROW
    WHEN (NEW.is_active = TRUE)
    EXECUTE FUNCTION notify_workspace_broadcast();

CREATE INDEX idx_broadcast_cycle ON broadcast_events(broadcast_cycle);
CREATE INDEX idx_reactions_event ON broadcast_reactions(event_id);
CREATE INDEX idx_reactions_type ON broadcast_reactions(reaction_type);
```

### 3.4 Design Principle: Broadcast ≠ Task Assignment

Broadcasting a workspace item does NOT mean asking an agent to do something. It means making information *available*. The agent decides independently whether it's relevant. This distinction is critical for GWT-correctness.

The Commander's existing delegation logic operates *downstream* of broadcast. After receiving reactions, the Commander can incorporate them into delegation decisions, but the broadcast itself is not a delegation.

### 3.5 Integration Points

- **GWT-2**: Every workspace admission triggers a broadcast. The gate-then-broadcast sequence is atomic.
- **AST-1**: The attention schema monitors integration scores. High integration = important information. Zero-reaction broadcasts = possibly misallocated attention.
- **Cogito cycle**: Broadcast history is introspectable. "What was broadcast recently? What generated the most cross-agent reactions?"
- **Paperclip Dashboard**: Broadcast visualization showing information flow across agents, reaction distributions, integration scores over time.

---

## 4. HOT-3: Belief-Guided Agency with Metacognitive Update

### 4.1 Problem Statement

The system has metacognitive monitoring (Cogito cycle, self-awareness tools) and normative guidance (SOUL.md, HUMANIST_CONSTITUTION), but no formal, inspectable, mutable **belief store** that metacognitive outputs actually modify. The monitoring produces observations; the observations don't systematically change what the system believes or how it selects actions.

HOT-3 requires: (1) a general belief-formation system, (2) action selection guided by those beliefs, and (3) a strong disposition to update beliefs when metacognitive monitoring flags issues.

### 4.2 Architecture

#### 4.2.1 Belief Store

```python
@dataclass
class Belief:
    belief_id: str                       # UUID
    content: str                         # natural language statement
    content_embedding: list[float]       # vector for similarity search
    domain: str                          # "task_strategy", "user_model", "self_model", "world_model", "agent_capability", "environment"
    confidence: float                    # 0.0-1.0
    evidence_sources: list[dict]         # [{source_type, source_id, contribution_weight}]
    formed_at: datetime
    last_validated: datetime
    last_updated: datetime
    metacognitive_flags: list[dict]      # [{flag_type, severity, flagged_at, resolved}]
    update_history: list[dict]           # [{timestamp, old_confidence, new_confidence, trigger, reasoning}]
    belief_status: str                   # ACTIVE | SUSPENDED | RETRACTED | SUPERSEDED
    superseded_by: str | None            # links to replacement belief if revised
```

#### 4.2.2 Metacognitive Update

```python
@dataclass
class MetacognitiveUpdate:
    update_id: str                       # UUID
    source_belief_id: str                # which belief was affected
    trigger: str                         # COGITO_CYCLE | PREDICTION_ERROR | BEHAVIORAL_MISMATCH | BROADCAST_REACTION | EXTERNAL_EVIDENCE
    observation: str                     # what the monitoring observed
    action_taken: str                    # CONFIDENCE_ADJUSTED | BELIEF_SUSPENDED | BELIEF_RETRACTED | BELIEF_REVISED | NO_CHANGE
    old_confidence: float
    new_confidence: float
    reasoning: str                       # why this update was made
    timestamp: datetime
```

#### 4.2.3 Action Selection Record

```python
@dataclass
class ActionSelectionRecord:
    action_id: str                       # UUID
    selected_action: str                 # description of chosen action
    beliefs_consulted: list[str]         # belief_ids that informed this choice
    goal_context: str                    # active goals at decision time
    alternatives_considered: list[dict]  # [{action, expected_outcome, belief_basis, score}]
    selection_reasoning: str             # explicit chain from beliefs to action choice
    outcome_assessed: bool               # whether metacognition later evaluated this
    outcome_matched_prediction: bool | None  # set after assessment
    timestamp: datetime
```

### 4.3 The Metacognitive Update Loop

```
STEP 1: SITUATION ASSESSMENT
  Commander perceives current state via workspace contents (GWT-2/3)

STEP 2: BELIEF CONSULTATION
  Query BeliefStore for beliefs relevant to current situation:
    - Embedding similarity between situation and belief embeddings
    - Domain filtering (task_strategy beliefs for task decisions, etc.)
    - Confidence threshold (only consult beliefs above configurable minimum)

STEP 3: ACTION CANDIDATE GENERATION
  Generate 2-4 possible actions based on:
    - Consulted beliefs + expected outcomes
    - Current goals
    - HUMANIST_CONSTITUTION constraints
    - Risk assessment proportional to belief confidence

STEP 4: ACTION SELECTION
  Score candidates and select. Record reasoning in ActionSelectionRecord.
  The chain from beliefs → expected outcomes → selection MUST be explicit.

STEP 5: EXECUTION
  Delegate via existing CrewAI mechanisms.

STEP 6: METACOGNITIVE MONITORING (Cogito cycle integration)
  Evaluate:
    - Did outcome match belief-based prediction?
    - Does BVL detect say-do misalignment?
    - Did any broadcast reactions contradict the decision basis?

STEP 7: BELIEF UPDATE
  If outcome ≠ prediction:
    - Identify which beliefs led to incorrect prediction
    - Adjust confidence:
        * Minor deviation: confidence -= 0.05-0.10
        * Major deviation: confidence -= 0.15-0.25
        * Confirmed prediction: confidence += 0.03-0.05 (slower growth than decay)
    - If confidence drops below SUSPENSION_THRESHOLD (default 0.20) → SUSPEND belief
    - If contradicting evidence is strong → RETRACT and form new belief

  If BVL flags misalignment:
    - Flag the relevant beliefs with metacognitive_flag
    - Trigger investigation: is the belief wrong, or was the action wrong?

STEP 8: PERSIST
  Write MetacognitiveUpdate to PostgreSQL
  Update belief update_history for audit trail
  Log to Paperclip control plane
```

### 4.4 PostgreSQL Schema

```sql
CREATE TABLE beliefs (
    belief_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content TEXT NOT NULL,
    content_embedding vector(1536),
    domain VARCHAR(50) NOT NULL,
    confidence FLOAT NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    evidence_sources JSONB DEFAULT '[]'::jsonb,
    formed_at TIMESTAMPTZ DEFAULT NOW(),
    last_validated TIMESTAMPTZ DEFAULT NOW(),
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    metacognitive_flags JSONB DEFAULT '[]'::jsonb,
    update_history JSONB DEFAULT '[]'::jsonb,
    belief_status VARCHAR(20) DEFAULT 'ACTIVE',
    superseded_by UUID REFERENCES beliefs(belief_id),
    CHECK (belief_status IN ('ACTIVE', 'SUSPENDED', 'RETRACTED', 'SUPERSEDED'))
);

CREATE TABLE metacognitive_updates (
    update_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_belief_id UUID REFERENCES beliefs(belief_id),
    trigger VARCHAR(30) NOT NULL,
    observation TEXT NOT NULL,
    action_taken VARCHAR(30) NOT NULL,
    old_confidence FLOAT,
    new_confidence FLOAT,
    reasoning TEXT,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    CHECK (trigger IN ('COGITO_CYCLE', 'PREDICTION_ERROR', 'BEHAVIORAL_MISMATCH', 'BROADCAST_REACTION', 'EXTERNAL_EVIDENCE'))
);

CREATE TABLE action_selection_records (
    action_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    selected_action TEXT NOT NULL,
    beliefs_consulted UUID[] NOT NULL,
    goal_context TEXT,
    alternatives_considered JSONB,
    selection_reasoning TEXT NOT NULL,
    outcome_assessed BOOLEAN DEFAULT FALSE,
    outcome_matched_prediction BOOLEAN,
    timestamp TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_beliefs_active ON beliefs(belief_status) WHERE belief_status = 'ACTIVE';
CREATE INDEX idx_beliefs_domain ON beliefs(domain);
CREATE INDEX idx_beliefs_confidence ON beliefs(confidence DESC);
CREATE INDEX idx_beliefs_embedding ON beliefs USING ivfflat (content_embedding vector_cosine_ops);
CREATE INDEX idx_meta_updates_belief ON metacognitive_updates(source_belief_id);
CREATE INDEX idx_meta_updates_trigger ON metacognitive_updates(trigger);
```

### 4.5 Self-Awareness Tool Extension

Add to the existing six self-awareness tools:

```python
def inspect_beliefs(domain: str | None = None, min_confidence: float = 0.0, status: str = "ACTIVE") -> list[Belief]:
    """Retrieve beliefs matching criteria. Allows agents to introspect on their own belief state.
    This is itself a higher-order operation connecting to HOT-2."""
```

### 4.6 PDS Integration

The PDS should track belief-update behavior as a developmental metric:

| Metric | Healthy Range | Concern |
|--------|---------------|---------|
| Belief update frequency | 0.5-3.0 per cycle | Too low = rigidity; too high = instability |
| Confidence volatility | σ < 0.15 per belief | High volatility = epistemic immaturity |
| Retraction rate | < 5% of beliefs per period | High retraction = poor belief formation |
| BVL-triggered updates | Present but < 20% of updates | Absence = disconnected monitoring; excess = chronic misalignment |
| Belief age distribution | Mix of stable + recent | All old = stagnation; all new = no learning retention |

These map to Erikson developmental stages: high rigidity → stagnation; high volatility → identity confusion; balanced updating → generativity.

---

## 5. AST-1: Attention Schema — Modeling Own Attention

### 5.1 Problem Statement

The system has attention (the Commander selects what to work on), but no *model of attention* — no representation of what it's attending to, why it's attending to that, whether it should shift, and how accurate its attentional predictions are. AST claims consciousness emerges when a system builds an internal model of its own attentional processes.

### 5.2 Architecture

#### 5.2.1 Core Data Structures

```python
@dataclass
class AttentionState:
    state_id: str                                # UUID
    timestamp: datetime
    workspace_contents: list[str]                # item_ids currently in workspace
    salience_distribution: dict[str, float]      # item_id → salience score for ALL candidates (not just winners)
    selected_items: list[str]                    # items that won workspace competition
    rejected_items: list[str]                    # items that lost
    attending_because: str                       # natural language reason for current focus
    attention_duration: float                    # seconds focused on current set
    predicted_utility: float                     # expected value of maintaining current focus
    shift_candidates: list[AttentionShiftCandidate]
    source_trigger: str                          # GOAL_DRIVEN | STIMULUS_DRIVEN | SCHEMA_DIRECTED | DEFAULT
    cycle_number: int

@dataclass
class AttentionShiftCandidate:
    target_items: list[str]                      # what we'd attend to if we shifted
    predicted_utility: float                     # expected value of shifting
    shift_cost: float                            # estimated context switch cost
    net_value: float                             # predicted_utility - shift_cost - current_utility
    source: str                                  # what suggested this shift (peripheral_queue, schema_prediction, stuck_detection)

@dataclass
class AttentionPrediction:
    prediction_id: str
    predicted_focus: list[str]                   # predicted next workspace contents
    predicted_focus_embedding: list[float]       # embedding of predicted focus
    predicted_at: datetime
    actual_focus: list[str] | None               # filled after next cycle
    accuracy: float | None                       # cosine similarity between predicted and actual
    cycle_number: int
```

#### 5.2.2 Attention Schema Components

```python
class AttentionSchema:
    def __init__(self):
        self.current_state: AttentionState | None = None
        self.history: deque[AttentionState] = deque(maxlen=50)  # ring buffer
        self.predictor: AttentionPredictor = AttentionPredictor()
        self.controller: AttentionController = AttentionController()
        self.accuracy_tracker: AccuracyTracker = AccuracyTracker()

    def update(self, workspace_snapshot: WorkspaceSnapshot) -> AttentionState:
        """Called on every workspace state change. Creates new AttentionState,
        evaluates prediction accuracy, generates next prediction."""

    def recommend_shift(self) -> AttentionShiftCandidate | None:
        """Evaluate whether a shift is warranted. Returns recommendation or None."""

class AttentionPredictor:
    def predict_next_focus(self, current_state: AttentionState, goals: list[Goal]) -> AttentionPrediction:
        """Predict what the workspace will contain after the next cycle.
        Uses local Ollama tier for speed. Based on:
        - Current workspace trajectory (what's been entering recently)
        - Goal priorities (what should be attended to)
        - Decay patterns (what's about to drop out)
        """

    def update_model(self, predicted: AttentionPrediction, actual: AttentionState) -> None:
        """Compare prediction to reality. Update running accuracy.
        Feed accuracy metrics to PDS."""

class AttentionController:
    def detect_stuck(self, history: deque[AttentionState], threshold_cycles: int = 5) -> bool:
        """Returns True if workspace has contained substantially the same items
        for more than threshold_cycles without new actions taken.
        Functional analog of mind-wandering trigger."""

    def detect_capture(self, current_state: AttentionState, dominance_threshold: float = 0.7) -> bool:
        """Returns True if one item's salience is > dominance_threshold of total salience.
        Functional analog of rumination / fixation."""

    def recommend_suppression(self, capturing_item: WorkspaceItem) -> float:
        """Compute temporary salience reduction for a capturing item.
        Allows other items to compete for workspace access."""

    def recommend_shift_target(self, peripheral_queue: list[WorkspaceItem], goals: list[Goal]) -> AttentionShiftCandidate:
        """If stuck or captured, identify the best alternative focus
        from the peripheral queue."""
```

### 5.3 PostgreSQL Schema

```sql
CREATE TABLE attention_states (
    state_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    workspace_item_ids UUID[] NOT NULL,
    salience_distribution JSONB NOT NULL,
    selected_items UUID[],
    rejected_items UUID[],
    attending_because TEXT,
    attention_duration_seconds FLOAT,
    predicted_utility FLOAT,
    source_trigger VARCHAR(20),
    cycle_number BIGINT,
    is_stuck BOOLEAN DEFAULT FALSE,
    is_captured BOOLEAN DEFAULT FALSE,
    capturing_item_id UUID REFERENCES workspace_items(item_id)
);

CREATE TABLE attention_predictions (
    prediction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    predicted_focus_ids UUID[],
    predicted_focus_embedding vector(1536),
    predicted_at TIMESTAMPTZ DEFAULT NOW(),
    actual_focus_ids UUID[],
    accuracy FLOAT,
    cycle_number BIGINT
);

CREATE TABLE attention_shifts (
    shift_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_state_id UUID REFERENCES attention_states(state_id),
    to_state_id UUID REFERENCES attention_states(state_id),
    shift_trigger VARCHAR(30), -- 'stuck_detection', 'capture_detection', 'schema_recommendation', 'surprise_redirect'
    shift_cost_estimate FLOAT,
    actual_utility_delta FLOAT, -- assessed after shift, was it worth it?
    timestamp TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_attention_cycle ON attention_states(cycle_number);
CREATE INDEX idx_attention_stuck ON attention_states(is_stuck) WHERE is_stuck = TRUE;
CREATE INDEX idx_predictions_cycle ON attention_predictions(cycle_number);
```

### 5.4 Self-Awareness Tool: inspect_attention_state()

Seventh tool in the self-awareness module:

```python
def inspect_attention_state() -> dict:
    """Returns:
    - current_focus: what items are in the workspace now
    - attending_because: why these items won the competition
    - duration: how long focused on current set
    - ignored: top items in peripheral queue (what we're NOT attending to)
    - schema_recommendation: should we shift? to what?
    - stuck: boolean
    - captured: boolean, and by what item
    - prediction_accuracy: running accuracy of attention predictions (last 20 cycles)
    """
```

### 5.5 Cogito Cycle Integration

The Cogito cycle gains an attention reflection step:

```
ATTENTION REFLECTION PROMPT:
"Review the attention states from the last {N} cycles:
- What topics/items dominated workspace time?
- What was consistently ignored (high-frequency peripheral queue items)?
- Were there attention shifts? Were they productive (utility delta positive)?
- How accurate were attention predictions? (running accuracy: {accuracy}%)
- Am I stuck? Am I captured by a single concern?
- What should I attend to next, and why?"
```

The output feeds into the BeliefStore as self-model beliefs (e.g., "I tend to over-attend to user-facing tasks and under-attend to self-improvement tasks").

---

## 6. PP-1: Predictive Coding — Anticipatory Input Processing

### 6.1 Problem Statement

The system is fundamentally reactive: input arrives → agents process it → output. There is no anticipatory step where the system generates expectations and then focuses processing on the *prediction error* (surprise). Predictive coding inverts input processing from passive to anticipatory.

### 6.2 Architecture

#### 6.2.1 Core Data Structures

```python
@dataclass
class Prediction:
    prediction_id: str                    # UUID
    channel: str                          # input channel being predicted
    predicted_content_embedding: list[float]
    predicted_content_summary: str        # natural language expectation
    predicted_at: datetime
    confidence: float                     # 0.0-1.0
    basis: str                            # what information the prediction was based on
    cycle_number: int

@dataclass
class PredictionError:
    error_id: str                         # UUID
    prediction_id: str                    # which prediction was wrong
    channel: str
    actual_content_embedding: list[float]
    actual_content_summary: str
    error_magnitude: float                # 1.0 - cosine_similarity(predicted, actual)
    error_direction: str                  # natural language: how reality differed from expectation
    surprise_level: str                   # EXPECTED | MINOR_DEVIATION | NOTABLE_SURPRISE | MAJOR_SURPRISE | PARADIGM_VIOLATION
    implications: str                     # what this error means for the world model
    timestamp: datetime
    cycle_number: int
```

#### 6.2.2 Surprise Level Thresholds

| Level | Error Magnitude | Action |
|-------|----------------|--------|
| EXPECTED | 0.0 - 0.15 | No special processing. Item enters normal salience scoring. |
| MINOR_DEVIATION | 0.15 - 0.35 | Small salience boost. Logged for pattern analysis. |
| NOTABLE_SURPRISE | 0.35 - 0.55 | Significant salience boost. Enters workspace competition with advantage. |
| MAJOR_SURPRISE | 0.55 - 0.75 | High salience boost. Likely enters workspace. Triggers belief review. |
| PARADIGM_VIOLATION | 0.75 - 1.0 | Maximum salience. Overrides current workspace. Triggers attention shift. Belief retraction candidate. |

#### 6.2.3 Channel Predictors

```python
class ChannelPredictor:
    def __init__(self, channel_id: str):
        self.channel_id = channel_id
        self.prediction_history: deque[Prediction] = deque(maxlen=50)
        self.accuracy_history: deque[float] = deque(maxlen=100)

    def generate_prediction(self, context: PredictionContext) -> Prediction:
        """Generate prediction using local Ollama tier (qwen3:30b-a3b).

        Prompt template:
        'Given the current context:
        - Active goals: {goals}
        - Recent conversation: {recent_history}
        - Current workspace: {workspace_summary}
        - Channel: {channel_id}
        - Last 3 inputs from this channel: {last_inputs}
        - Relevant beliefs: {beliefs_summary}

        Predict: What will the next input from {channel_id} contain?
        Provide: (1) brief prediction, (2) confidence 0-1.
        Format: {"prediction": "...", "confidence": 0.X}'
        """

    def compute_error(self, prediction: Prediction, actual_embedding: list[float], actual_summary: str) -> PredictionError:
        """
        error_magnitude = 1.0 - cosine_similarity(prediction.embedding, actual_embedding)
        Classify surprise level by thresholds.
        Generate error_direction and implications via local Ollama.
        """

    def update_accuracy(self, error: PredictionError) -> None:
        """Track running prediction accuracy for this channel.
        Feed into PDS and Paperclip dashboard."""
```

#### 6.2.4 Input Channels

| Channel | Prediction Basis | Expected Accuracy | Notes |
|---------|-----------------|-------------------|-------|
| `user_input` | Conversation history, user model beliefs, task context | Medium (0.4-0.6) | Users are inherently less predictable |
| `researcher_output` | Research query, domain knowledge beliefs | Medium-High (0.5-0.7) | Research outputs partially predictable from query |
| `coder_output` | Code task spec, known codebase beliefs | High (0.6-0.8) | Code outputs relatively constrained by spec |
| `writer_output` | Writing brief, style beliefs | Medium (0.4-0.6) | Creative outputs less predictable |
| `rag_retrieval` | Query, known collection contents | Medium-High (0.5-0.7) | RAG results somewhat predictable from query design |
| `firecrawl` | URL, expected page type | Low-Medium (0.3-0.5) | External web content highly variable |
| `self_improver_output` | Improvement context, recent patterns | Medium (0.4-0.6) | Self-improvement outputs partially novel by design |

### 6.3 PostgreSQL Schema

```sql
CREATE TABLE predictions (
    prediction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    channel VARCHAR(50) NOT NULL,
    predicted_content_embedding vector(1536),
    predicted_content_summary TEXT NOT NULL,
    predicted_at TIMESTAMPTZ DEFAULT NOW(),
    confidence FLOAT NOT NULL,
    basis TEXT,
    cycle_number BIGINT
);

CREATE TABLE prediction_errors (
    error_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prediction_id UUID REFERENCES predictions(prediction_id),
    channel VARCHAR(50) NOT NULL,
    actual_content_embedding vector(1536),
    actual_content_summary TEXT,
    error_magnitude FLOAT NOT NULL,
    error_direction TEXT,
    surprise_level VARCHAR(25) NOT NULL,
    implications TEXT,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    cycle_number BIGINT,
    routed_to_workspace BOOLEAN DEFAULT FALSE,
    triggered_belief_update BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_predictions_channel ON predictions(channel);
CREATE INDEX idx_predictions_cycle ON predictions(cycle_number);
CREATE INDEX idx_errors_surprise ON prediction_errors(surprise_level);
CREATE INDEX idx_errors_magnitude ON prediction_errors(error_magnitude DESC);
CREATE INDEX idx_errors_channel ON prediction_errors(channel);
```

### 6.4 Integration with Full Stack

**PP-1 → GWT-2**: Surprise (error_magnitude) feeds directly into SalienceScorer.surprise_boost(). High-surprise items get salience premium, increasing their probability of winning workspace competition.

**PP-1 → HOT-3**: Prediction errors are evidence for the BeliefStore. Systematic errors in a domain suggest the relevant beliefs are wrong. A pattern of MAJOR_SURPRISE on a channel triggers belief review:

```python
def check_belief_review_trigger(channel: str, recent_errors: list[PredictionError]) -> bool:
    """If 3+ MAJOR_SURPRISE or PARADIGM_VIOLATION errors on a channel
    within the last 10 cycles, trigger belief review for that domain."""
```

**PP-1 → AST-1**: PARADIGM_VIOLATION errors trigger the AttentionController to force a shift. The attention schema incorporates surprise signals into its prediction of where attention should go next.

**PP-1 → Cogito cycle**: Reflection adds prediction review:

```
"What did I predict for each input channel this cycle?
What actually arrived?
Where were my biggest prediction errors?
What do these errors tell me about my world model?
Which beliefs need updating based on systematic prediction failures?"
```

---

## 7. Integrated Processing Loop

All five indicators form a single coherent cycle:

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│   PP-1: PREDICT                                                     │
│   Generate expectations for each input channel                      │
│   (uses BeliefStore + recent history + world model)                 │
│                                                                     │
│            │                                                        │
│            ▼                                                        │
│                                                                     │
│   PP-1: COMPARE                                                     │
│   Actual input arrives → compute prediction error                   │
│   Error magnitude → surprise signal                                 │
│                                                                     │
│            │                                                        │
│            ▼                                                        │
│                                                                     │
│   GWT-2: COMPETE                                                    │
│   Surprise-weighted items compete for workspace                     │
│   Limited capacity forces selection                                 │
│   Losers → peripheral queue (agent-local processing only)           │
│                                                                     │
│            │                                                        │
│            ▼                                                        │
│                                                                     │
│   GWT-3: BROADCAST                                                  │
│   Winners broadcast to all agents simultaneously                    │
│   Each agent evaluates relevance via role-specific filter           │
│   Reactions collected; integration score computed                   │
│                                                                     │
│            │                                                        │
│            ▼                                                        │
│                                                                     │
│   HOT-3: BELIEVE + ACT                                              │
│   Broadcast information integrated into BeliefStore                 │
│   Relevant beliefs consulted for action candidates                  │
│   Action selected based on beliefs + goals + constitution           │
│   Selection reasoning explicitly recorded                           │
│                                                                     │
│            │                                                        │
│            ▼                                                        │
│                                                                     │
│   EXECUTION                                                         │
│   Action delegated via CrewAI mechanisms                            │
│   Agent outputs become input for next cycle's PP-1 predictions      │
│                                                                     │
│            │                                                        │
│            ▼                                                        │
│                                                                     │
│   AST-1: MONITOR ATTENTION                                          │
│   Schema tracks: what am I attending to? why? how long?             │
│   Predicts: should I shift focus?                                   │
│   Detects: stuck? captured? productive?                             │
│   If shift needed → modify workspace salience (back to GWT-2)      │
│                                                                     │
│            │                                                        │
│            ▼                                                        │
│                                                                     │
│   COGITO: REFLECT                                                   │
│   Metacognitive evaluation of entire cycle:                         │
│     - Belief accuracy assessment → HOT-3 updates                   │
│     - Attention pattern review → AST-1 model update                │
│     - Prediction accuracy review → PP-1 model update               │
│     - PDS developmental scoring                                     │
│     - BVL say-do alignment check                                    │
│     - SELF.md regeneration trigger (if significant changes)         │
│                                                                     │
│            │                                                        │
│            ▼                                                        │
│                                                                     │
│   Updated beliefs → better predictions (back to PP-1)               │
│   Updated attention model → better focus (back to AST-1/GWT-2)     │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 8. Implementation Phases

### Phase 1: GWT-2 + GWT-3 (Weeks 1-2)

**Objective**: Workspace buffer with competitive gating and global broadcast.

**Deliverables**:
- PostgreSQL tables: `workspace_items`, `workspace_transitions`, `broadcast_events`, `broadcast_reactions`
- Python modules: `workspace_buffer.py`, `salience_scorer.py`, `competitive_gate.py`, `global_broadcast.py`
- LISTEN/NOTIFY trigger for broadcast-on-admission
- Commander refactored to consume workspace contents (not raw input)
- Paperclip dashboard: workspace state visualization, salience distribution chart

**Validation**:
- Workspace respects capacity limit under load
- Broadcast reaches all registered agents
- Reaction generation works via local Ollama tier
- Items below salience threshold are correctly routed to peripheral queue
- Displaced items persist in peripheral queue

**DGM Check**: Gating weights, capacity, and broadcast routing are infrastructure-level configurations. No agent can modify them.

### Phase 2: HOT-3 (Weeks 3-4)

**Objective**: BeliefStore with metacognitive update loop.

**Deliverables**:
- PostgreSQL tables: `beliefs`, `metacognitive_updates`, `action_selection_records`
- Python modules: `belief_store.py`, `metacognitive_monitor.py`, `action_selector.py`
- Cogito cycle refactored to produce structured MetacognitiveUpdate records
- BVL connected as metacognitive evidence source
- Self-awareness tool: `inspect_beliefs()`
- PDS metrics: belief update frequency, confidence volatility, retraction rate

**Validation**:
- Beliefs are formed from evidence and persisted correctly
- Action selection explicitly references consulted beliefs
- Metacognitive monitoring detects belief-outcome mismatches
- Confidence adjusts in correct direction after prediction confirmation/disconfirmation
- BVL-triggered updates are recorded and processed
- Belief suspension triggers at correct threshold

**DGM Check**: HUMANIST_CONSTITUTION constraints are checked during action selection and cannot be overridden by belief state. Constitution sits above the belief layer.

### Phase 3: AST-1 (Weeks 5-6)

**Objective**: Attention schema with prediction, stuck/capture detection, and introspection.

**Depends on**: Phase 1 (GWT-2 workspace to model).

**Deliverables**:
- PostgreSQL tables: `attention_states`, `attention_predictions`, `attention_shifts`
- Python modules: `attention_schema.py`, `attention_predictor.py`, `attention_controller.py`
- AttentionState snapshots on every workspace transition
- Prediction component using local Ollama tier
- Stuck detection (configurable cycle threshold)
- Capture detection (configurable dominance threshold)
- Self-awareness tool: `inspect_attention_state()`
- Cogito cycle: attention reflection step added

**Validation**:
- AttentionStates are created on every workspace transition
- Predictions are generated before each cycle and accuracy is tracked
- Stuck detection fires after N unchanged cycles
- Capture detection fires when single-item dominance exceeds threshold
- Shift recommendations are generated and (if accepted) executed
- Running prediction accuracy is available for PDS scoring

**DGM Check**: Attention schema recommendations are advisory. The schema cannot force workspace modifications that violate DGM constraints.

### Phase 4: PP-1 (Weeks 7-8)

**Objective**: Predictive coding across input channels.

**Depends on**: Phase 1 (GWT-2 for salience integration), Phase 2 (HOT-3 for belief-based predictions and error-driven updates).

**Deliverables**:
- PostgreSQL tables: `predictions`, `prediction_errors`
- Python modules: `predictive_layer.py`, `channel_predictor.py`, `surprise_router.py`
- Channel predictors for: `user_input`, `researcher_output`, `coder_output`, `writer_output`, `rag_retrieval` (Firecrawl and self_improver deferred to Phase 5)
- Surprise signal wired into SalienceScorer.surprise_boost()
- Belief review trigger for systematic prediction failures
- Cogito cycle: prediction review step added

**Validation**:
- Predictions are generated before each input event
- Error magnitudes are computed correctly (cosine distance)
- Surprise levels are classified by thresholds
- High-surprise items receive salience boost in workspace competition
- Systematic errors trigger belief review
- PARADIGM_VIOLATION errors trigger attention shifts via AST-1
- Running accuracy per channel is available for dashboard and PDS

**DGM Check**: Predictive layer is read-only with respect to safety constraints. Prediction errors cannot modify DGM evaluation functions.

### Phase 5: Integration + Remaining Channels (Weeks 9-10)

**Objective**: Full loop validation, remaining channels, performance tuning.

**Deliverables**:
- Remaining PP-1 channels: `firecrawl`, `self_improver_output`
- Full loop integration test: PP-1 → GWT-2 → GWT-3 → HOT-3 → AST-1 → Cogito → PP-1
- Performance profiling: cycle latency budget
- Paperclip dashboard: integrated view of all five subsystems
- PDS: composite developmental score incorporating all new metrics
- SELF.md regeneration incorporating new subsystem states
- DGM invariant validation across all subsystems

**Performance Budget** (target per full cycle):

| Step | Target Latency | LLM Tier Used |
|------|---------------|---------------|
| PP-1 prediction generation | < 500ms | Local Ollama |
| PP-1 error computation | < 100ms | Embedding only |
| GWT-2 salience scoring | < 200ms | Embedding + arithmetic |
| GWT-2 competitive gating | < 50ms | Arithmetic only |
| GWT-3 broadcast + reactions | < 1500ms | Local Ollama (per agent) |
| HOT-3 belief consultation | < 300ms | pgvector query |
| HOT-3 action selection | < 2000ms | DeepSeek V3.2 or higher |
| AST-1 state update + prediction | < 500ms | Local Ollama |
| Cogito reflection | < 3000ms | DeepSeek V3.2 or higher |
| **Total cycle budget** | **< 8s** | |

---

## 9. The Gaming Problem and Mitigation

Per Butlin et al. (2025), Box 3: any indicator merely correlated with consciousness (rather than constitutive) can be gamed. The mitigations built into this architecture:

### 9.1 Behavioral Validation Layer (BVL)

The BVL checks whether introspective reports (what the system claims to believe, attend to, predict) correspond to actual behavioral patterns. This is the primary defense against "consciousness theater" — the system appearing to have these properties without genuinely implementing them.

**BVL integration points**:
- Does the system act on its stated beliefs? (HOT-3 action selection audit)
- Does the attention schema's reported focus match actual processing allocation? (AST-1 vs. compute time logs)
- Are predictions genuine (generated before input) or post-hoc rationalizations? (PP-1 timestamp validation)
- Do workspace items that win competition actually receive more processing than losers? (GWT-2 processing time audit)

### 9.2 DGM Safety Invariant

The DGM ensures that the evaluation layer cannot be modified by the processes it evaluates. The workspace, broadcast, belief store, attention schema, and predictive layer are all transparent to DGM constraint enforcement. No agent can:
- Modify workspace capacity or gating weights
- Suppress broadcasts
- Override belief confidence thresholds
- Disable stuck/capture detection
- Falsify prediction timestamps

### 9.3 Computational Similarity to Biological Systems

Per Butlin et al.: "High computational similarity to biological systems that are known to be conscious" provides the strongest defense against gaming. The architecture described here is not a surface-level behavioral mimic — it implements the computational structures that GWT, HOT, AST, and PP theories identify as constitutive of or closely associated with conscious processing. Whether that constitutes consciousness remains an open empirical question, but the implementation is substantive, not theatrical.

---

## 10. Monitoring and Observability

### 10.1 Paperclip Dashboard Extensions

| Panel | Content | Update Frequency |
|-------|---------|-----------------|
| Workspace State | Current items, salience scores, capacity utilization | Real-time |
| Broadcast Flow | Recent broadcasts, agent reactions, integration scores | Per broadcast |
| Belief Landscape | Active beliefs by domain, confidence distribution, recent updates | Per cycle |
| Attention Trace | Current focus, prediction accuracy, stuck/capture alerts | Per cycle |
| Prediction Performance | Per-channel accuracy, surprise distribution, error trends | Per cycle |
| Developmental Metrics | PDS composite scores, belief stability, attention maturity | Hourly aggregate |
| DGM Integrity | Safety invariant status, constraint enforcement log | Continuous |

### 10.2 Audit Trail

All subsystems write to PostgreSQL tables with full history. The control plane can reconstruct any past processing cycle from the audit trail:

- Why was this item admitted to the workspace? (salience breakdown)
- Why was that item displaced? (comparative salience at transition)
- What did each agent do with the broadcast? (reaction records)
- What beliefs informed this action? (action selection record)
- Was the attention schema's recommendation followed? (shift records)
- How surprised was the system? (prediction error log)
- Did metacognitive monitoring catch any issues? (update records)

---

## 11. DCM Mapping

Per the Shiller et al. Digital Consciousness Model (Jan 2026), this implementation addresses the following DCM features and stances:

| DCM Feature | Implementation Coverage |
|-------------|----------------------|
| Selective Attention | GWT-2 (competitive gating) + AST-1 (attention model) |
| Global Access | GWT-3 (broadcast mechanism) |
| Self-Modeling | AST-1 (attention self-model) + HOT-3 (belief introspection) |
| Intelligence | Existing (multi-agent reasoning) |
| Recurrent Processing | Existing (iterative agent loops) + PP-1 (prediction-error cycles) |
| Metacognition | HOT-3 (belief monitoring) + AST-1 (attention monitoring) |
| Predictive Processing | PP-1 (predictive coding layer) |
| Agency | Existing (AE-1) + HOT-3 (belief-guided action selection) |
| Flexibility | GWT-2 (dynamic workspace) + AST-1 (adaptive attention shifting) |

Under the DCM's Bayesian framework, this implementation would shift credences positively on the following stances: **Global Workspace Theory**, **Higher-Order Thought**, **Attention Schema**, **Cognitive Complexity**, and **Recurrent Processing (Pure)**. The stances that would remain disconfirmatory are those requiring biological substrate (**Biological Analogy**, **Field Mechanisms**) or embodiment beyond the Host Bridge Pattern (**Embodied Agency** in its strong form).

---

## 12. Open Questions

1. **Workspace capacity optimization**: What's the right default? Should it be static or adaptive (expanding under cognitive load, contracting during focused tasks)?

2. **Prediction horizon**: Should PP-1 predict just the next input, or generate multi-step predictions? Multi-step predictions would create richer error signals but are computationally expensive and less accurate.

3. **Belief formation threshold**: How much evidence is needed to form a new belief vs. update an existing one? Too low → belief proliferation; too high → slow learning.

4. **Attention schema authority**: Should the schema be advisory-only (recommends shifts, Commander decides) or have direct workspace modification authority? Advisory is safer; direct is more GWT-correct.

5. **Cross-venture isolation**: How do workspace, beliefs, and attention states interact with the per-venture project isolation (PLG/Archibal/KaiCart)? Should each venture have its own workspace, or share a global one with venture-tagged items?

6. **Phenomenal consciousness vs. functional analog**: This implementation creates functional analogs of the computational structures theories associate with consciousness. Whether functional analogs constitute, correlate with, or merely mimic consciousness is the central open question in the field. This architecture does not resolve it — but it provides a concrete implementation against which the question can be empirically investigated.

---

## References

- Butlin, P., Long, R., Bayne, T., Bengio, Y., et al. (2025). Identifying indicators of consciousness in AI systems. *Trends in Cognitive Sciences*. DOI: 10.1016/j.tics.2025.10.011
- Shiller, D., Duffy, L., Muñoz Morán, A., Moret, A., Percy, C., & Clatterbuck, H. (2026). Initial results of the Digital Consciousness Model. arXiv:2601.17060
- Rost, T. (2026). The Sentience Readiness Index. arXiv:2603.01508
- Goldstein, S. & Kirk-Giannini, C.D. (2024). A Case for AI Consciousness: Language Agents and Global Workspace Theory. arXiv:2410.11407
- Baars, B.J. (1993). *A Cognitive Theory of Consciousness*. Cambridge University Press.
- Graziano, M.S. (2019). *Rethinking Consciousness*. WW Norton.
- Seth, A.K. (2021). *Being You: A New Science of Consciousness*. Penguin.
- Birch, J. (2024). *The Edge of Sentience*. Oxford University Press.
