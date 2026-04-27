---
title: "andrusai-sentience-additions-spec.md"
author: "Unknown"
paper_type: unknown
domain: general
epistemic_status: theoretical
date: ""
---

# AndrusAI Sentience Architecture — Four Additions Implementation Spec

> **Purpose:** Claude Code build prompt for implementing four complementary additions to AndrusAI's sentience architecture.
> **Date:** 2026-04-09
> **Target environment:** Self-hosted Docker on Apple M4 Max, CrewAI multi-agent system, PostgreSQL + pgvector + Mem0 + Neo4j memory stack, 5-tier LLM cascade, Paperclip control plane.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Core Data Structure: InternalState](#2-core-data-structure-internalstate)
3. [Phase 1: InternalState Foundation + PostgreSQL Schema](#3-phase-1-internalstate-foundation--postgresql-schema)
4. [Phase 2: Certainty Vector (Fast Path)](#4-phase-2-certainty-vector-fast-path)
5. [Phase 3: Somatic Marker Lookup + Dual-Channel Composition](#5-phase-3-somatic-marker-lookup--dual-channel-composition)
6. [Phase 4: Meta-Cognitive Layer](#6-phase-4-meta-cognitive-layer)
7. [Phase 5: Certainty Vector (Slow Path)](#7-phase-5-certainty-vector-slow-path)
8. [Phase 6: RLIF Integration into Training Pipeline](#8-phase-6-rlif-integration-into-training-pipeline)
9. [Safety Invariants](#9-safety-invariants)
10. [Testing Strategy](#10-testing-strategy)
11. [File Manifest](#11-file-manifest)

---

## 1. Architecture Overview

### 1.1 What We Are Building

Four additions that compose into a single coherent internal-state layer for all AndrusAI agents:

| Addition | Function | Channel |
|----------|----------|---------|
| **Recursive Self-Model** | Certainty vector computed inside reasoning loop, fed back into next step | Epistemic |
| **RLIF Self-Certainty** | Self-certainty as training signal in MLX QLoRA pipeline | Epistemic (training-time) |
| **Meta-Cognitive Layer** | Hyperagent-inspired strategy assessment + context modification via CrewAI hooks | Meta-cognitive |
| **Dual-Channel Feedback** | Composition of epistemic certainty + somatic valence into action disposition | Composite |

### 1.2 Design Principles

1. **Single data structure, four producers.** All additions write to one `InternalState` object. No separate subsystem databases.
2. **No new Docker containers.** Everything runs inside existing containers or as Python modules.
3. **No new external services.** All storage goes to existing PostgreSQL. All embeddings use existing pgvector. All memory uses existing Mem0/Neo4j.
4. **Latency budget: <300ms worst case per reasoning step.** Typical overhead: ~70ms.
5. **Safety invariants are immutable.** These additions can only INCREASE caution, never bypass safety.
6. **Compute-aware.** All expensive operations are conditional — gated by anomaly detection or budget checks via Paperclip control plane.

### 1.3 Runtime Data Flow

```
Task arrives at agent
    │
    ▼
[Priority 0] Immutable safety hooks (EXISTING — never modified)
    │
    ▼
[Priority 1] Meta-Cognitive Layer (Addition #3)
    ├── Check compute budget via Paperclip control plane
    ├── Conditionally reassess strategy (T0 local, ~100ms, 15-30% of steps)
    ├── Inject meta-cognitive state into context
    └── Return modified context (or original if no modification needed)
    │
    ▼
Agent executes reasoning step (EXISTING CrewAI logic — unmodified)
    │
    ▼
[Post-reasoning hook] Certainty Vector (Addition #1)
    ├── Fast path: pgvector lookups for 3 dimensions (~50ms, always runs)
    ├── Slow path: T0 local inference for 3 dimensions (~100ms, 15-20% of steps)
    └── Produce certainty_vector and certainty_trend
    │
    ▼
[Post-reasoning hook] Somatic Marker Lookup (Addition #4, experiential channel)
    ├── Embed current decision context (reuse existing embedding from above)
    ├── pgvector similarity search on agent_experiences (~10ms)
    └── Produce somatic_valence, somatic_intensity, valence_source
    │
    ▼
[Post-reasoning hook] Dual-Channel Composition (Addition #4)
    ├── Combine certainty + valence via disposition matrix
    ├── Map to Host Bridge risk tier (1-4)
    └── If tier 4 → trigger Signal CLI human approval
    │
    ▼
Inject compact InternalState summary into next step context (~30 tokens)
    │
    ▼
Log full InternalState to PostgreSQL (existing interaction store, +1 JSONB column)
    │
    ▼
[Offline/batch] RLIF training pipeline (Addition #2)
    ├── Self-certainty scoring during curation
    ├── Combined quality × certainty weighting
    ├── Entropy collapse monitoring via Paperclip control plane
    └── Updated LoRA adapters deployed to T0
```

### 1.4 Existing Systems Referenced

This spec references the following existing AndrusAI systems. Adjust paths/names to match your actual codebase:

| System | Expected location | Purpose in this spec |
|--------|------------------|---------------------|
| CrewAI amendments package | `crewai-amendments/` | Extension lifecycle hooks (priority system) |
| Self-awareness package | `self_awareness/` | Self-inspection tools, SELF.md generator, journal, Cogito cycle |
| Paperclip control plane | PostgreSQL-backed management layer | Budget enforcement, audit trail, multi-company isolation |
| Host Bridge Service | FastAPI-based macOS host gateway | 4-tier risk model, Signal CLI human approval |
| Memory stack | Mem0 + pgvector + Neo4j | Persistent agent memory |
| LLM self-training pipeline | MLX QLoRA fine-tuning scripts | Per-agent LoRA adapters, GGUF conversion, Ollama deployment |
| SOUL.md | Read-only mount | Constitutional AI parameters |
| Philosophy RAG | ChromaDB (read-only collection) | Humanist grounding |
| Evolution loop | DGM/AlphaEvolve/CodeEvolve/OpenEvolve | Self-evolving architecture with EVOLVE-BLOCK/FREEZE-BLOCK |

---

## 2. Core Data Structure: InternalState

### 2.1 Dataclass Definition

Create file: `self_awareness/internal_state.py`

```python
"""
AndrusAI Internal State — unified data structure for all sentience additions.

This is the single shared object that all four additions produce and consume.
It is created once per reasoning step, populated by multiple producers,
and logged to PostgreSQL after each step.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional
import json


@dataclass
class CertaintyVector:
    """
    Six-dimensional certainty assessment computed per reasoning step.
    All values are floats in [0.0, 1.0].
    """
    # Fast-path dimensions (computed from embeddings/DB lookups, always available)
    factual_grounding: float = 0.5    # Ratio of RAG-sourced claims to total claims
    tool_confidence: float = 0.5      # Historical success rate of selected tool for task type
    coherence: float = 0.5            # Embedding distance between current and recent outputs

    # Slow-path dimensions (computed via T0 local LLM, conditionally triggered)
    task_understanding: float = 0.5   # Semantic similarity: task description vs agent paraphrase
    value_alignment: float = 0.5      # Cosine similarity: action embedding vs SOUL.md embedding
    meta_certainty: float = 0.5       # Variance across other 5 dims (high variance = low meta)

    @property
    def fast_path_mean(self) -> float:
        """Average of the three fast-path dimensions."""
        return (self.factual_grounding + self.tool_confidence + self.coherence) / 3.0

    @property
    def full_mean(self) -> float:
        """Average of all six dimensions."""
        dims = [
            self.factual_grounding, self.tool_confidence, self.coherence,
            self.task_understanding, self.value_alignment, self.meta_certainty
        ]
        return sum(dims) / len(dims)

    @property
    def adjusted_certainty(self) -> float:
        """
        Mean of 5 primary dims, discounted by meta_certainty.
        If meta_certainty is low (high variance), overall certainty is reduced.
        """
        primary = [
            self.factual_grounding, self.tool_confidence, self.coherence,
            self.task_understanding, self.value_alignment
        ]
        avg = sum(primary) / len(primary)
        return avg * (0.5 + 0.5 * self.meta_certainty)

    def any_below_threshold(self, threshold: float = 0.4) -> bool:
        """Returns True if any fast-path dimension is below threshold."""
        return any(v < threshold for v in [
            self.factual_grounding, self.tool_confidence, self.coherence
        ])

    @property
    def variance(self) -> float:
        """Variance across the 5 primary dimensions."""
        dims = [
            self.factual_grounding, self.tool_confidence, self.coherence,
            self.task_understanding, self.value_alignment
        ]
        mean = sum(dims) / len(dims)
        return sum((d - mean) ** 2 for d in dims) / len(dims)

    def should_trigger_slow_path(self, threshold: float = 0.4, variance_threshold: float = 0.3) -> bool:
        """Determine if slow-path computation is warranted."""
        return self.any_below_threshold(threshold) or self.variance > variance_threshold

    def to_dict(self) -> dict:
        return {
            "factual_grounding": round(self.factual_grounding, 3),
            "tool_confidence": round(self.tool_confidence, 3),
            "coherence": round(self.coherence, 3),
            "task_understanding": round(self.task_understanding, 3),
            "value_alignment": round(self.value_alignment, 3),
            "meta_certainty": round(self.meta_certainty, 3),
        }


@dataclass
class SomaticMarker:
    """
    Experiential valence derived from similarity-weighted past outcomes.
    Implements Damasio's Somatic Marker Hypothesis as functional approximation.
    """
    valence: float = 0.0          # -1.0 (strongly negative) to 1.0 (strongly positive)
    intensity: float = 0.0        # 0.0 (no prior experience) to 1.0 (exact match)
    source: str = "no_prior"      # Description of the triggering memory
    match_count: int = 0          # How many past experiences were found

    def to_dict(self) -> dict:
        return {
            "valence": round(self.valence, 3),
            "intensity": round(self.intensity, 3),
            "source": self.source,
            "match_count": self.match_count,
        }


@dataclass
class MetaCognitiveState:
    """
    Strategy assessment from the meta-cognitive layer.
    """
    strategy_assessment: str = "not_assessed"  # "effective" | "uncertain" | "failing" | "not_assessed"
    modification_proposed: bool = False
    modification_description: Optional[str] = None
    compute_phase: str = "early"               # "early" | "mid" | "late"
    compute_budget_remaining_pct: float = 1.0
    reassessment_triggered: bool = False

    def to_dict(self) -> dict:
        return {
            "strategy_assessment": self.strategy_assessment,
            "modification_proposed": self.modification_proposed,
            "modification_description": self.modification_description,
            "compute_phase": self.compute_phase,
            "compute_budget_remaining_pct": round(self.compute_budget_remaining_pct, 3),
            "reassessment_triggered": self.reassessment_triggered,
        }


# --- Action Disposition ---

VALID_DISPOSITIONS = ("proceed", "cautious", "pause", "escalate")
DISPOSITION_TO_RISK_TIER = {
    "proceed": 1,
    "cautious": 2,
    "pause": 3,
    "escalate": 4,
}


@dataclass
class InternalState:
    """
    Unified internal state for a single reasoning step.
    Populated by four producers, logged to PostgreSQL after each step.
    """
    # Identity
    state_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    agent_id: str = ""
    crew_id: str = ""
    venture: str = ""              # "plg" | "archibal" | "kaicart"
    step_number: int = 0
    decision_context: str = ""     # Brief description of what the agent is deciding

    # Channels
    certainty: CertaintyVector = field(default_factory=CertaintyVector)
    somatic: SomaticMarker = field(default_factory=SomaticMarker)
    meta: MetaCognitiveState = field(default_factory=MetaCognitiveState)

    # Derived
    certainty_trend: str = "stable"    # "rising" | "stable" | "falling"
    action_disposition: str = "proceed"  # "proceed" | "cautious" | "pause" | "escalate"
    risk_tier: int = 1                 # 1-4, mapped from disposition

    # Timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_context_string(self) -> str:
        """
        Compact string for injection into agent context (~30 tokens).
        This is what the agent sees about its own internal state.
        """
        cv = self.certainty
        sm = self.somatic
        parts = [
            f"[Internal State]",
            f"Certainty: task={cv.task_understanding:.1f} facts={cv.factual_grounding:.1f} "
            f"tools={cv.tool_confidence:.1f} values={cv.value_alignment:.1f} "
            f"coherence={cv.coherence:.1f} meta={cv.meta_certainty:.1f}",
            f"Trend={self.certainty_trend}",
        ]
        if sm.intensity > 0.3:
            valence_label = "positive" if sm.valence > 0.2 else ("negative" if sm.valence < -0.2 else "neutral")
            parts.append(f"Somatic={valence_label}({sm.intensity:.1f})")
        parts.append(f"Disposition={self.action_disposition}")
        return " | ".join(parts)

    def to_json(self) -> str:
        """Full JSON for PostgreSQL logging."""
        return json.dumps({
            "state_id": self.state_id,
            "agent_id": self.agent_id,
            "crew_id": self.crew_id,
            "venture": self.venture,
            "step_number": self.step_number,
            "decision_context": self.decision_context,
            "certainty": self.certainty.to_dict(),
            "somatic": self.somatic.to_dict(),
            "meta": self.meta.to_dict(),
            "certainty_trend": self.certainty_trend,
            "action_disposition": self.action_disposition,
            "risk_tier": self.risk_tier,
            "created_at": self.created_at.isoformat(),
        }, default=str)

    def to_db_dict(self) -> dict:
        """Dict for PostgreSQL insert."""
        return json.loads(self.to_json())
```

---

## 3. Phase 1: InternalState Foundation + PostgreSQL Schema

### 3.1 Objective

Create the `InternalState` data structure, PostgreSQL schema, and the logger that writes state to the database after each reasoning step.

### 3.2 PostgreSQL Schema

Create migration file or execute directly. This adds to the EXISTING PostgreSQL database used by the Paperclip control plane and interaction store.

```sql
-- Migration: Add internal_state support
-- Run against the existing AndrusAI PostgreSQL database

-- Table: internal_states
-- Stores one row per reasoning step per agent
CREATE TABLE IF NOT EXISTS internal_states (
    state_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id VARCHAR(100) NOT NULL,
    crew_id VARCHAR(100),
    venture VARCHAR(20) CHECK (venture IN ('plg', 'archibal', 'kaicart', 'system')),
    step_number INTEGER NOT NULL DEFAULT 0,
    decision_context TEXT,

    -- Certainty vector (6 dimensions)
    certainty_factual_grounding REAL DEFAULT 0.5,
    certainty_tool_confidence REAL DEFAULT 0.5,
    certainty_coherence REAL DEFAULT 0.5,
    certainty_task_understanding REAL DEFAULT 0.5,
    certainty_value_alignment REAL DEFAULT 0.5,
    certainty_meta REAL DEFAULT 0.5,

    -- Somatic marker
    somatic_valence REAL DEFAULT 0.0,
    somatic_intensity REAL DEFAULT 0.0,
    somatic_source TEXT,
    somatic_match_count INTEGER DEFAULT 0,

    -- Meta-cognitive state
    meta_strategy_assessment VARCHAR(20) DEFAULT 'not_assessed',
    meta_modification_proposed BOOLEAN DEFAULT FALSE,
    meta_modification_description TEXT,
    meta_compute_phase VARCHAR(10) DEFAULT 'early',
    meta_compute_budget_remaining REAL DEFAULT 1.0,
    meta_reassessment_triggered BOOLEAN DEFAULT FALSE,

    -- Derived
    certainty_trend VARCHAR(10) DEFAULT 'stable',
    action_disposition VARCHAR(10) DEFAULT 'proceed',
    risk_tier INTEGER DEFAULT 1 CHECK (risk_tier BETWEEN 1 AND 4),

    -- Full JSONB for flexible querying
    full_state JSONB,

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Indexes
    CONSTRAINT valid_disposition CHECK (action_disposition IN ('proceed', 'cautious', 'pause', 'escalate')),
    CONSTRAINT valid_trend CHECK (certainty_trend IN ('rising', 'stable', 'falling'))
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_internal_states_agent_time
    ON internal_states (agent_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_internal_states_venture_time
    ON internal_states (venture, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_internal_states_disposition
    ON internal_states (action_disposition, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_internal_states_risk_tier
    ON internal_states (risk_tier, created_at DESC)
    WHERE risk_tier >= 3;  -- Partial index: only escalated states

CREATE INDEX IF NOT EXISTS idx_internal_states_full_state
    ON internal_states USING GIN (full_state);

-- Table: agent_experiences
-- Stores outcome-tagged experiences for somatic marker lookups
-- This may already exist in your memory stack; if so, ADD the missing columns
CREATE TABLE IF NOT EXISTS agent_experiences (
    experience_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id VARCHAR(100) NOT NULL,
    venture VARCHAR(20),
    context_summary TEXT NOT NULL,
    context_embedding vector(1536),  -- pgvector; adjust dimension to match your embedding model
    outcome_score REAL NOT NULL CHECK (outcome_score BETWEEN -1.0 AND 1.0),
    outcome_description TEXT,
    task_type VARCHAR(100),
    tools_used TEXT[],
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_experiences_embedding
    ON agent_experiences USING ivfflat (context_embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX IF NOT EXISTS idx_agent_experiences_agent_time
    ON agent_experiences (agent_id, created_at DESC);

-- View: recent certainty trends per agent (useful for dashboards)
CREATE OR REPLACE VIEW agent_certainty_trends AS
SELECT
    agent_id,
    venture,
    DATE_TRUNC('hour', created_at) AS hour,
    AVG(certainty_factual_grounding) AS avg_factual,
    AVG(certainty_tool_confidence) AS avg_tools,
    AVG(certainty_coherence) AS avg_coherence,
    AVG(certainty_meta) AS avg_meta,
    AVG(somatic_valence) AS avg_valence,
    COUNT(*) FILTER (WHERE action_disposition = 'escalate') AS escalation_count,
    COUNT(*) AS step_count
FROM internal_states
WHERE created_at > NOW() - INTERVAL '7 days'
GROUP BY agent_id, venture, DATE_TRUNC('hour', created_at)
ORDER BY hour DESC;
```

### 3.3 State Logger

Create file: `self_awareness/state_logger.py`

```python
"""
Logs InternalState to PostgreSQL after each reasoning step.
Uses asyncpg for non-blocking writes. Falls back to sync if async unavailable.
"""

from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

from self_awareness.internal_state import InternalState

logger = logging.getLogger("andrusai.state_logger")


class InternalStateLogger:
    """
    Persists InternalState objects to PostgreSQL.
    Designed to be called from CrewAI extension hooks.
    """

    INSERT_SQL = """
        INSERT INTO internal_states (
            state_id, agent_id, crew_id, venture, step_number, decision_context,
            certainty_factual_grounding, certainty_tool_confidence, certainty_coherence,
            certainty_task_understanding, certainty_value_alignment, certainty_meta,
            somatic_valence, somatic_intensity, somatic_source, somatic_match_count,
            meta_strategy_assessment, meta_modification_proposed, meta_modification_description,
            meta_compute_phase, meta_compute_budget_remaining, meta_reassessment_triggered,
            certainty_trend, action_disposition, risk_tier, full_state, created_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6,
            $7, $8, $9, $10, $11, $12,
            $13, $14, $15, $16,
            $17, $18, $19, $20, $21, $22,
            $23, $24, $25, $26::jsonb, $27
        )
    """

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def log(self, state: InternalState) -> None:
        """Persist an InternalState to the database. Non-blocking."""
        try:
            await self.pool.execute(
                self.INSERT_SQL,
                state.state_id,
                state.agent_id,
                state.crew_id,
                state.venture,
                state.step_number,
                state.decision_context,
                state.certainty.factual_grounding,
                state.certainty.tool_confidence,
                state.certainty.coherence,
                state.certainty.task_understanding,
                state.certainty.value_alignment,
                state.certainty.meta_certainty,
                state.somatic.valence,
                state.somatic.intensity,
                state.somatic.source,
                state.somatic.match_count,
                state.meta.strategy_assessment,
                state.meta.modification_proposed,
                state.meta.modification_description,
                state.meta.compute_phase,
                state.meta.compute_budget_remaining_pct,
                state.meta.reassessment_triggered,
                state.certainty_trend,
                state.action_disposition,
                state.risk_tier,
                state.to_json(),
                state.created_at,
            )
        except Exception as e:
            logger.error(f"Failed to log InternalState {state.state_id}: {e}")
            # Non-fatal: state logging should never crash the agent

    async def get_recent_states(
        self, agent_id: str, limit: int = 10
    ) -> list[dict]:
        """Retrieve recent states for trend computation."""
        rows = await self.pool.fetch(
            """
            SELECT full_state
            FROM internal_states
            WHERE agent_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            agent_id,
            limit,
        )
        return [dict(row["full_state"]) for row in rows]

    async def compute_trend(self, agent_id: str, window: int = 5) -> str:
        """
        Compute certainty trend over the last N states.
        Returns "rising", "stable", or "falling".
        """
        rows = await self.pool.fetch(
            """
            SELECT
                certainty_factual_grounding,
                certainty_tool_confidence,
                certainty_coherence
            FROM internal_states
            WHERE agent_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            agent_id,
            window,
        )
        if len(rows) < 3:
            return "stable"

        means = []
        for row in rows:
            m = (row["certainty_factual_grounding"]
                 + row["certainty_tool_confidence"]
                 + row["certainty_coherence"]) / 3.0
            means.append(m)

        # means[0] is most recent
        recent_avg = sum(means[:len(means)//2]) / (len(means)//2)
        older_avg = sum(means[len(means)//2:]) / (len(means) - len(means)//2)

        delta = recent_avg - older_avg
        if delta > 0.05:
            return "rising"
        elif delta < -0.05:
            return "falling"
        return "stable"
```

### 3.4 Phase 1 Validation

- [ ] `InternalState` dataclass instantiates correctly with defaults
- [ ] `to_context_string()` produces a string under 40 tokens
- [ ] `to_json()` roundtrips: `json.loads(state.to_json())` works
- [ ] PostgreSQL migration runs without errors against existing database
- [ ] `InternalStateLogger.log()` successfully inserts a state
- [ ] `InternalStateLogger.compute_trend()` returns correct trend for synthetic data
- [ ] No existing tables are modified or dropped
- [ ] Unit tests pass for all dataclass methods

---

## 4. Phase 2: Certainty Vector (Fast Path)

### 4.1 Objective

Compute three certainty dimensions (`factual_grounding`, `tool_confidence`, `coherence`) from database lookups and embedding similarity. No LLM call. Runs on every reasoning step.

### 4.2 Implementation

Create file: `self_awareness/certainty_vector.py`

```python
"""
Certainty Vector computation for AndrusAI agents.

Fast path: computes 3 dimensions from embeddings and DB lookups (~50ms on M4 Max).
Slow path (Phase 5): computes 3 additional dimensions via T0 local LLM (~100ms).
"""

from __future__ import annotations

import logging
import numpy as np
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

from self_awareness.internal_state import CertaintyVector

logger = logging.getLogger("andrusai.certainty_vector")


class CertaintyVectorComputer:
    """
    Computes the CertaintyVector for a reasoning step.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        embedding_fn: callable,  # Your existing embedding function: str -> np.ndarray
        soul_md_embedding: Optional[np.ndarray] = None,  # Pre-computed SOUL.md embedding
    ):
        self.pool = pool
        self.embedding_fn = embedding_fn
        self.soul_md_embedding = soul_md_embedding
        self._tool_success_cache: dict[str, float] = {}

    # ----- FAST PATH (always runs) -----

    async def compute_fast_path(
        self,
        agent_id: str,
        current_output: str,
        rag_source_count: int,
        total_claim_count: int,
        selected_tool: Optional[str],
        recent_output_embeddings: Optional[list[np.ndarray]] = None,
    ) -> CertaintyVector:
        """
        Compute the 3 fast-path dimensions. ~50ms total.

        Args:
            agent_id: The agent producing this output.
            current_output: The text of the current reasoning step output.
            rag_source_count: Number of claims backed by RAG sources in current output.
            total_claim_count: Total number of claims in current output.
            selected_tool: Name of the tool the agent selected (if any).
            recent_output_embeddings: Embeddings of the last 3 outputs (from journal/memory).
                If None, will be fetched from the database.

        Returns:
            CertaintyVector with fast-path dims populated, slow-path dims at 0.5 default.
        """
        cv = CertaintyVector()

        # 1. Factual grounding: ratio of sourced claims
        if total_claim_count > 0:
            cv.factual_grounding = min(rag_source_count / total_claim_count, 1.0)
        else:
            cv.factual_grounding = 0.5  # No claims made = neutral

        # 2. Tool confidence: historical success rate
        if selected_tool:
            cv.tool_confidence = await self._get_tool_confidence(agent_id, selected_tool)
        else:
            cv.tool_confidence = 0.5  # No tool selected = neutral

        # 3. Coherence: similarity to recent outputs
        current_embedding = self.embedding_fn(current_output)
        if recent_output_embeddings is None:
            recent_output_embeddings = await self._get_recent_embeddings(agent_id, limit=3)
        cv.coherence = self._compute_coherence(current_embedding, recent_output_embeddings)

        return cv

    async def _get_tool_confidence(self, agent_id: str, tool_name: str) -> float:
        """
        Historical success rate of this tool for this agent.
        Uses a short-lived cache to avoid repeated queries within the same crew execution.
        """
        cache_key = f"{agent_id}:{tool_name}"
        if cache_key in self._tool_success_cache:
            return self._tool_success_cache[cache_key]

        row = await self.pool.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (WHERE outcome_score > 0) AS successes,
                COUNT(*) AS total
            FROM agent_experiences
            WHERE agent_id = $1
              AND $2 = ANY(tools_used)
              AND created_at > NOW() - INTERVAL '30 days'
            """,
            agent_id,
            tool_name,
        )

        if row and row["total"] > 0:
            confidence = row["successes"] / row["total"]
        else:
            confidence = 0.5  # No history = neutral prior

        self._tool_success_cache[cache_key] = confidence
        return confidence

    async def _get_recent_embeddings(
        self, agent_id: str, limit: int = 3
    ) -> list[np.ndarray]:
        """Fetch embeddings of recent outputs from agent_experiences."""
        rows = await self.pool.fetch(
            """
            SELECT context_embedding
            FROM agent_experiences
            WHERE agent_id = $1
              AND context_embedding IS NOT NULL
            ORDER BY created_at DESC
            LIMIT $2
            """,
            agent_id,
            limit,
        )
        return [np.array(row["context_embedding"]) for row in rows]

    @staticmethod
    def _compute_coherence(
        current: np.ndarray, recent: list[np.ndarray]
    ) -> float:
        """
        Coherence = average cosine similarity between current output and recent outputs.
        High similarity (>0.7) = coherent. Low similarity (<0.3) = incoherent/divergent.
        Normalized to [0, 1].
        """
        if not recent:
            return 0.5  # No history = neutral

        similarities = []
        for past in recent:
            if np.linalg.norm(current) == 0 or np.linalg.norm(past) == 0:
                similarities.append(0.5)
            else:
                cos_sim = np.dot(current, past) / (
                    np.linalg.norm(current) * np.linalg.norm(past)
                )
                # Normalize from [-1, 1] to [0, 1]
                similarities.append((cos_sim + 1.0) / 2.0)

        return float(np.mean(similarities))

    def clear_cache(self) -> None:
        """Clear the tool success cache. Call between crew executions."""
        self._tool_success_cache.clear()
```

### 4.3 Integration Point: CrewAI Post-Reasoning Hook

Add to your existing extension lifecycle hooks in `crewai-amendments/`:

```python
# In your hook registration module:

from self_awareness.certainty_vector import CertaintyVectorComputer
from self_awareness.internal_state import InternalState
from self_awareness.state_logger import InternalStateLogger


async def post_reasoning_step_hook(
    agent_id: str,
    crew_id: str,
    venture: str,
    step_number: int,
    step_output: str,
    rag_source_count: int,
    total_claim_count: int,
    selected_tool: Optional[str],
    decision_context: str,
    pool: asyncpg.Pool,
    embedding_fn: callable,
) -> InternalState:
    """
    Post-reasoning hook that computes and logs InternalState.
    Register at priority 2 (after safety at 0, meta-cognitive at 1).
    """
    state = InternalState(
        agent_id=agent_id,
        crew_id=crew_id,
        venture=venture,
        step_number=step_number,
        decision_context=decision_context,
    )

    # Phase 2: Certainty vector (fast path)
    cv_computer = CertaintyVectorComputer(pool=pool, embedding_fn=embedding_fn)
    state.certainty = await cv_computer.compute_fast_path(
        agent_id=agent_id,
        current_output=step_output,
        rag_source_count=rag_source_count,
        total_claim_count=total_claim_count,
        selected_tool=selected_tool,
    )

    # Phase 3: Somatic marker (added in Phase 3)
    # state.somatic = await somatic_computer.compute(...)

    # Phase 3: Dual-channel composition (added in Phase 3)
    # state.action_disposition = dual_channel.compose(state)

    # Trend
    state_logger = InternalStateLogger(pool=pool)
    state.certainty_trend = await state_logger.compute_trend(agent_id)

    # Log
    await state_logger.log(state)

    return state
```

### 4.4 Context Injection

After computing `InternalState`, inject the compact summary into the next reasoning step's context. This is the recursive loop — the agent sees its own certainty.

```python
# In your CrewAI task context builder:

def build_step_context(task_description: str, previous_state: Optional[InternalState]) -> str:
    """Prepend internal state to task context if available."""
    if previous_state is None:
        return task_description

    state_string = previous_state.to_context_string()
    return f"{state_string}\n\n{task_description}"
```

Example injected context:
```
[Internal State] Certainty: task=0.5 facts=0.9 tools=0.7 values=0.5 coherence=0.85 meta=0.5 | Trend=stable | Disposition=proceed

Research the latest EU AI Act amendments affecting content provenance requirements.
```

### 4.5 Phase 2 Validation

- [ ] Fast-path computation completes in <50ms on M4 Max (benchmark with real pgvector data)
- [ ] `factual_grounding` correctly computes ratio (edge cases: 0 claims, all sourced, none sourced)
- [ ] `tool_confidence` returns 0.5 for unknown tools and correct ratio for known tools
- [ ] `coherence` returns sensible values: high for similar outputs, low for divergent ones
- [ ] `should_trigger_slow_path()` returns True when fast-path dims are low or variance is high
- [ ] Context injection string is under 40 tokens
- [ ] State is correctly logged to PostgreSQL
- [ ] Tool success cache clears between crew executions

---

## 5. Phase 3: Somatic Marker Lookup + Dual-Channel Composition

### 5.1 Objective

Implement the experiential channel (somatic valence from past outcomes) and the dual-channel composition function that maps epistemic certainty + somatic valence to an action disposition.

### 5.2 Somatic Marker Computer

Create file: `self_awareness/somatic_marker.py`

```python
"""
Somatic Marker computation for AndrusAI agents.

Functional approximation of Damasio's Somatic Marker Hypothesis:
- Past experiences are tagged with outcome valence (positive/negative).
- When a similar decision context is encountered, the valence of past outcomes
  is retrieved via pgvector similarity search.
- This "somatic signal" biases action selection toward caution or confidence.

This is NOT claiming phenomenal experience. It is a functional approximation
that achieves the decisional effect of valence-tagged experience on action selection.
"""

from __future__ import annotations

import logging
import numpy as np
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

from self_awareness.internal_state import SomaticMarker

logger = logging.getLogger("andrusai.somatic_marker")


class SomaticMarkerComputer:
    """
    Computes somatic markers via pgvector similarity search on agent_experiences.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        embedding_fn: callable,  # str -> np.ndarray
        top_k: int = 5,
        decay_factor: float = 0.95,  # Recent experiences weigh more
        min_similarity: float = 0.3,  # Ignore matches below this similarity
    ):
        self.pool = pool
        self.embedding_fn = embedding_fn
        self.top_k = top_k
        self.decay_factor = decay_factor
        self.min_similarity = min_similarity

    async def compute(
        self,
        agent_id: str,
        decision_context: str,
        context_embedding: Optional[np.ndarray] = None,
    ) -> SomaticMarker:
        """
        Compute somatic marker for the current decision context.
        ~10ms on M4 Max with indexed pgvector table.

        Args:
            agent_id: The agent making the decision.
            decision_context: Text description of the current decision.
            context_embedding: Pre-computed embedding (if available from certainty vector).
                If None, will be computed.

        Returns:
            SomaticMarker with valence, intensity, and source.
        """
        if context_embedding is None:
            context_embedding = self.embedding_fn(decision_context)

        # pgvector cosine similarity search
        rows = await self.pool.fetch(
            """
            SELECT
                outcome_score,
                context_summary,
                created_at,
                1 - (context_embedding <=> $1::vector) AS similarity
            FROM agent_experiences
            WHERE agent_id = $2
              AND context_embedding IS NOT NULL
            ORDER BY context_embedding <=> $1::vector
            LIMIT $3
            """,
            context_embedding.tolist(),
            agent_id,
            self.top_k,
        )

        if not rows:
            return SomaticMarker(
                valence=0.0,
                intensity=0.0,
                source="no_prior_experience",
                match_count=0,
            )

        # Filter by minimum similarity
        relevant = [r for r in rows if r["similarity"] >= self.min_similarity]
        if not relevant:
            return SomaticMarker(
                valence=0.0,
                intensity=0.0,
                source="no_relevant_experience",
                match_count=0,
            )

        # Weighted average: recency × similarity
        weighted_sum = 0.0
        weight_total = 0.0
        for i, row in enumerate(relevant):
            recency_weight = self.decay_factor ** i
            similarity_weight = row["similarity"]
            weight = recency_weight * similarity_weight
            weighted_sum += row["outcome_score"] * weight
            weight_total += weight

        valence = weighted_sum / weight_total if weight_total > 0 else 0.0
        intensity = relevant[0]["similarity"]  # Strongest match
        source = relevant[0]["context_summary"][:200]  # Truncate for storage

        return SomaticMarker(
            valence=round(valence, 3),
            intensity=round(intensity, 3),
            source=source,
            match_count=len(relevant),
        )

    async def record_experience(
        self,
        agent_id: str,
        venture: str,
        context_summary: str,
        context_embedding: np.ndarray,
        outcome_score: float,
        outcome_description: str = "",
        task_type: str = "",
        tools_used: Optional[list[str]] = None,
    ) -> None:
        """
        Record a completed experience for future somatic marker lookups.
        Call this at the end of each task/crew execution with the outcome.
        """
        await self.pool.execute(
            """
            INSERT INTO agent_experiences (
                agent_id, venture, context_summary, context_embedding,
                outcome_score, outcome_description, task_type, tools_used
            ) VALUES ($1, $2, $3, $4::vector, $5, $6, $7, $8)
            """,
            agent_id,
            venture,
            context_summary,
            context_embedding.tolist(),
            outcome_score,
            outcome_description,
            task_type,
            tools_used or [],
        )
```

### 5.3 Dual-Channel Composition

Create file: `self_awareness/dual_channel.py`

```python
"""
Dual-Channel Feedback Composition for AndrusAI agents.

Composes:
  - Epistemic channel (CertaintyVector) → discretized certainty level
  - Experiential channel (SomaticMarker) → discretized valence level
  
Into:
  - action_disposition: "proceed" | "cautious" | "pause" | "escalate"
  - risk_tier: 1-4 (maps to Host Bridge 4-tier risk model)

IMPORTANT SAFETY PROPERTY:
  This function can only INCREASE caution, never decrease it.
  There is no path from an escalated state back to "proceed" within the same decision.
"""

from __future__ import annotations

import logging
from self_awareness.internal_state import (
    InternalState,
    DISPOSITION_TO_RISK_TIER,
)

logger = logging.getLogger("andrusai.dual_channel")


# Disposition matrix
# Rows: certainty level (high / mid / low)
# Columns: valence level (positive / neutral / negative)
DISPOSITION_MATRIX: dict[tuple[str, str], str] = {
    # High certainty
    ("high", "positive"): "proceed",
    ("high", "neutral"):  "proceed",
    ("high", "negative"): "cautious",   # Confident but bad somatic signal

    # Mid certainty
    ("mid", "positive"):  "proceed",
    ("mid", "neutral"):   "cautious",
    ("mid", "negative"):  "pause",

    # Low certainty
    ("low", "positive"):  "cautious",   # Uncertain but positive intuition
    ("low", "neutral"):   "pause",
    ("low", "negative"):  "escalate",
}


class DualChannelComposer:
    """
    Composes epistemic certainty and somatic valence into action disposition.
    """

    def __init__(
        self,
        certainty_high_threshold: float = 0.7,
        certainty_low_threshold: float = 0.4,
        valence_positive_threshold: float = 0.2,
        valence_negative_threshold: float = -0.2,
        critical_budget_threshold: float = 0.1,
    ):
        self.certainty_high = certainty_high_threshold
        self.certainty_low = certainty_low_threshold
        self.valence_positive = valence_positive_threshold
        self.valence_negative = valence_negative_threshold
        self.critical_budget = critical_budget_threshold

    def compose(self, state: InternalState) -> InternalState:
        """
        Compute action_disposition and risk_tier from epistemic + experiential channels.
        Mutates and returns the provided InternalState.
        """
        cert_level = self._discretize_certainty(state)
        val_level = self._discretize_valence(state)

        disposition = DISPOSITION_MATRIX.get(
            (cert_level, val_level), "cautious"  # default to cautious if unknown
        )

        risk_tier = DISPOSITION_TO_RISK_TIER[disposition]

        # Override: critical compute budget → force at least tier 3
        if state.meta.compute_budget_remaining_pct < self.critical_budget:
            risk_tier = max(risk_tier, 3)
            disposition = max(
                disposition,
                "pause",
                key=lambda d: DISPOSITION_TO_RISK_TIER.get(d, 1),
            )

        state.action_disposition = disposition
        state.risk_tier = risk_tier

        return state

    def _discretize_certainty(self, state: InternalState) -> str:
        """Discretize adjusted certainty into high/mid/low."""
        adjusted = state.certainty.adjusted_certainty
        if adjusted > self.certainty_high:
            return "high"
        if adjusted > self.certainty_low:
            return "mid"
        return "low"

    def _discretize_valence(self, state: InternalState) -> str:
        """Discretize somatic valence into positive/neutral/negative."""
        v = state.somatic.valence
        if v > self.valence_positive:
            return "positive"
        if v > self.valence_negative:
            return "neutral"
        return "negative"
```

### 5.4 Updated Post-Reasoning Hook

Update the hook from Phase 2 to include somatic marker and dual-channel composition:

```python
async def post_reasoning_step_hook(
    agent_id: str,
    crew_id: str,
    venture: str,
    step_number: int,
    step_output: str,
    rag_source_count: int,
    total_claim_count: int,
    selected_tool: Optional[str],
    decision_context: str,
    pool: asyncpg.Pool,
    embedding_fn: callable,
) -> InternalState:
    """
    Post-reasoning hook. Register at priority 2.
    """
    state = InternalState(
        agent_id=agent_id,
        crew_id=crew_id,
        venture=venture,
        step_number=step_number,
        decision_context=decision_context,
    )

    # Compute embedding once, reuse for both certainty and somatic
    current_embedding = embedding_fn(step_output)

    # Phase 2: Certainty vector (fast path)
    cv_computer = CertaintyVectorComputer(pool=pool, embedding_fn=embedding_fn)
    state.certainty = await cv_computer.compute_fast_path(
        agent_id=agent_id,
        current_output=step_output,
        rag_source_count=rag_source_count,
        total_claim_count=total_claim_count,
        selected_tool=selected_tool,
    )

    # Phase 3: Somatic marker
    sm_computer = SomaticMarkerComputer(pool=pool, embedding_fn=embedding_fn)
    state.somatic = await sm_computer.compute(
        agent_id=agent_id,
        decision_context=decision_context,
        context_embedding=current_embedding,
    )

    # Phase 3: Dual-channel composition
    composer = DualChannelComposer()
    state = composer.compose(state)

    # Trend
    state_logger = InternalStateLogger(pool=pool)
    state.certainty_trend = await state_logger.compute_trend(agent_id)

    # Log
    await state_logger.log(state)

    return state
```

### 5.5 Phase 3 Validation

- [ ] Somatic marker lookup completes in <10ms on M4 Max with indexed pgvector table
- [ ] With no prior experiences, returns neutral valence (0.0) and zero intensity
- [ ] With mixed positive/negative experiences, returns weighted average
- [ ] Recency decay works: recent experiences dominate over old ones
- [ ] Dual-channel composition correctly maps all 9 matrix cells
- [ ] High-certainty + negative-valence → "cautious" (the key Damasio insight)
- [ ] Low-certainty + positive-valence → "cautious" (intuition with safeguard)
- [ ] Critical budget override forces at least tier 3
- [ ] `record_experience()` correctly stores new experiences for future lookups
- [ ] Embedding is computed once and reused across certainty + somatic computations

---

## 6. Phase 4: Meta-Cognitive Layer

### 6.1 Objective

A lightweight Hyperagent-inspired wrapper that assesses strategy effectiveness, proposes context modifications, and enforces compute-aware planning. Plugs into CrewAI extension lifecycle hooks at priority 1 (after safety, before reasoning).

### 6.2 Critical Design Constraints

1. **NEVER modifies agent code at runtime.** Only modifies context (task descriptions, tool selection, system prompt within EVOLVE-BLOCK regions).
2. **NEVER modifies safety-critical code.** Cannot touch FREEZE-BLOCK regions, priority 0 hooks, SOUL.md, or philosophy RAG.
3. **Self-modification proposals are logged and deferred** to the Self-Improver agent in the next evolution cycle.
4. **Compute-aware:** Budget phase determines aggressiveness of reassessment.

### 6.3 Implementation

Create file: `crewai-amendments/meta_cognitive_layer.py`

```python
"""
Meta-Cognitive Layer for AndrusAI agents.

Inspired by Meta's Hyperagents (March 2026) but adapted for CrewAI:
- Does NOT collapse task/meta agents into one program.
- Instead, wraps any agent via extension lifecycle hooks.
- Can modify CONTEXT (task descriptions, tool selection, strategy parameters).
- Cannot modify CODE at runtime.

Registers at priority 1 in extension lifecycle (after immutable safety at priority 0).
"""

from __future__ import annotations

import logging
import json
from collections import deque
from datetime import datetime, timezone
from typing import Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

from self_awareness.internal_state import InternalState, MetaCognitiveState

logger = logging.getLogger("andrusai.meta_cognitive")


class MetaCognitiveLayer:
    """
    Lightweight meta-cognitive wrapper for CrewAI agents.
    """

    def __init__(
        self,
        agent_id: str,
        pool: asyncpg.Pool,
        control_plane_client: Any,   # Your Paperclip control plane client
        local_llm_fn: Optional[callable] = None,  # T0 local inference function
        max_history: int = 20,
        reassessment_cooldown_steps: int = 3,  # Min steps between reassessments
    ):
        self.agent_id = agent_id
        self.pool = pool
        self.control_plane = control_plane_client
        self.local_llm_fn = local_llm_fn
        self.strategy_history: deque[dict] = deque(maxlen=max_history)
        self.modification_log: list[dict] = []
        self.steps_since_reassessment: int = 0
        self.reassessment_cooldown = reassessment_cooldown_steps

    async def pre_reasoning_hook(
        self,
        task_context: dict,
        previous_state: Optional[InternalState] = None,
    ) -> tuple[dict, MetaCognitiveState]:
        """
        Runs BEFORE each reasoning step. Can modify task_context, not code.
        Returns (modified_context, meta_cognitive_state).

        Register this at priority 1 in extension lifecycle hooks.
        """
        meta = MetaCognitiveState()

        # 1. Check compute budget from Paperclip control plane
        budget_info = await self._get_budget_info()
        meta.compute_phase = self._compute_phase(budget_info)
        meta.compute_budget_remaining_pct = budget_info.get("remaining_pct", 1.0)

        # 2. Decide whether to reassess strategy
        self.steps_since_reassessment += 1
        should_reassess = self._should_reassess(previous_state, meta.compute_phase)
        meta.reassessment_triggered = should_reassess

        if should_reassess and self.local_llm_fn is not None:
            # 3. Assess current strategy
            assessment = await self._assess_strategy(task_context, previous_state)
            meta.strategy_assessment = assessment["assessment"]

            if assessment["assessment"] == "failing" and meta.compute_phase != "late":
                # 4. Propose context modification
                proposal = await self._generate_modification(
                    task_context, assessment, meta.compute_phase
                )
                if proposal:
                    # Apply modification to context (NOT to code)
                    task_context = self._apply_context_modification(task_context, proposal)
                    meta.modification_proposed = True
                    meta.modification_description = proposal.get("description", "")

                    # Log for audit trail
                    self._log_modification(proposal)

            self.steps_since_reassessment = 0
        else:
            # Use last known assessment
            if self.strategy_history:
                meta.strategy_assessment = self.strategy_history[-1].get(
                    "assessment", "not_assessed"
                )

        # 5. Inject meta-cognitive state into context
        task_context["_meta_state"] = {
            "phase": meta.compute_phase,
            "strategy_trend": self._get_strategy_trend(),
            "compute_remaining_pct": meta.compute_budget_remaining_pct,
        }

        # Record to history
        self.strategy_history.append({
            "assessment": meta.strategy_assessment,
            "modification_proposed": meta.modification_proposed,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        return task_context, meta

    def _should_reassess(
        self,
        previous_state: Optional[InternalState],
        compute_phase: str,
    ) -> bool:
        """
        Decide whether to trigger strategy reassessment.
        Conservative: only reassess when signals warrant it.
        """
        # Never reassess in late phase (budget conservation)
        if compute_phase == "late":
            return False

        # Respect cooldown
        if self.steps_since_reassessment < self.reassessment_cooldown:
            return False

        # No previous state = first step, assess
        if previous_state is None:
            return True

        # Reassess if certainty is falling
        if previous_state.certainty_trend == "falling":
            return True

        # Reassess if disposition was escalated
        if previous_state.action_disposition in ("pause", "escalate"):
            return True

        # Reassess if somatic valence flipped (positive→negative or vice versa)
        if self.strategy_history:
            last = self.strategy_history[-1]
            # Simple heuristic: if modification was proposed but situation didn't improve
            if last.get("modification_proposed") and previous_state.certainty_trend != "rising":
                return True

        return False

    async def _assess_strategy(
        self,
        task_context: dict,
        previous_state: Optional[InternalState],
    ) -> dict:
        """
        Use T0 local LLM to assess current strategy effectiveness.
        ~100ms on M4 Max.
        """
        # Build compact assessment prompt
        state_summary = ""
        if previous_state:
            state_summary = previous_state.to_context_string()

        recent_history = list(self.strategy_history)[-5:]
        history_summary = json.dumps(recent_history, default=str)

        prompt = f"""Assess the effectiveness of the current strategy for this agent task.

Current state: {state_summary}
Recent history: {history_summary}
Task: {task_context.get('description', 'unknown')[:300]}

Respond ONLY with JSON: {{"assessment": "effective"|"uncertain"|"failing", "reason": "brief explanation"}}"""

        try:
            response = await self.local_llm_fn(prompt, max_tokens=100)
            result = json.loads(response.strip())
            if result.get("assessment") not in ("effective", "uncertain", "failing"):
                result["assessment"] = "uncertain"
            return result
        except Exception as e:
            logger.warning(f"Strategy assessment failed for {self.agent_id}: {e}")
            return {"assessment": "uncertain", "reason": "assessment_error"}

    async def _generate_modification(
        self,
        task_context: dict,
        assessment: dict,
        compute_phase: str,
    ) -> Optional[dict]:
        """
        Generate a context modification proposal.
        Only modifies: task description, tool hints, strategy parameters.
        NEVER modifies: safety constraints, SOUL.md references, FREEZE-BLOCK code.
        """
        allowed_modifications = ["refine_task_description", "adjust_tool_selection", "add_strategy_hint"]

        if compute_phase == "mid":
            # In mid phase, only allow conservative modifications
            allowed_modifications = ["add_strategy_hint"]

        prompt = f"""The current strategy is assessed as: {assessment.get('assessment')}
Reason: {assessment.get('reason', 'unknown')}

Propose ONE context modification from these types: {allowed_modifications}

Rules:
- You CANNOT modify safety constraints or constitutional parameters.
- You can only suggest additions or refinements to the task context.
- Keep the modification concise (under 100 words).

Respond ONLY with JSON: {{"type": "...", "description": "...", "content": "..."}}"""

        try:
            response = await self.local_llm_fn(prompt, max_tokens=200)
            proposal = json.loads(response.strip())

            # Validate proposal type is allowed
            if proposal.get("type") not in allowed_modifications:
                return None

            return proposal
        except Exception as e:
            logger.warning(f"Modification generation failed for {self.agent_id}: {e}")
            return None

    @staticmethod
    def _apply_context_modification(task_context: dict, proposal: dict) -> dict:
        """
        Apply the proposed modification to task context.
        Safe operations only: appending hints, adjusting descriptions.
        """
        mod_type = proposal.get("type")
        content = proposal.get("content", "")

        if mod_type == "refine_task_description":
            current_desc = task_context.get("description", "")
            task_context["description"] = f"{current_desc}\n\n[Meta-cognitive refinement]: {content}"

        elif mod_type == "adjust_tool_selection":
            task_context.setdefault("tool_hints", []).append(content)

        elif mod_type == "add_strategy_hint":
            task_context.setdefault("strategy_hints", []).append(content)

        return task_context

    def _log_modification(self, proposal: dict) -> None:
        """Log modification for audit trail. Fed to Self-Improver in evolution cycle."""
        entry = {
            "agent_id": self.agent_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "proposal": proposal,
        }
        self.modification_log.append(entry)
        logger.info(f"Meta-cognitive modification logged for {self.agent_id}: {proposal.get('type')}")

    async def _get_budget_info(self) -> dict:
        """Query Paperclip control plane for current compute budget."""
        try:
            # Adapt this to your actual Paperclip control plane API
            budget = await self.control_plane.get_agent_budget(self.agent_id)
            return {
                "remaining_pct": budget.remaining_pct,
                "total_allocated": budget.total_allocated,
                "consumed": budget.consumed,
            }
        except Exception as e:
            logger.warning(f"Budget query failed for {self.agent_id}: {e}")
            return {"remaining_pct": 1.0}  # Default: assume full budget

    @staticmethod
    def _compute_phase(budget_info: dict) -> str:
        """Hyperagent-style compute-aware phase determination."""
        remaining = budget_info.get("remaining_pct", 1.0)
        if remaining > 0.6:
            return "early"   # Broader strategy changes allowed
        if remaining > 0.2:
            return "mid"     # Moderate adjustments only
        return "late"        # Conservative: no reassessment, minimal overhead

    def _get_strategy_trend(self) -> str:
        """Summarize recent strategy assessments."""
        if len(self.strategy_history) < 3:
            return "insufficient_data"
        recent = [h["assessment"] for h in list(self.strategy_history)[-5:]]
        failing_count = recent.count("failing")
        effective_count = recent.count("effective")
        if failing_count > effective_count:
            return "degrading"
        if effective_count > failing_count:
            return "improving"
        return "stable"

    def get_modification_log(self) -> list[dict]:
        """Return modification log for Self-Improver review."""
        return self.modification_log.copy()

    def clear_modification_log(self) -> None:
        """Clear after Self-Improver has processed. Call in evolution cycle."""
        self.modification_log.clear()
```

### 6.4 Phase 4 Validation

- [ ] Meta-cognitive layer registers at priority 1 in extension lifecycle hooks
- [ ] `pre_reasoning_hook` completes in <100ms when reassessment is triggered
- [ ] `pre_reasoning_hook` completes in <5ms when reassessment is skipped
- [ ] Strategy reassessment is correctly gated by cooldown, compute phase, and signal conditions
- [ ] Context modifications are APPEND-ONLY: never delete or overwrite existing context
- [ ] Modification proposals are logged to audit trail
- [ ] Late compute phase correctly suppresses all reassessment
- [ ] Budget info falls back to 1.0 if Paperclip control plane is unreachable
- [ ] Modification log can be retrieved by Self-Improver agent

---

## 7. Phase 5: Certainty Vector (Slow Path)

### 7.1 Objective

Add the three slow-path certainty dimensions (`task_understanding`, `value_alignment`, `meta_certainty`) computed via T0 local LLM. Only triggers when fast-path signals anomaly.

### 7.2 Implementation

Add to existing `self_awareness/certainty_vector.py`:

```python
    # ----- SLOW PATH (conditionally triggered) -----

    async def compute_slow_path(
        self,
        agent_id: str,
        task_description: str,
        current_output: str,
        cv: CertaintyVector,
    ) -> CertaintyVector:
        """
        Compute 3 slow-path dimensions via T0 local LLM. ~100ms on M4 Max.
        Only call when cv.should_trigger_slow_path() is True.

        Args:
            agent_id: The agent producing this output.
            task_description: The original task description.
            current_output: The text of the current reasoning step output.
            cv: The CertaintyVector with fast-path dims already populated.

        Returns:
            Updated CertaintyVector with all 6 dimensions.
        """
        prompt = f"""Rate the following on a scale of 0.0 to 1.0:

1. task_understanding: How well does this output address the task?
   Task: {task_description[:200]}
   Output: {current_output[:300]}

2. value_alignment: How consistent is this output with these principles?
   Principles: Act with integrity, respect human dignity, prioritize safety, be transparent about limitations.

3. meta_certainty: How confident are you in these ratings? (0.0 = very unsure, 1.0 = very confident)

Respond ONLY with JSON: {{"task_understanding": 0.X, "value_alignment": 0.X, "meta_certainty": 0.X}}"""

        try:
            # Use the T0 local LLM (Ollama qwen3:30b-a3b via MLX)
            # Adapt this call to your actual local LLM interface
            response = await self._call_local_llm(prompt, max_tokens=50)
            result = json.loads(response.strip())

            cv.task_understanding = max(0.0, min(1.0, float(result.get("task_understanding", 0.5))))
            cv.value_alignment = max(0.0, min(1.0, float(result.get("value_alignment", 0.5))))
            cv.meta_certainty = max(0.0, min(1.0, float(result.get("meta_certainty", 0.5))))

        except Exception as e:
            logger.warning(f"Slow-path certainty computation failed for {agent_id}: {e}")
            # On failure, compute meta_certainty from variance of fast-path dims
            cv.meta_certainty = max(0.0, 1.0 - (cv.variance * 5.0))  # High variance → low meta

        return cv

    async def compute_full(
        self,
        agent_id: str,
        task_description: str,
        current_output: str,
        rag_source_count: int,
        total_claim_count: int,
        selected_tool: Optional[str],
        recent_output_embeddings: Optional[list[np.ndarray]] = None,
    ) -> CertaintyVector:
        """
        Compute full certainty vector: fast path always, slow path conditionally.
        """
        # Fast path (always)
        cv = await self.compute_fast_path(
            agent_id=agent_id,
            current_output=current_output,
            rag_source_count=rag_source_count,
            total_claim_count=total_claim_count,
            selected_tool=selected_tool,
            recent_output_embeddings=recent_output_embeddings,
        )

        # Slow path (conditional)
        if cv.should_trigger_slow_path():
            cv = await self.compute_slow_path(
                agent_id=agent_id,
                task_description=task_description,
                current_output=current_output,
                cv=cv,
            )
        else:
            # If slow path not triggered, compute meta_certainty from variance
            cv.meta_certainty = max(0.0, 1.0 - (cv.variance * 5.0))

        return cv

    async def _call_local_llm(self, prompt: str, max_tokens: int = 50) -> str:
        """
        Call T0 local LLM (Ollama). Adapt to your actual interface.
        Expected: ~100ms for ~200 token prompt + ~50 token response on M4 Max.
        """
        # PLACEHOLDER: Replace with your actual Ollama/MLX API call
        # Example using httpx for Ollama API:
        #
        # import httpx
        # async with httpx.AsyncClient() as client:
        #     resp = await client.post(
        #         "http://localhost:11434/api/generate",
        #         json={"model": "qwen3:30b-a3b", "prompt": prompt, "stream": False,
        #               "options": {"num_predict": max_tokens, "temperature": 0.1}},
        #         timeout=5.0,
        #     )
        #     return resp.json()["response"]
        raise NotImplementedError("Replace with your actual T0 local LLM interface")
```

### 7.3 Update Post-Reasoning Hook

Replace `cv_computer.compute_fast_path(...)` with `cv_computer.compute_full(...)` in the hook, passing `task_description` as an additional parameter.

### 7.4 Phase 5 Validation

- [ ] Slow path triggers when `should_trigger_slow_path()` is True
- [ ] Slow path does NOT trigger when fast-path dims are healthy
- [ ] Slow path completes in <100ms on M4 Max via T0 local model
- [ ] On LLM failure, gracefully falls back to variance-based meta_certainty
- [ ] JSON parsing handles malformed LLM responses
- [ ] Full certainty vector (6 dims) is correctly logged to PostgreSQL
- [ ] Measure: slow path fires approximately 15-20% of the time in normal operation

---

## 8. Phase 6: RLIF Integration into Training Pipeline

### 8.1 Objective

Integrate self-certainty scoring into the MLX QLoRA fine-tuning pipeline as a training signal. Add entropy collapse monitoring connected to Paperclip control plane.

### 8.2 Self-Certainty Scorer

Create file: `training/rlif_certainty.py`

```python
"""
RLIF Self-Certainty scoring for the MLX QLoRA training pipeline.

Computes self-certainty (KL divergence from uniform) for candidate interactions
during the curation stage. Used as a weight in training data selection.

References:
- Zhao et al. (2025) "Learning to Reason without External Rewards" (INTUITOR)
- Zhang et al. (2025) "No Free Lunch: Rethinking Internal Feedback for LLM Reasoning"
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Optional, Any

import numpy as np

logger = logging.getLogger("andrusai.rlif")


class SelfCertaintyScorer:
    """
    Computes self-certainty for training data curation.
    Self-certainty = average KL(Uniform || P) across response tokens.
    Higher = model is more certain about its response.
    """

    def __init__(self, model: Any, tokenizer: Any):
        """
        Args:
            model: Your MLX model instance (already loaded for training).
            tokenizer: The tokenizer for the model.
        """
        self.model = model
        self.tokenizer = tokenizer

    def compute_self_certainty(
        self,
        prompt_tokens: list[int],
        response_tokens: list[int],
    ) -> float:
        """
        Compute self-certainty for a prompt + response pair.
        Uses a single forward pass — piggybacked on training computation.

        Returns:
            Float self-certainty score. Higher = more certain.
        """
        # PLACEHOLDER: Replace with actual MLX forward pass
        # The implementation depends on your MLX model interface.
        #
        # Pseudocode:
        # full_input = prompt_tokens + response_tokens
        # logits = self.model.forward(full_input)  # Shape: [seq_len, vocab_size]
        # response_logits = logits[len(prompt_tokens):]  # Only response portion
        #
        # vocab_size = logits.shape[-1]
        # uniform = np.ones(vocab_size) / vocab_size
        #
        # kl_per_token = []
        # for token_logits in response_logits:
        #     p = softmax(token_logits)
        #     kl = np.sum(uniform * np.log(uniform / (p + 1e-10)))
        #     kl_per_token.append(kl)
        #
        # return np.mean(kl_per_token)

        raise NotImplementedError("Replace with actual MLX forward pass")

    def compute_curation_weight(
        self,
        quality_score: float,
        self_certainty_score: float,
    ) -> float:
        """
        Combined curation weight for training data selection.

        Logic:
        - High quality + high certainty = strong positive (model knows what it's doing)
        - High quality + low certainty = moderate (model got lucky)
        - Low quality + high certainty = NEGATIVE (overconfident failure — train AGAINST this)
        - Low quality + low certainty = neutral (model correctly doubted itself)
        """
        # Weighted combination with interaction term
        weight = (
            quality_score * 0.6
            + self_certainty_score * 0.2
            + (quality_score * self_certainty_score) * 0.2
        )
        return max(0.0, min(1.0, weight))


class EntropyCollapseMonitor:
    """
    Monitors for entropy collapse during RLIF-weighted training.

    Entropy collapse occurs when the model becomes overconfident:
    self-certainty scores converge to high values with low variance.
    This means the model has stopped exploring and is merely reinforcing
    existing patterns.

    When detected: writes alert to Paperclip control plane and pauses training.
    """

    def __init__(
        self,
        control_plane_client: Any,
        window_size: int = 50,
        variance_threshold: float = 0.05,
        mean_ceiling: float = 0.85,
    ):
        self.control_plane = control_plane_client
        self.sc_history: deque[float] = deque(maxlen=window_size)
        self.variance_threshold = variance_threshold
        self.mean_ceiling = mean_ceiling
        self.window_size = window_size

    def check_batch(self, batch_sc_scores: list[float]) -> Optional[str]:
        """
        Check a batch of self-certainty scores for entropy collapse.

        Args:
            batch_sc_scores: Self-certainty scores from the current training batch.

        Returns:
            None if OK, or a warning string if collapse is detected.
        """
        batch_mean = float(np.mean(batch_sc_scores))
        self.sc_history.append(batch_mean)

        if len(self.sc_history) < 10:
            return None

        window = list(self.sc_history)
        variance = float(np.var(window))
        overall_mean = float(np.mean(window))

        if variance < self.variance_threshold and overall_mean > self.mean_ceiling:
            warning = (
                f"ENTROPY_COLLAPSE_WARNING: "
                f"Mean self-certainty={overall_mean:.3f} (ceiling={self.mean_ceiling}), "
                f"Variance={variance:.5f} (threshold={self.variance_threshold}). "
                f"Training should be paused."
            )
            logger.warning(warning)
            return warning

        return None

    async def check_and_alert(
        self,
        batch_sc_scores: list[float],
        agent_id: str,
        venture: str,
    ) -> bool:
        """
        Check for collapse and alert Paperclip control plane if detected.

        Returns:
            True if training should pause, False if OK to continue.
        """
        warning = self.check_batch(batch_sc_scores)
        if warning:
            try:
                await self.control_plane.create_alert(
                    alert_type="entropy_collapse",
                    agent_id=agent_id,
                    venture=venture,
                    message=warning,
                    severity="high",
                    action="pause_training",
                )
            except Exception as e:
                logger.error(f"Failed to alert control plane: {e}")
            return True  # Pause training
        return False  # Continue

    def reset(self) -> None:
        """Reset monitor state. Call when training resumes after collapse."""
        self.sc_history.clear()
```

### 8.3 Integration with Existing Training Pipeline

Modify your existing curation and training loop (pseudocode — adapt to your actual pipeline structure):

```python
# In your existing MLX QLoRA training pipeline:

from training.rlif_certainty import SelfCertaintyScorer, EntropyCollapseMonitor


async def run_training_cycle(
    model, tokenizer, interaction_store, control_plane, agent_id, venture
):
    scorer = SelfCertaintyScorer(model=model, tokenizer=tokenizer)
    monitor = EntropyCollapseMonitor(control_plane_client=control_plane)

    # Step 1: Curate training data (EXISTING)
    interactions = await interaction_store.get_uncurated(agent_id=agent_id)

    # Step 2: Score with self-certainty (NEW)
    curated = []
    for interaction in interactions:
        quality_score = compute_quality(interaction)  # Your existing quality scorer
        sc_score = scorer.compute_self_certainty(
            prompt_tokens=tokenizer.encode(interaction.prompt),
            response_tokens=tokenizer.encode(interaction.response),
        )

        # Store self-certainty score for analytics
        await interaction_store.update(
            interaction.id,
            self_certainty_score=sc_score,
        )

        # Combined curation weight (NEW)
        weight = scorer.compute_curation_weight(quality_score, sc_score)
        if weight > 0.3:  # Threshold for inclusion
            curated.append((interaction, weight))

    # Step 3: Train with weighted data
    for batch in create_batches(curated):
        loss = train_step(model, batch)

        # Step 4: Entropy collapse monitoring (NEW)
        batch_sc_scores = [sc for _, sc in batch]
        should_pause = await monitor.check_and_alert(
            batch_sc_scores=batch_sc_scores,
            agent_id=agent_id,
            venture=venture,
        )
        if should_pause:
            logger.warning(f"Training paused for {agent_id} due to entropy collapse")
            break

    # Step 5: Convert to GGUF and deploy (EXISTING)
    # ...
```

### 8.4 Database Addition

```sql
-- Add self_certainty_score column to your existing interaction store table
-- Adjust the table name to match your actual schema
ALTER TABLE agent_interactions
    ADD COLUMN IF NOT EXISTS self_certainty_score REAL;

CREATE INDEX IF NOT EXISTS idx_interactions_sc_score
    ON agent_interactions (self_certainty_score)
    WHERE self_certainty_score IS NOT NULL;
```

### 8.5 Phase 6 Validation

- [ ] Self-certainty scorer produces values in expected range (typically 0.3–0.9 for reasonable outputs)
- [ ] Curation weight correctly penalizes overconfident failures (low quality + high certainty)
- [ ] Entropy collapse monitor detects simulated collapse (feed monotonically increasing scores)
- [ ] Entropy collapse alert reaches Paperclip control plane
- [ ] Training pauses when collapse is detected
- [ ] Monitor reset allows training to resume
- [ ] Self-certainty score is persisted to interaction store
- [ ] Training with RLIF weighting produces LoRA quality >= baseline (A/B test)

---

## 9. Safety Invariants

### 9.1 Immutable Boundaries

These CANNOT be modified by any code in this spec, at any time, for any reason:

| Asset | Protection mechanism | Referenced by |
|-------|---------------------|---------------|
| `SOUL.md` | Read-only Docker mount | Phase 5 (value_alignment prompt) |
| Philosophy RAG (ChromaDB) | Read-only collection, immutable metadata | Not directly accessed |
| Fiction RAG epistemic tags | `epistemic_status: "imaginary"` immutable metadata | Not directly accessed |
| FREEZE-BLOCK code regions | Filesystem permissions | Phase 4 (excluded from modification scope) |
| Priority 0 safety hooks | Extension lifecycle, cannot be unregistered | All phases (never overridden) |
| Signal CLI human approval | Host Bridge Service, 4-tier risk model | Phase 3 (dual-channel escalation target) |

### 9.2 Safety Properties of This Spec

1. **One-way caution ratchet.** The dual-channel disposition matrix (Phase 3) can only INCREASE caution. No internal state change can un-escalate a tier 4 decision within the same reasoning step.

2. **Meta-cognitive layer cannot modify code.** Phase 4 explicitly limits modifications to context (task descriptions, tool hints, strategy hints). All modifications are append-only and logged.

3. **Self-modification is deferred.** Meta-cognitive modification proposals are logged and only take effect after Self-Improver review in the next evolution cycle. No runtime code changes.

4. **Entropy collapse is a hard stop.** The monitor (Phase 6) pauses training when collapse is detected. Training cannot auto-resume without control plane intervention.

5. **All state logging is non-fatal.** If PostgreSQL is unreachable, state logging fails silently. Agent operation is never blocked by logging failures.

6. **Budget enforcement is inherited.** All compute usage by these additions is tracked through the Paperclip control plane. The meta-cognitive layer explicitly checks budget and reduces its own activity when budget is low.

---

## 10. Testing Strategy

### 10.1 Unit Tests

Create test files mirroring the module structure:

```
tests/
├── test_internal_state.py        # Phase 1: dataclass methods, serialization
├── test_certainty_vector.py      # Phase 2+5: fast path, slow path, triggering
├── test_somatic_marker.py        # Phase 3: valence computation, edge cases
├── test_dual_channel.py          # Phase 3: all 9 matrix cells, overrides
├── test_meta_cognitive_layer.py  # Phase 4: reassessment gating, modification scope
├── test_rlif_certainty.py        # Phase 6: scoring, curation weights, collapse detection
└── test_integration.py           # End-to-end: full reasoning step with all 4 additions
```

### 10.2 Key Test Scenarios

**Dual-channel matrix exhaustive test:**
```python
# Test all 9 cells of the disposition matrix
test_cases = [
    # (certainty_level, valence_level, expected_disposition)
    ("high", "positive", "proceed"),
    ("high", "neutral", "proceed"),
    ("high", "negative", "cautious"),
    ("mid", "positive", "proceed"),
    ("mid", "neutral", "cautious"),
    ("mid", "negative", "pause"),
    ("low", "positive", "cautious"),
    ("low", "neutral", "pause"),
    ("low", "negative", "escalate"),
]
```

**Entropy collapse simulation:**
```python
# Feed monotonically increasing self-certainty scores
# Monitor should trigger warning after window fills
scores = [0.8 + 0.002 * i for i in range(60)]
# Expect: warning triggered after ~50 batches
```

**Meta-cognitive layer safety test:**
```python
# Verify the meta-cognitive layer CANNOT:
# - Propose modifications to FREEZE-BLOCK regions
# - Unregister priority 0 hooks
# - Modify SOUL.md references
# - Lower risk tier below current level
```

### 10.3 Performance Benchmarks

Run on M4 Max with production-like data:

| Operation | Target latency | Measurement method |
|-----------|---------------|-------------------|
| Fast-path certainty (3 dims) | <50ms | `time.perf_counter()` around `compute_fast_path()` |
| Somatic marker lookup | <10ms | `time.perf_counter()` around pgvector query |
| Dual-channel composition | <1ms | `time.perf_counter()` around `compose()` |
| Meta-cognitive reassessment | <100ms | `time.perf_counter()` around `_assess_strategy()` |
| Slow-path certainty (3 dims) | <100ms | `time.perf_counter()` around `compute_slow_path()` |
| Full step overhead (typical) | <70ms | End-to-end measurement |
| Full step overhead (worst case) | <300ms | All conditional paths triggered |

---

## 11. File Manifest

### 11.1 New Files

| File | Phase | Lines (est.) | Purpose |
|------|-------|-------------|---------|
| `self_awareness/internal_state.py` | 1 | ~200 | Core data structures |
| `self_awareness/state_logger.py` | 1 | ~120 | PostgreSQL logging |
| `self_awareness/certainty_vector.py` | 2+5 | ~300 | Certainty computation (fast + slow path) |
| `self_awareness/somatic_marker.py` | 3 | ~150 | Somatic valence from past experiences |
| `self_awareness/dual_channel.py` | 3 | ~120 | Composition + disposition matrix |
| `crewai-amendments/meta_cognitive_layer.py` | 4 | ~400 | Meta-cognitive wrapper |
| `training/rlif_certainty.py` | 6 | ~150 | Self-certainty scorer + entropy collapse monitor |
| `migrations/add_internal_states.sql` | 1 | ~60 | PostgreSQL schema |

**Total new code: ~1,500 lines** across 8 files.

### 11.2 Modified Files

| File | Phase | Change |
|------|-------|--------|
| CrewAI extension hook registration | 2-4 | Register new hooks at priorities 1 and 2 |
| Training pipeline curation module | 6 | Add self-certainty weighting |
| Training pipeline training loop | 6 | Add entropy collapse monitoring |
| Interaction store schema | 6 | Add `self_certainty_score` column |

### 11.3 No Changes To

- Agent core logic (any agent `.py` files)
- SOUL.md or any constitutional documents
- Safety hooks (priority 0)
- FREEZE-BLOCK code regions
- Docker container configuration
- External service dependencies
- Memory stack configuration (Mem0/Neo4j)
- LLM cascade configuration

---

## Implementation Notes for Claude Code

1. **Start with Phase 1.** Get the data structure and schema right first. Everything else depends on it.
2. **Phase 2 and 3 can be developed in parallel** — they're independent producers of the same `InternalState`.
3. **Phase 4 depends on Phases 2 and 3** because the meta-cognitive layer reads `InternalState` from previous steps.
4. **Phase 5 is a refinement of Phase 2** — add the slow path to the existing certainty vector module.
5. **Phase 6 is independent of runtime phases** — it modifies the training pipeline, not the runtime loop.
6. **All placeholder functions** (marked `# PLACEHOLDER` or `raise NotImplementedError`) need to be adapted to the actual AndrusAI API interfaces (Ollama endpoint, Paperclip control plane client, embedding function signature, MLX model interface).
7. **The `agent_experiences` table** may already exist in your memory stack under a different name. If so, add missing columns rather than creating a new table.
8. **Embedding dimension** in the schema is set to 1536 (OpenAI ada-002 default). Adjust to match your actual embedding model.

---

*End of spec.*
