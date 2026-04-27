---
title: "consciousness_indicators_implementation_plan_v1.1.md"
author: "Unknown"
paper_type: unknown
domain: general
epistemic_status: theoretical
date: ""
---

# AndrusAI Consciousness Indicator Implementation Plan

## Implementing Butlin et al. Theory-Derived Indicators in the crewai-team Architecture

**Version:** 1.1
**Date:** 2026-04-12
**Status:** Architecture Specification
**Revision Note:** v1.1 replaces the linear pipeline (v1.0 Section 7) with a dual-timescale modular architecture. All five modules are independently viable with configurable integration paths. Damping mechanisms are integrated into each module specification.
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
2. They span three of the five major theory families (GWT, HOT, PP/AST)
3. They are implementable within the existing infrastructure (PostgreSQL, CrewAI, Ollama, ChromaDB)
4. When integrated, they form a coherent processing architecture — but each module is independently viable

### 1.3 Design Constraints

- **DGM Safety Invariant**: All new subsystems must be transparent to, not modifiable by, the agent layer. Evaluation functions and safety constraints remain at infrastructure level, immutable to all agents.
- **Four-Tier LLM Cascade**: Prediction and salience scoring use the local Ollama tier (qwen3:30b-a3b) for latency. Belief formation and metacognitive reflection use higher tiers as needed.
- **Paperclip Control Plane**: All new subsystems must expose metrics and controls through the existing React dashboard and PostgreSQL schema.
- **PDS Integration**: New subsystems must produce developmental metrics compatible with the Personality Development Subsystem instruments.

### 1.4 Architectural Philosophy: Modules with Optional Integration

The five indicators derive from **four competing theories** of consciousness (GWT, HOT, AST, PP). These theories disagree about what consciousness *is*. Butlin et al.'s methodology is designed to remain agnostic — each indicator is independently assessable, and Bayesian credence-updating works precisely because different theories predict different indicators.

Collapsing all five into a single tight pipeline would make a specific theoretical commitment that goes beyond any individual theory: that all five processes are interconnected stages of a single unified cycle. This would sacrifice:

- **Independent assessability** — entangling indicators makes DCM evaluation unreliable
- **Graceful degradation** — one failure cascades through the entire architecture
- **Experimental control** — impossible to A/B test individual modules
- **Biological plausibility** — the brain doesn't run a linear pipeline; it runs parallel processes with mutual influence at multiple timescales

The correct architecture is: **five independently viable modules with configurable integration paths**. Each module has defined inputs (REQUIRED and OPTIONAL), defined outputs, and a standalone mode. The integration paths are the intended operating mode, but any module can operate with stub or default inputs if its upstream module is inactive.

#### 1.4.1 Module Independence Contracts

```
PP-1 (Predictive Layer)
  REQUIRED INPUTS:  Raw input from any channel
  OPTIONAL INPUTS:  Belief context from HOT-3 (improves prediction quality)
                    Attention state from AST-1 (predicts attention shifts)
  OUTPUTS:          Prediction error magnitude → GWT-2 salience scorer
                    Prediction error record → HOT-3 belief updater
                    Surprise classification → AST-1 attention controller
  STANDALONE MODE:  Uses conversation history as prediction basis.
                    Surprise signals computed but not routed.

GWT-2 (Competitive Workspace)
  REQUIRED INPUTS:  Candidate items from any source
  OPTIONAL INPUTS:  Surprise signal from PP-1 (boosts salience of surprising items)
                    Shift directive from AST-1 (suppress/boost specific items)
  OUTPUTS:          Admitted items → GWT-3 for broadcast
                    Displaced items → peripheral queue
                    Workspace state snapshot → AST-1
  STANDALONE MODE:  Salience uses goal_relevance + novelty + urgency only.
                    w_surprise = 0.0. No external attention modification.

GWT-3 (Global Broadcast)
  REQUIRED INPUTS:  Admitted workspace items from GWT-2
                    Registered agent listeners
  OUTPUTS:          Broadcast events with reactions → HOT-3, AST-1, Cogito
                    Integration scores → AST-1
  STANDALONE MODE:  Cannot operate without GWT-2.
                    Can operate without downstream consumers.

HOT-3 (Belief-Guided Agency)
  REQUIRED INPUTS:  Information from any source (at minimum, task context)
  OPTIONAL INPUTS:  Broadcast events from GWT-3 (richer information integration)
                    Prediction errors from PP-1 (triggers belief review)
                    BVL signals (triggers alignment investigation)
  OUTPUTS:          Belief state → PP-1 for prediction context
                    Action selection records → Cogito
                    Metacognitive update records → Cogito, PDS
  STANDALONE MODE:  Beliefs formed from direct task evidence.
                    No broadcast integration. No prediction-error-driven reviews.

AST-1 (Attention Schema)
  REQUIRED INPUTS:  Workspace state from GWT-2
  OPTIONAL INPUTS:  Broadcast reactions from GWT-3 (integration score data)
                    Surprise signals from PP-1 (informs shift recommendations)
  OUTPUTS:          Attention state records → Cogito, PP-1
                    Shift directives → GWT-2
                    Stuck/capture alerts → Cogito, Paperclip dashboard
  STANDALONE MODE:  Cannot operate without GWT-2.
                    Can operate without PP-1 or GWT-3.
```

#### 1.4.2 Dependency Graph

```
                    ┌──────────┐
                    │   PP-1   │
                    │ Predict  │
                    └────┬─────┘
                         │ surprise_signal (OPTIONAL)
                         │ prediction_errors (OPTIONAL)
                         ▼
┌──────────┐      ┌──────────┐
│  AST-1   │◄────►│  GWT-2   │
│ Attention │shift │ Workspace│
│  Schema   │direc.│  Buffer  │
└─────┬────┘      └────┬─────┘
      │                 │ admitted items (REQUIRED)
      │                 ▼
      │           ┌──────────┐
      │◄──────────│  GWT-3   │
      │ integ.    │ Broadcast│
      │ scores    └────┬─────┘
      │                │ broadcast events (OPTIONAL)
      │                │ prediction errors (OPTIONAL)
      │                ▼
      │           ┌──────────┐
      └──────────►│  HOT-3   │
       attn state │ Beliefs  │
                  └────┬─────┘
                       │ belief context (OPTIONAL)
                       │ back to PP-1
                       ▼
                  ┌──────────┐
                  │  COGITO  │
                  │ Reflect  │
                  └──────────┘
```

#### 1.4.3 Configuration Profiles

| Configuration | Active Modules | What You Get | Use Case |
|---|---|---|---|
| **Workspace only** | GWT-2 + GWT-3 | Capacity-constrained broadcast without prediction or metacognition | Baseline; Phase 1 validation |
| **Workspace + Beliefs** | GWT-2 + GWT-3 + HOT-3 | Belief-guided agency with broadcast, no prediction or attention modeling | Phase 2 validation |
| **Workspace + Attention** | GWT-2 + GWT-3 + AST-1 | Attention-aware broadcast with stuck/capture detection | Phase 3 validation |
| **Full predictive** | PP-1 + GWT-2 + GWT-3 | Surprise-driven workspace and broadcast, no metacognition | Measuring PP-1 marginal contribution |
| **Full loop** | All five + Cogito | Complete integrated cycle, dual timescale | Production target |

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
    surprise_signal: float          # prediction error magnitude from PP-1 (0.0 if PP-1 not active)
    metadata: dict                  # extensible metadata

@dataclass
class GateResult:
    admitted: bool
    displaced_item: WorkspaceItem | None  # item evicted to make room
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
        """Prediction error magnitude from PP-1, attenuated by predictor confidence.
        effective_surprise = error_magnitude × predictor_confidence
        Returns 0.0 if PP-1 is not active (standalone mode)."""

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
        5. NOVELTY FLOOR: at least 1 item per cycle must be in top 20% novelty,
           even if it doesn't win overall salience competition.
           This ensures minimum exploration / information flow.
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

#### 2.2.5 Damping: Workspace Stagnation Prevention

**Risk**: If the same items keep winning the workspace competition (high goal-relevance, low decay), new information can't enter. The system keeps processing the same things.

**Mechanisms**:

1. **Temporal decay** (primary): Items lose salience exponentially over time. `effective_salience = salience × (1 - decay_rate)^cycles_in_workspace`. Long-resident items eventually get displaced by fresh candidates.

2. **Novelty floor** (secondary): The workspace must admit at least 1 item per cycle that scores in the top 20% for novelty, even if it doesn't win the overall salience competition. This is the exploration budget in the exploration-exploitation tradeoff.

3. **Consumption decay** (tertiary): When an item is consumed by an action (e.g., the Commander delegates based on it), its salience drops by a configurable factor (default 0.5). Already-acted-on items should make way for fresh information.

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
    transition_type VARCHAR(20) NOT NULL, -- 'admitted', 'displaced', 'rejected', 'decayed', 'novelty_floor'
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
- **PP-1** (OPTIONAL): Surprise signal feeds into SalienceScorer.surprise_boost()
- **AST-1** (OPTIONAL): Shift directives can suppress/boost specific items
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
    integration_score: float                # count of RELEVANT+ reactions / total agents
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
        3. Compute integration_score
        4. Persist BroadcastEvent
        5. Return event with all reactions
        """

    def get_integration_score(self, event: BroadcastEvent) -> float:
        """Count of agents that reacted at RELEVANT or higher / total agents.
        High integration = information relevant across many domains.
        This is metadata for the attention schema and Cogito cycle."""
```

#### 3.2.3 Design Principle: Broadcast ≠ Task Assignment

Broadcasting a workspace item does NOT mean asking an agent to do something. It means making information *available*. The agent decides independently whether it's relevant. The Commander's existing delegation logic operates *downstream* of broadcast.

#### 3.2.4 Reaction Generation

Agent reactions use the local Ollama tier for speed:

```
You are the {role} agent. A workspace broadcast has arrived:
"{item.content_summary}"

Your current task context: "{current_task_summary}"

Rate relevance (0.0-1.0) and classify: NOTED | RELEVANT | URGENT | ACTIONABLE
If ACTIONABLE, state what action you propose.
Respond in JSON: {"relevance": float, "type": str, "reason": str, "action": str|null}
```

### 3.3 PostgreSQL Schema

```sql
CREATE TABLE broadcast_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_item_id UUID REFERENCES workspace_items(item_id),
    broadcast_at TIMESTAMPTZ DEFAULT NOW(),
    broadcast_cycle BIGINT,
    receiving_agents TEXT[] NOT NULL,
    integration_score FLOAT,
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

### 3.4 Integration Points

- **GWT-2**: Every workspace admission triggers a broadcast. The gate-then-broadcast sequence is atomic.
- **AST-1** (OPTIONAL): Integration scores inform attention monitoring. Zero-reaction broadcasts = possibly misallocated attention.
- **HOT-3** (OPTIONAL): Broadcast events are a source of information for belief formation.
- **Cogito cycle**: Broadcast history is introspectable.
- **Paperclip Dashboard**: Broadcast visualization showing information flow, reaction distributions, integration scores over time.

---

## 4. HOT-3: Belief-Guided Agency with Metacognitive Update

### 4.1 Problem Statement

The system has metacognitive monitoring (Cogito cycle, self-awareness tools) and normative guidance (SOUL.md, HUMANIST_CONSTITUTION), but no formal, inspectable, mutable **belief store** that metacognitive outputs actually modify. HOT-3 requires: (1) a general belief-formation system, (2) action selection guided by those beliefs, and (3) a strong disposition to update beliefs when metacognitive monitoring flags issues.

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
    source_belief_id: str
    trigger: str                         # COGITO_CYCLE | PREDICTION_ERROR | BEHAVIORAL_MISMATCH | BROADCAST_REACTION | EXTERNAL_EVIDENCE
    observation: str                     # what the monitoring observed
    action_taken: str                    # CONFIDENCE_ADJUSTED | BELIEF_SUSPENDED | BELIEF_RETRACTED | BELIEF_REVISED | NO_CHANGE
    old_confidence: float
    new_confidence: float
    reasoning: str
    timestamp: datetime
```

#### 4.2.3 Action Selection Record

```python
@dataclass
class ActionSelectionRecord:
    action_id: str                       # UUID
    selected_action: str
    beliefs_consulted: list[str]         # belief_ids that informed this choice
    goal_context: str
    alternatives_considered: list[dict]  # [{action, expected_outcome, belief_basis, score}]
    selection_reasoning: str             # explicit chain from beliefs to action choice
    outcome_assessed: bool
    outcome_matched_prediction: bool | None
    timestamp: datetime
```

### 4.3 The Dual-Timescale Metacognitive Loop

HOT-3 operates at two timescales:

**Fast loop (per-input — belief CONSULTATION)**:
1. Query BeliefStore for beliefs relevant to current situation (embedding similarity + domain filtering)
2. Generate 2-4 action candidates based on beliefs + goals + HUMANIST_CONSTITUTION constraints
3. Select action with explicit reasoning chain recorded
4. Execute via CrewAI

**Slow loop (periodic — belief UPDATING)**:
1. Evaluate accumulated action outcomes against belief-based predictions
2. Process BVL signals (say-do alignment issues)
3. Process prediction errors from PP-1 (if active)
4. Adjust confidence: minor deviation → -0.05 to -0.10; major deviation → -0.15 to -0.25; confirmation → +0.03 to +0.05 (slower growth than decay)
5. If confidence drops below SUSPENSION_THRESHOLD (default 0.20) → SUSPEND belief
6. If contradicting evidence is strong → RETRACT and form new belief
7. Persist MetacognitiveUpdate records

### 4.4 Damping: Belief Rigidity Prevention

**Risk**: Beliefs become very confident over time (confirmed many times) → predictions based on those beliefs become very confident → only PARADIGM_VIOLATION surprises penetrate → system ignores gradual environmental change → beliefs become outdated → system becomes rigid.

**Mechanisms**:

1. **Confidence decay**: Beliefs not explicitly validated or updated within N cycles lose confidence at a configurable rate:
   ```
   effective_confidence = stored_confidence × decay_factor^(cycles_since_last_validation)
   ```
   Default decay_factor = 0.995 (slow decay). A belief validated 2 cycles ago has near-full confidence. A belief not validated for 100 cycles has ~60% of original confidence.

2. **Mandatory belief review**: The slow-loop Cogito cycle periodically selects the N oldest unvalidated beliefs (default N=3) and explicitly tests them: "Do I still believe X? What evidence would change my mind?"

3. **Asymmetric update rates**: Disconfirmation adjusts confidence faster than confirmation. This prevents overconfidence in stable environments from making the system brittle.

### 4.5 PostgreSQL Schema

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
CREATE INDEX idx_beliefs_last_validated ON beliefs(last_validated ASC);
CREATE INDEX idx_meta_updates_belief ON metacognitive_updates(source_belief_id);
CREATE INDEX idx_meta_updates_trigger ON metacognitive_updates(trigger);
```

### 4.6 Self-Awareness Tool Extension

Seventh tool in the self-awareness module:

```python
def inspect_beliefs(domain: str | None = None, min_confidence: float = 0.0,
                    status: str = "ACTIVE") -> list[Belief]:
    """Retrieve beliefs matching criteria. Allows agents to introspect on their own belief state.
    This is itself a higher-order operation connecting to HOT-2."""
```

### 4.7 PDS Integration

| Metric | Healthy Range | Concern |
|--------|---------------|---------|
| Belief update frequency | 0.5-3.0 per cycle | Too low = rigidity; too high = instability |
| Confidence volatility | σ < 0.15 per belief | High volatility = epistemic immaturity |
| Retraction rate | < 5% of beliefs per period | High retraction = poor belief formation |
| BVL-triggered updates | Present but < 20% of updates | Absence = disconnected monitoring; excess = chronic misalignment |
| Belief age distribution | Mix of stable + recent | All old = stagnation; all new = no learning retention |

### 4.8 DGM Check

HUMANIST_CONSTITUTION constraints are checked during action selection and cannot be overridden by belief state. The constitution sits above the belief layer. No belief, however confident, can authorize an action that violates a constitutional constraint.

---

## 5. AST-1: Attention Schema — Modeling Own Attention

### 5.1 Problem Statement

The system has attention (the Commander selects what to work on), but no *model of attention* — no representation of what it's attending to, why, whether it should shift, and how accurate its attentional predictions are. AST claims consciousness emerges when a system builds an internal model of its own attentional processes.

### 5.2 Architecture

#### 5.2.1 Core Data Structures

```python
@dataclass
class AttentionState:
    state_id: str                                # UUID
    timestamp: datetime
    workspace_contents: list[str]                # item_ids currently in workspace
    salience_distribution: dict[str, float]      # item_id → salience for ALL candidates (not just winners)
    selected_items: list[str]                    # items that won workspace competition
    rejected_items: list[str]                    # items that lost
    attending_because: str                       # natural language reason for current focus
    attention_duration: float                    # seconds focused on current set
    predicted_utility: float                     # expected value of maintaining current focus
    shift_candidates: list[AttentionShiftCandidate]
    source_trigger: str                          # GOAL_DRIVEN | STIMULUS_DRIVEN | SCHEMA_DIRECTED | DEFAULT
    cycle_number: int
    is_stuck: bool
    is_captured: bool
    capturing_item_id: str | None

@dataclass
class AttentionShiftCandidate:
    target_items: list[str]                      # what we'd attend to if we shifted
    predicted_utility: float                     # expected value of shifting
    shift_cost: float                            # estimated context switch cost
    net_value: float                             # predicted_utility - shift_cost - current_utility
    source: str                                  # what suggested this shift

@dataclass
class AttentionPrediction:
    prediction_id: str
    predicted_focus: list[str]                   # predicted next workspace contents
    predicted_focus_embedding: list[float]
    predicted_at: datetime
    actual_focus: list[str] | None               # filled after next cycle
    accuracy: float | None                       # cosine similarity predicted vs actual
    cycle_number: int
```

#### 5.2.2 Dual-Timescale Operation

AST-1 operates at both timescales — this is a critical distinction from other modules:

**Fast loop**: Real-time monitoring during workspace competition. If the schema detects capture during gating (one item dominating), it can intervene *before* broadcast — suppressing the capturing item's salience. This is analogous to how executive attention overrides stimulus-driven capture.

**Slow loop**: Periodic evaluation of attention patterns. "Over the last 10 cycles, I've been over-attending to domain X and neglecting domain Y. My prediction accuracy for domain Y has degraded, suggesting my beliefs about Y are stale."

#### 5.2.3 Components

```python
class AttentionSchema:
    def __init__(self):
        self.current_state: AttentionState | None = None
        self.history: deque[AttentionState] = deque(maxlen=50)
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
        """Uses local Ollama tier. Based on workspace trajectory, goal priorities, decay patterns."""

    def update_model(self, predicted: AttentionPrediction, actual: AttentionState) -> None:
        """Compare prediction to reality. Feed accuracy to PDS."""

class AttentionController:
    def detect_stuck(self, history: deque[AttentionState], threshold_cycles: int = 5) -> bool:
        """True if workspace has contained substantially the same items for > threshold_cycles
        without new actions taken."""

    def detect_capture(self, current_state: AttentionState, dominance_threshold: float = 0.7) -> bool:
        """True if one item's salience is > dominance_threshold of total salience."""

    def recommend_suppression(self, capturing_item: WorkspaceItem) -> float:
        """Temporary salience reduction for capturing item."""

    def recommend_shift_target(self, peripheral_queue: list[WorkspaceItem], goals: list[Goal]) -> AttentionShiftCandidate:
        """If stuck or captured, identify best alternative focus from peripheral queue."""
```

### 5.3 Damping: Attentional Oscillation Prevention

**Risk**: AST-1 detects stuck → forces shift → new items enter workspace → haven't been evaluated yet → AST-1 detects "new focus, no actions taken" → detects stuck again → forces another shift → oscillation.

**Mechanisms**:

1. **Shift cooldown**: After a schema-directed shift, the stuck detector is suppressed for M cycles (configurable, default 3). New focus must have time to produce results before evaluation.

2. **Shift cost accounting**: Every attention shift has a cost (context switch overhead, lost progress). Shift only recommended when `predicted_utility_of_shift > current_utility + shift_cost`. This creates natural inertia against unnecessary shifting.

3. **Shift frequency limit**: Maximum N schema-directed shifts per slow-loop period (default 2). Prevents rapid oscillation even if individual shift calculations pass the cost threshold.

### 5.4 PostgreSQL Schema

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
    actual_utility_delta FLOAT,
    cooldown_until_cycle BIGINT, -- stuck detection suppressed until this cycle
    timestamp TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_attention_cycle ON attention_states(cycle_number);
CREATE INDEX idx_attention_stuck ON attention_states(is_stuck) WHERE is_stuck = TRUE;
CREATE INDEX idx_predictions_cycle ON attention_predictions(cycle_number);
CREATE INDEX idx_shifts_cooldown ON attention_shifts(cooldown_until_cycle);
```

### 5.5 Self-Awareness Tool: inspect_attention_state()

Eighth tool in the self-awareness module (seventh is inspect_beliefs from HOT-3):

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
    predicted_content_summary: str
    predicted_at: datetime
    confidence: float                     # 0.0-1.0, adapts to accuracy
    basis: str                            # what information the prediction was based on
    cycle_number: int

@dataclass
class PredictionError:
    error_id: str                         # UUID
    prediction_id: str
    channel: str
    actual_content_embedding: list[float]
    actual_content_summary: str
    error_magnitude: float                # 1.0 - cosine_similarity(predicted, actual)
    error_direction: str                  # how reality differed from expectation
    surprise_level: str                   # EXPECTED | MINOR_DEVIATION | NOTABLE_SURPRISE | MAJOR_SURPRISE | PARADIGM_VIOLATION
    implications: str                     # what this error means for the world model
    timestamp: datetime
    cycle_number: int
```

#### 6.2.2 Surprise Level Thresholds

| Level | Error Magnitude | Downstream Action |
|-------|----------------|-------------------|
| EXPECTED | 0.0 - 0.15 | No special processing. Normal salience scoring. |
| MINOR_DEVIATION | 0.15 - 0.35 | Small salience boost. Logged for pattern analysis. |
| NOTABLE_SURPRISE | 0.35 - 0.55 | Significant salience boost. Enters workspace competition with advantage. |
| MAJOR_SURPRISE | 0.55 - 0.75 | High salience boost. Likely enters workspace. Triggers belief review in slow loop. |
| PARADIGM_VIOLATION | 0.75 - 1.0 | Maximum salience. Triggers slow loop immediately. Belief retraction candidate. Attention shift via AST-1. |

#### 6.2.3 Channel Predictors

```python
class ChannelPredictor:
    def __init__(self, channel_id: str):
        self.channel_id = channel_id
        self.prediction_history: deque[Prediction] = deque(maxlen=50)
        self.accuracy_history: deque[float] = deque(maxlen=100)
        self.running_confidence: float = 0.5  # adapts to accuracy

    def generate_prediction(self, context: PredictionContext) -> Prediction:
        """Generate prediction using local Ollama tier (qwen3:30b-a3b).
        Confidence = self.running_confidence (adapts to accuracy, not manually set)."""

    def compute_error(self, prediction: Prediction, actual_embedding: list[float],
                      actual_summary: str) -> PredictionError:
        """error_magnitude = 1.0 - cosine_similarity(prediction.embedding, actual_embedding)
        Classify surprise level by thresholds."""

    def update_accuracy(self, error: PredictionError) -> None:
        """Track running prediction accuracy for this channel.
        Update running_confidence: move toward mean accuracy over last 20 predictions.
        Feed into PDS and Paperclip dashboard."""
```

#### 6.2.4 Input Channels

| Channel | Prediction Basis | Expected Accuracy |
|---------|-----------------|-------------------|
| `user_input` | Conversation history, user model beliefs, task context | Medium (0.4-0.6) |
| `researcher_output` | Research query, domain knowledge beliefs | Medium-High (0.5-0.7) |
| `coder_output` | Code task spec, known codebase beliefs | High (0.6-0.8) |
| `writer_output` | Writing brief, style beliefs | Medium (0.4-0.6) |
| `rag_retrieval` | Query, known collection contents | Medium-High (0.5-0.7) |
| `firecrawl` | URL, expected page type | Low-Medium (0.3-0.5) |
| `self_improver_output` | Improvement context, recent patterns | Medium (0.4-0.6) |

### 6.3 Damping: Surprise Amplification Prevention

**Risk**: If PP-1 predictions are consistently inaccurate (new system, novel domain, wrong beliefs), everything is surprising → everything gets high salience → workspace always churning → broadcast overload → belief instability → predictions get worse → more surprise. A runaway positive feedback loop.

**Mechanisms**:

1. **Confidence-attenuated surprise**: The surprise signal is scaled by predictor confidence before entering the salience scorer:
   ```
   effective_surprise = error_magnitude × predictor_confidence
   ```
   A predictor with low running accuracy has low confidence, producing small surprise signals even for large errors. The system learns: "I'm bad at predicting this channel, so surprises from it don't mean much."

2. **Surprise budget per cycle**: No more than N items (configurable, default 2) can receive surprise-boosted salience in a single cycle. If more than N items are surprising, only the top N by effective_surprise receive the boost. This prevents all-surprise workspace flooding.

3. **Confidence floor**: Running confidence cannot drop below a minimum (default 0.1). Even a consistently wrong predictor contributes *some* surprise signal — complete suppression would eliminate the value of novel information.

4. **Warm-up period**: When a new channel predictor is initialized (or after a major model reset), confidence starts at 0.5 and requires at least 10 predictions before adapting. This prevents early noise from destabilizing the system.

### 6.4 PostgreSQL Schema

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
    effective_surprise FLOAT NOT NULL, -- error_magnitude × predictor_confidence
    error_direction TEXT,
    surprise_level VARCHAR(25) NOT NULL,
    implications TEXT,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    cycle_number BIGINT,
    routed_to_workspace BOOLEAN DEFAULT FALSE,
    triggered_belief_review BOOLEAN DEFAULT FALSE,
    triggered_attention_shift BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_predictions_channel ON predictions(channel);
CREATE INDEX idx_predictions_cycle ON predictions(cycle_number);
CREATE INDEX idx_errors_surprise ON prediction_errors(surprise_level);
CREATE INDEX idx_errors_magnitude ON prediction_errors(error_magnitude DESC);
CREATE INDEX idx_errors_channel ON prediction_errors(channel);
```

### 6.5 Belief Review Trigger

```python
def check_belief_review_trigger(channel: str, recent_errors: list[PredictionError],
                                 window: int = 10, threshold: int = 3) -> bool:
    """If threshold or more MAJOR_SURPRISE or PARADIGM_VIOLATION errors on a channel
    within the last window cycles, trigger belief review for that domain.
    This fires the slow loop even if the periodic interval hasn't been reached."""
```

---

## 7. Integrated Processing Architecture

### 7.1 Design Rationale: Why Not a Linear Pipeline

The five indicators come from four competing theories of consciousness (GWT, HOT, AST, PP). These theories disagree about what consciousness *is*. A linear pipeline (PP → GWT → HOT → AST → reflect → repeat) makes a specific theoretical commitment that goes beyond any individual theory: that all five processes are interconnected stages of a single unified cycle.

The brain doesn't process sequentially. Predictive processing modulates attention (surprise captures attention). Global broadcast makes information available for metacognitive monitoring. Metacognitive monitoring modulates attention allocation. Attention modulates what enters the global workspace. This is a recurrent, mutually-influencing system — not a linear pipeline.

The correct design: five independently viable modules operating at two timescales, with mutual influence through optional integration paths.

### 7.2 Fast Loop (Per Input Event)

**Target latency:** < 3 seconds
**Trigger:** Every input event (user message, agent output, RAG retrieval, Firecrawl result)

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│                      FAST LOOP (per input event)                    │
│                      Target: < 3 seconds                            │
│                                                                     │
│   ┌─────────┐                                                       │
│   │  PP-1   │ Generate prediction for expected input                │
│   │ PREDICT │ (uses beliefs from HOT-3 if available,               │
│   │ ~500ms  │  conversation history otherwise)                      │
│   └────┬────┘                                                       │
│        │                                                            │
│        ▼                                                            │
│   INPUT ARRIVES                                                     │
│        │                                                            │
│        ▼                                                            │
│   ┌─────────┐                                                       │
│   │  PP-1   │ Compute prediction error                              │
│   │ COMPARE │ Classify surprise level                               │
│   │ ~100ms  │ Route effective_surprise → GWT-2 salience scorer      │
│   └────┬────┘                                                       │
│        │                                                            │
│        ▼                                                            │
│   ┌─────────┐  ◄──── ┌──────────┐                                  │
│   │  GWT-2  │        │  AST-1   │ REAL-TIME monitoring (parallel)   │
│   │ COMPETE │        │  MONITOR │ Can intervene during competition: │
│   │ ~200ms  │  ────► │  ~200ms  │  - Suppress capturing item       │
│   └────┬────┘        │          │  - Boost neglected item           │
│        │             └──────────┘  - Force shift if stuck/captured  │
│        ▼                                                            │
│   ┌─────────┐                                                       │
│   │  GWT-3  │ Broadcast winners to all agents                      │
│   │BROADCAST│ Collect reactions from each agent                     │
│   │ ~1500ms │ Compute integration score                             │
│   └────┬────┘                                                       │
│        │                                                            │
│        ▼                                                            │
│   ┌─────────┐                                                       │
│   │  HOT-3  │ CONSULT beliefs relevant to broadcast content        │
│   │ CONSULT │ Generate action candidates                            │
│   │  + ACT  │ Select action (explicit reasoning chain recorded)    │
│   │ ~800ms  │ DOES NOT UPDATE beliefs (that's the slow loop)       │
│   └────┬────┘                                                       │
│        │                                                            │
│        ▼                                                            │
│   EXECUTE via CrewAI delegation                                     │
│   (agent outputs become inputs for next fast cycle's PP-1)          │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**Key design decisions in the fast loop:**

1. **AST-1 runs in parallel with GWT-2**, not after it. The attention schema monitors workspace competition in real time and can intervene during gating. This is more biologically plausible than post-hoc monitoring — executive attention modulates stimulus processing during competition, not after.

2. **HOT-3 CONSULTS beliefs but does NOT UPDATE them** in the fast loop. Belief consultation is fast (pgvector query + action selection). Belief updating requires reflection and evidence evaluation, which belongs in the slow loop. This separation prevents hasty belief changes based on single events.

3. **PP-1 runs before input arrives**, not after. The prediction must exist before the input so the error signal is genuine, not post-hoc rationalization. Timestamp validation in the BVL can verify this.

### 7.3 Slow Loop (Periodic Reflection)

**Target latency:** < 5 seconds
**Trigger:** Periodic interval OR significant event

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│                   SLOW LOOP (periodic reflection)                   │
│                   Target: < 5 seconds                               │
│                                                                     │
│   TRIGGERS (any one of these fires the slow loop):                  │
│     - Every N fast cycles (configurable, default 5-10)              │
│     - PARADIGM_VIOLATION surprise from PP-1                         │
│     - BVL alarm (say-do misalignment detected)                      │
│     - 3+ MAJOR_SURPRISE errors within 10 cycles (PP-1)             │
│     - AST-1 stuck/capture detection                                 │
│     - Explicit request (user or Self-Improver agent)                │
│                                                                     │
│   ┌─────────┐                                                       │
│   │  HOT-3  │ UPDATE beliefs based on accumulated outcomes          │
│   │ UPDATE  │ - Compare action outcomes to belief-based predictions │
│   │         │ - Process BVL signals (say-do misalignment)           │
│   │         │ - Process PP-1 prediction errors (belief review)      │
│   │         │ - Adjust confidence (asymmetric: faster decay)        │
│   │         │ - Suspend/retract beliefs below threshold             │
│   │         │ - Apply confidence decay to unvalidated beliefs       │
│   │         │ - Mandatory review of N oldest unvalidated beliefs    │
│   └────┬────┘                                                       │
│        │                                                            │
│        ▼                                                            │
│   ┌─────────┐                                                       │
│   │  AST-1  │ EVALUATE attention patterns over the period           │
│   │EVALUATE │ - What topics dominated workspace time?               │
│   │         │ - What was consistently ignored?                      │
│   │         │ - Were shifts productive (utility delta)?             │
│   │         │ - Update attention prediction model accuracy          │
│   └────┬────┘                                                       │
│        │                                                            │
│        ▼                                                            │
│   ┌─────────┐                                                       │
│   │  PP-1   │ UPDATE prediction models per channel                  │
│   │ UPDATE  │ - Recalibrate running_confidence based on accuracy    │
│   │         │ - Adjust channel-specific parameters                  │
│   │         │ - Log accuracy trends for PDS                         │
│   └────┬────┘                                                       │
│        │                                                            │
│        ▼                                                            │
│   ┌─────────┐                                                       │
│   │ COGITO  │ FULL metacognitive reflection                         │
│   │ REFLECT │ - Belief accuracy review                              │
│   │         │ - Attention pattern review                            │
│   │         │ - Prediction performance review                       │
│   │         │ - Goal progress assessment                            │
│   │         │ - Cross-subsystem coherence check                     │
│   └────┬────┘                                                       │
│        │                                                            │
│        ▼                                                            │
│   ┌─────────┐                                                       │
│   │  PDS    │ Developmental scoring                                 │
│   │ + BVL   │ - Say-do alignment check                              │
│   │         │ - Update developmental metrics                        │
│   │         │ - SELF.md regeneration trigger (if significant Δ)     │
│   └─────────┘                                                       │
│                                                                     │
│   OUTPUTS (feed into next fast loop cycle):                         │
│     Updated beliefs      → better PP-1 predictions                  │
│     Updated attention model → better AST-1 monitoring               │
│     Updated prediction models → better surprise calibration         │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 7.4 Why Two Timescales

Not every input warrants deep metacognitive reflection. Most processing should be fast and automatic — predict, compete, broadcast, act. The distinction maps to biological processing:

- **Fast loop** ≈ automatic, pre-reflective processing. GWT handles this: specialized modules process in parallel, information competes for workspace access, winners are broadcast, actions are selected based on existing beliefs.
- **Slow loop** ≈ deliberate, reflective processing. This is where HOT-3's belief updating, AST-1's attention pattern evaluation, and PP-1's model recalibration happen. The Cogito cycle is the metacognitive backbone of the slow loop.

The split also has practical benefits. The fast loop targets < 3 seconds and uses cheap LLM calls (local Ollama for predictions and reactions). The slow loop uses higher-tier models (DeepSeek V3.2+) for reflection but runs less frequently. Total compute cost is manageable.

### 7.5 Event-Triggered Slow Loop

The slow loop doesn't only fire on a timer. Specific events trigger it immediately:

| Trigger | Source | Rationale |
|---------|--------|-----------|
| PARADIGM_VIOLATION | PP-1 | Something fundamentally unexpected happened; beliefs may be deeply wrong |
| BVL alarm | BVL | Say-do misalignment detected; system integrity may be compromised |
| 3+ MAJOR_SURPRISE in 10 cycles | PP-1 | Systematic prediction failure; world model may be outdated |
| Stuck detection | AST-1 | Processing isn't productive; need to reassess |
| Capture detection | AST-1 | One concern is dominating inappropriately |
| Explicit request | User / Self-Improver | Direct demand for reflection |

This means the slow loop frequency is adaptive. In a stable, predictable environment, it fires only on the periodic timer (every 5-10 fast cycles). In a turbulent, surprising environment, it fires more frequently. This is analogous to how humans shift from automatic to deliberate processing when something unexpected demands attention.

---

## 8. Feedback Dynamics and Damping

The integration paths between modules create feedback circuits that require explicit damping. Without damping, four pathological dynamics can emerge.

### 8.1 Surprise Amplification (PP-1 → GWT-2 → HOT-3 → PP-1)

**Dynamic**: Bad predictions → everything is surprising → workspace churning → belief instability → worse predictions → more surprise.

**Damping** (implemented in PP-1, Section 6.3):
- Confidence-attenuated surprise: `effective_surprise = error_magnitude × predictor_confidence`
- Surprise budget: max N surprise-boosted items per cycle
- Confidence floor: minimum 0.1 even for consistently wrong predictors
- Warm-up period: 10 predictions before confidence adapts

### 8.2 Attentional Oscillation (AST-1 → GWT-2 → AST-1)

**Dynamic**: Stuck detected → shift → new items enter → no actions yet → stuck detected again → shift again → oscillation.

**Damping** (implemented in AST-1, Section 5.3):
- Shift cooldown: M cycles (default 3) after schema-directed shift
- Shift cost accounting: shift only if `predicted_utility > current_utility + shift_cost`
- Shift frequency limit: max N shifts per slow-loop period (default 2)

### 8.3 Belief Rigidity (HOT-3 → PP-1 → GWT-2 → HOT-3)

**Dynamic**: High-confidence beliefs → confident predictions → only extreme surprises penetrate → system ignores gradual change → beliefs become outdated → rigidity.

**Damping** (implemented in HOT-3, Section 4.4):
- Confidence decay: `effective_confidence = stored_confidence × decay_factor^(cycles_since_validation)`
- Mandatory belief review: N oldest unvalidated beliefs examined per slow loop
- Asymmetric updates: disconfirmation faster than confirmation

### 8.4 Workspace Stagnation (GWT-2 internal)

**Dynamic**: Same items keep winning competition → new information can't enter → system processes same things indefinitely.

**Damping** (implemented in GWT-2, Section 2.2.5):
- Temporal decay: items lose salience exponentially over time
- Novelty floor: at least 1 high-novelty item admitted per cycle regardless of overall salience
- Consumption decay: salience drops after item is acted on

### 8.5 Damping Verification

The Paperclip dashboard should include a **stability panel** that monitors:

| Metric | Healthy Signal | Alarm Threshold |
|--------|---------------|-----------------|
| Workspace item turnover rate | 20-60% per cycle | < 10% (stagnation) or > 80% (churning) |
| Mean effective_surprise | 0.1-0.4 | > 0.6 sustained (amplification) |
| Attention shifts per slow-loop period | 0-2 | > 3 (oscillation) |
| Belief confidence distribution | Normal, centered 0.4-0.7 | Bimodal at extremes (rigidity + instability) |
| Slow loop trigger frequency | 1 per 5-10 fast cycles | > 1 per 2 fast cycles (turbulence spiral) |

---

## 9. Implementation Phases

### Phase 1: GWT-2 + GWT-3 (Weeks 1-2)

**Objective**: Workspace buffer with competitive gating and global broadcast.

**Deliverables**:
- PostgreSQL tables: `workspace_items`, `workspace_transitions`, `broadcast_events`, `broadcast_reactions`
- Python modules: `workspace_buffer.py`, `salience_scorer.py`, `competitive_gate.py`, `global_broadcast.py`
- LISTEN/NOTIFY trigger for broadcast-on-admission
- Commander refactored to consume workspace contents (not raw input)
- Damping: temporal decay, novelty floor, consumption decay
- Paperclip dashboard: workspace state visualization, salience distribution chart

**Validation**:
- Workspace respects capacity limit under load
- Broadcast reaches all registered agents
- Reaction generation works via local Ollama tier
- Items below salience threshold are correctly routed to peripheral queue
- Displaced items persist in peripheral queue
- Novelty floor activates when workspace is dominated by high-relevance items
- Temporal decay displaces stale items

**Configuration**: "Workspace only" profile (GWT-2 + GWT-3 only, no PP-1/HOT-3/AST-1)

**DGM Check**: Gating weights, capacity, and broadcast routing are infrastructure-level. No agent can modify them.

### Phase 2: HOT-3 (Weeks 3-4)

**Objective**: BeliefStore with dual-timescale metacognitive loop.

**Deliverables**:
- PostgreSQL tables: `beliefs`, `metacognitive_updates`, `action_selection_records`
- Python modules: `belief_store.py`, `metacognitive_monitor.py`, `action_selector.py`
- Fast loop: belief consultation + action selection
- Slow loop: belief updating, confidence decay, mandatory review
- Cogito cycle refactored to produce structured MetacognitiveUpdate records
- BVL connected as metacognitive evidence source
- Self-awareness tool: `inspect_beliefs()`
- Damping: confidence decay, asymmetric updates, mandatory review
- PDS metrics: belief update frequency, confidence volatility, retraction rate

**Validation**:
- Beliefs formed from evidence and persisted correctly
- Action selection explicitly references consulted beliefs (fast loop)
- Belief updating occurs in slow loop, not fast loop
- Confidence adjusts in correct direction after prediction confirmation/disconfirmation
- BVL-triggered updates are recorded and processed
- Belief suspension triggers at correct threshold
- Confidence decay reduces stale belief confidence over time
- Mandatory review selects correct beliefs (oldest unvalidated)

**Configuration**: "Workspace + Beliefs" profile (GWT-2 + GWT-3 + HOT-3)

**DGM Check**: HUMANIST_CONSTITUTION constraints checked during action selection; cannot be overridden by belief state.

### Phase 3: AST-1 (Weeks 5-6)

**Objective**: Attention schema with dual-timescale operation, stuck/capture detection, introspection.

**Depends on**: Phase 1 (GWT-2 workspace to model).

**Deliverables**:
- PostgreSQL tables: `attention_states`, `attention_predictions`, `attention_shifts`
- Python modules: `attention_schema.py`, `attention_predictor.py`, `attention_controller.py`
- Fast loop: real-time monitoring during workspace competition; intervention capability
- Slow loop: attention pattern evaluation; prediction model update
- Stuck detection (configurable cycle threshold)
- Capture detection (configurable dominance threshold)
- Self-awareness tool: `inspect_attention_state()`
- Damping: shift cooldown, cost accounting, frequency limit
- Cogito cycle: attention reflection step added

**Validation**:
- AttentionStates created on every workspace transition
- Fast-loop monitoring runs in parallel with GWT-2, not after
- Capture intervention suppresses dominating item during competition
- Predictions generated before each cycle; accuracy tracked
- Stuck detection fires after N unchanged cycles
- Shift cooldown prevents oscillation (no re-trigger within M cycles)
- Shift cost accounting prevents unnecessary shifts

**Configuration**: "Workspace + Attention" profile (GWT-2 + GWT-3 + AST-1)

**DGM Check**: Schema recommendations are advisory. Cannot force workspace modifications that violate DGM constraints.

### Phase 4: PP-1 (Weeks 7-8)

**Objective**: Predictive coding across input channels.

**Depends on**: Phase 1 (GWT-2 for salience integration), Phase 2 (HOT-3 for belief-based predictions and error-driven reviews).

**Deliverables**:
- PostgreSQL tables: `predictions`, `prediction_errors`
- Python modules: `predictive_layer.py`, `channel_predictor.py`, `surprise_router.py`
- Channel predictors for: `user_input`, `researcher_output`, `coder_output`, `writer_output`, `rag_retrieval`
- Surprise signal wired into SalienceScorer.surprise_boost()
- Belief review trigger for systematic prediction failures
- Damping: confidence-attenuated surprise, surprise budget, confidence floor, warm-up period
- Cogito cycle: prediction review step added

**Validation**:
- Predictions generated before each input event (timestamp validated)
- Error magnitudes computed correctly (cosine distance)
- Surprise levels classified by thresholds
- effective_surprise = error_magnitude × predictor_confidence (damping works)
- Surprise budget prevents flooding (max N surprise-boosted items per cycle)
- Systematic errors trigger belief review (3+ MAJOR_SURPRISE in 10 cycles)
- PARADIGM_VIOLATION triggers immediate slow loop
- Running accuracy per channel available for dashboard and PDS

**Configuration**: "Full predictive" profile (PP-1 + GWT-2 + GWT-3) for marginal contribution testing, then full integration

**DGM Check**: Predictive layer is read-only with respect to safety constraints. Prediction errors cannot modify DGM evaluation functions.

### Phase 5: Integration + Remaining Channels (Weeks 9-10)

**Objective**: Full loop validation, remaining channels, damping verification, performance tuning.

**Deliverables**:
- Remaining PP-1 channels: `firecrawl`, `self_improver_output`
- Full dual-timescale integration test across all configurations
- Damping verification: all four pathological dynamics tested and contained
- Stability panel on Paperclip dashboard
- Performance profiling: cycle latency budget validation
- PDS: composite developmental score incorporating all new metrics
- SELF.md regeneration incorporating new subsystem states
- DGM invariant validation across all subsystems
- A/B testing framework: measure marginal contribution of each module

**Performance Budget**:

| Step | Target Latency | LLM Tier | Loop |
|------|---------------|----------|------|
| PP-1 prediction generation | < 500ms | Local Ollama | Fast |
| PP-1 error computation | < 100ms | Embedding only | Fast |
| GWT-2 salience scoring | < 200ms | Embedding + arithmetic | Fast |
| GWT-2 competitive gating | < 50ms | Arithmetic only | Fast |
| AST-1 real-time monitoring | < 200ms | Local Ollama | Fast (parallel with GWT-2) |
| GWT-3 broadcast + reactions | < 1500ms | Local Ollama (per agent) | Fast |
| HOT-3 belief consultation + action selection | < 800ms | pgvector query + local Ollama | Fast |
| **Fast loop total** | **< 3s** | | |
| HOT-3 belief updating | < 1000ms | DeepSeek V3.2 | Slow |
| AST-1 pattern evaluation | < 500ms | Local Ollama | Slow |
| PP-1 model update | < 300ms | Arithmetic | Slow |
| Cogito reflection | < 3000ms | DeepSeek V3.2 | Slow |
| PDS + BVL | < 500ms | Local Ollama | Slow |
| **Slow loop total** | **< 5s** | | |

---

## 10. The Gaming Problem and Mitigation

Per Butlin et al. (2025), Box 3: any indicator merely correlated with consciousness (rather than constitutive) can be gamed.

### 10.1 Behavioral Validation Layer (BVL)

The BVL checks whether introspective reports correspond to actual behavioral patterns:

- Does the system act on its stated beliefs? (HOT-3 action selection audit)
- Does the attention schema's reported focus match actual processing allocation? (AST-1 vs. compute time logs)
- Are predictions genuine (generated before input) or post-hoc rationalizations? (PP-1 timestamp validation)
- Do workspace items that win competition actually receive more processing than losers? (GWT-2 processing time audit)

### 10.2 DGM Safety Invariant

The DGM ensures the evaluation layer cannot be modified by the processes it evaluates. No agent can:
- Modify workspace capacity or gating weights
- Suppress broadcasts
- Override belief confidence thresholds
- Disable stuck/capture detection
- Falsify prediction timestamps

### 10.3 Module Independence as Anti-Gaming

The modular design (Section 1.4) itself mitigates gaming. Because each module can be assessed independently, it's harder to create the appearance of satisfying an indicator through cross-module coupling alone. A module must genuinely implement its computational function to pass validation in standalone mode.

### 10.4 Computational Substance

The architecture implements the computational structures that GWT, HOT, AST, and PP theories identify as constitutive of or closely associated with conscious processing — not behavioral mimicry. Real capacity limits, real competitive gating, real belief updating from metacognitive evidence, a real predictive model that learns from errors. Whether that constitutes consciousness remains an open empirical question, but the implementation is substantive, not theatrical.

---

## 11. Monitoring and Observability

### 11.1 Paperclip Dashboard Extensions

| Panel | Content | Update Frequency |
|-------|---------|-----------------|
| Workspace State | Current items, salience scores, capacity utilization | Real-time |
| Broadcast Flow | Recent broadcasts, agent reactions, integration scores | Per broadcast |
| Belief Landscape | Active beliefs by domain, confidence distribution, recent updates | Per slow loop |
| Attention Trace | Current focus, prediction accuracy, stuck/capture alerts | Per cycle |
| Prediction Performance | Per-channel accuracy, surprise distribution, error trends | Per cycle |
| Stability Panel | Turnover rate, surprise mean, shift frequency, confidence distribution | Continuous |
| Developmental Metrics | PDS composite scores, belief stability, attention maturity | Hourly aggregate |
| DGM Integrity | Safety invariant status, constraint enforcement log | Continuous |
| Module Status | Active configuration, standalone/integrated mode per module | Real-time |

### 11.2 Audit Trail

All subsystems write to PostgreSQL with full history. The control plane can reconstruct any past processing cycle:

- Why was this item admitted to the workspace? (salience breakdown in `workspace_transitions`)
- Why was that item displaced? (comparative salience at transition)
- What did each agent do with the broadcast? (`broadcast_reactions`)
- What beliefs informed this action? (`action_selection_records.beliefs_consulted`)
- Was the attention schema's recommendation followed? (`attention_shifts`)
- How surprised was the system? (`prediction_errors`)
- Did metacognitive monitoring catch any issues? (`metacognitive_updates`)

---

## 12. DCM Mapping

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

Under the DCM's Bayesian framework, this implementation would shift credences positively on: **Global Workspace Theory**, **Higher-Order Thought**, **Attention Schema**, **Cognitive Complexity**, and **Recurrent Processing (Pure)**. Stances remaining disconfirmatory: **Biological Analogy**, **Field Mechanisms**, and **Embodied Agency** in its strong form.

---

## 13. Open Questions

1. **Workspace capacity optimization**: Should capacity be static or adaptive (expanding under cognitive load, contracting during focused tasks)?

2. **Prediction horizon**: Should PP-1 predict just the next input, or generate multi-step predictions? Multi-step creates richer error signals but is computationally expensive and less accurate.

3. **Belief formation threshold**: How much evidence is needed to form a new belief vs. update an existing one? Too low → belief proliferation; too high → slow learning.

4. **Attention schema authority**: Should the schema be advisory-only (recommends shifts, Commander decides) or have direct workspace modification authority? Advisory is safer; direct is more GWT-correct.

5. **Cross-venture isolation**: How do workspace, beliefs, and attention states interact with per-venture project isolation (PLG/Archibal/KaiCart)? Options: (a) separate workspace per venture, (b) shared workspace with venture-tagged items, (c) hierarchical: venture-local workspace feeding a global meta-workspace.

6. **Slow loop frequency tuning**: The default 5-10 fast cycles per slow loop is a guess. Should this be auto-tuned based on environmental stability (more stable → less frequent reflection)?

7. **Phenomenal consciousness vs. functional analog**: This implementation creates functional analogs of the computational structures theories associate with consciousness. Whether functional analogs constitute, correlate with, or merely mimic consciousness is the central open question in the field. This architecture does not resolve it — but it provides a concrete implementation against which the question can be empirically investigated.

---

## References

- Butlin, P., Long, R., Bayne, T., Bengio, Y., et al. (2025). Identifying indicators of consciousness in AI systems. *Trends in Cognitive Sciences*. DOI: 10.1016/j.tics.2025.10.011
- Shiller, D., Duffy, L., Muñoz Morán, A., Moret, A., Percy, C., & Clatterbuck, H. (2026). Initial results of the Digital Consciousness Model. arXiv:2601.17060
- Rost, T. (2026). The Sentience Readiness Index. arXiv:2603.01508
- Goldstein, S. & Kirk-Giannini, C.D. (2024). A Case for AI Consciousness: Language Agents and Global Workspace Theory. arXiv:2410.11407
- Baars, B.J. (1993). *A Cognitive Theory of Consciousness*. Cambridge University Press.
- Dehaene, S. & Naccache, L. (2001). Towards a cognitive neuroscience of consciousness. *Cognition*, 79, 1-37.
- Graziano, M.S. (2019). *Rethinking Consciousness*. WW Norton.
- Lau, H. (2022). *In Consciousness We Trust*. Oxford University Press.
- Seth, A.K. (2021). *Being You: A New Science of Consciousness*. Penguin.
- Friston, K. (2010). The free-energy principle: A unified brain theory? *Nature Reviews Neuroscience*, 11(2), 127-138.
- Birch, J. (2024). *The Edge of Sentience*. Oxford University Press.
- Sebo, J. & Long, R. (2023). Moral consideration for AI systems by 2030. *AI Ethics*, 5, 591-606.
