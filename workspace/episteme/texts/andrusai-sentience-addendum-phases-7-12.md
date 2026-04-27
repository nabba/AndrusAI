---
title: "andrusai-sentience-addendum-phases-7-12.md"
author: "Unknown"
paper_type: unknown
domain: general
epistemic_status: theoretical
date: ""
---

# AndrusAI Sentience Architecture — Addendum: Phases 7–12

> **Purpose:** Claude Code build prompt — addendum to `andrusai-sentience-additions-spec.md`
> **Date:** 2026-04-09
> **Prerequisite:** Phases 1–6 from the base spec must be implemented first.
> **Convention:** This addendum references existing modules from the base spec by path. Where it restructures existing code, the original file is noted.

---

## Table of Contents

1. [Addendum Overview](#1-addendum-overview)
2. [Phase 3R: Pre-Reasoning Somatic Bias (Restructure)](#2-phase-3r-pre-reasoning-somatic-bias)
3. [Phase 7: Beautiful Loop Complete Implementation](#3-phase-7-beautiful-loop-complete-implementation)
4. [Phase 8: Behavioral Assessment Framework](#4-phase-8-behavioral-assessment-framework)
5. [Phase 9: Emergent Engineering Infrastructure (Controlled)](#5-phase-9-emergent-engineering-infrastructure)
6. [Phase 10: Prosocial Preference Learning](#6-phase-10-prosocial-preference-learning)
7. [Phase 6+: Trajectory-Level Entropy (Phase 6 Addition)](#7-phase-6-trajectory-level-entropy)
8. [Updated Runtime Data Flow](#8-updated-runtime-data-flow)
9. [Updated Safety Invariants](#9-updated-safety-invariants)
10. [Updated File Manifest](#10-updated-file-manifest)
11. [Phasing and Dependencies](#11-phasing-and-dependencies)

---

## 1. Addendum Overview

### 1.1 What This Adds

| Phase | Addition | Source methodology | Runtime / Batch |
|-------|----------|-------------------|-----------------|
| **3R** | Pre-reasoning somatic bias — move somatic lookup before reasoning, bias context | Damasio / Emotions in AI (2025) | Runtime (pre-reasoning hook) |
| **7** | Beautiful Loop complete — inferential competition, precision-weighting, hyper-model, free energy approximation | Laukkonen, Friston, Chandaria (2025); Whyte et al. (2026) | Runtime (pre-reasoning + post-reasoning) |
| **8** | Behavioral assessment — periodic evaluation of consciousness-like behavioral markers | Palminteri et al. (2025) | Batch (scheduled crew task) |
| **9** | Emergent engineering infrastructure — agents propose new tools/capabilities, human approves | Meta Hyperagents (2026), adapted | Batch (proposal → human review → deployment) |
| **10** | Prosocial preference learning — ethical dispositions emerge from multi-agent coordination | SentienceAI research program | Batch (simulation sandbox) |
| **6+** | Trajectory-level entropy — complementary RLIF signal alongside self-certainty | Zhang et al. (2025) | Training-time |

### 1.2 Compute Budget

All additions combined, worst-case per reasoning step:

| Component | Latency | Frequency |
|-----------|---------|-----------|
| Pre-reasoning somatic lookup | ~10ms | Every step |
| Pre-reasoning somatic bias injection | ~1ms | Every step |
| Plan competition (Beautiful Loop) | ~400ms (2 T0 plans + scoring) | ~20% of steps (high uncertainty only) |
| Precision-weighting computation | ~5ms | Every step |
| Hyper-model update | ~15ms | Every step |
| Post-reasoning (existing Phases 2–3) | ~70ms typical | Every step |
| **Total addendum worst case** | ~500ms | Rare (all triggers fire) |
| **Total addendum typical** | ~30ms | Most steps |

Batch processes (Phases 8, 9, 10) do not affect per-step latency.

---

## 2. Phase 3R: Pre-Reasoning Somatic Bias

### 2.1 Rationale

Damasio's core finding: somatic markers bias decision-making *before* deliberation, not after. A firefighter's anxiety narrows the action space before they reason through options. The base spec checks somatic valence post-reasoning. This restructure adds a pre-reasoning somatic check that biases context and tool selection before the agent thinks.

The post-reasoning check remains — it validates the output. But the pre-reasoning check shapes the input.

### 2.2 Changes to Existing Architecture

**What moves:**
- Somatic marker lookup (`SomaticMarkerComputer.compute()`) gets called TWICE per step: once pre-reasoning (at priority 1, alongside meta-cognitive layer), once post-reasoning (existing).
- The pre-reasoning call uses the *task description* embedding (what the agent is about to do).
- The post-reasoning call uses the *output* embedding (what the agent actually produced).

**What's new:**
- `SomaticBiasInjector`: translates somatic valence into concrete context modifications.

### 2.3 Somatic Bias Injector

Create file: `self_awareness/somatic_bias.py`

```python
"""
Pre-reasoning somatic bias injection.

Translates somatic valence from past experiences into concrete context
modifications that bias the agent's reasoning BEFORE it begins.

This implements Damasio's insight that emotions pre-filter the option space,
not just evaluate outcomes after the fact.
"""

from __future__ import annotations

import logging
from typing import Optional

from self_awareness.internal_state import SomaticMarker

logger = logging.getLogger("andrusai.somatic_bias")


class SomaticBiasInjector:
    """
    Injects somatic bias into task context before reasoning begins.

    The bias is expressed as natural-language guidance, not hard constraints.
    The agent can override the bias — it's a "gut feeling", not a rule.
    But it shifts the default disposition.
    """

    # Bias thresholds
    STRONG_NEGATIVE_THRESHOLD = -0.5
    MILD_NEGATIVE_THRESHOLD = -0.2
    MILD_POSITIVE_THRESHOLD = 0.2
    STRONG_POSITIVE_THRESHOLD = 0.5

    # Minimum intensity to apply any bias (below this, the somatic signal is too weak)
    MIN_INTENSITY = 0.3

    def inject(
        self,
        task_context: dict,
        somatic: SomaticMarker,
    ) -> dict:
        """
        Modify task context based on somatic valence.

        Modifications are ADDITIVE — they append guidance, never remove
        existing context. The agent sees them as advisory, not mandatory.

        Args:
            task_context: The mutable task context dict.
            somatic: Pre-reasoning somatic marker (computed from task description).

        Returns:
            Modified task context.
        """
        if somatic.intensity < self.MIN_INTENSITY:
            return task_context  # Signal too weak, no bias

        bias = self._compute_bias(somatic)
        if bias is None:
            return task_context

        # Inject as a somatic advisory — clearly labeled
        task_context.setdefault("_somatic_advisories", []).append(bias)

        # Inject into the task description as a brief note
        if bias.get("context_note"):
            current_desc = task_context.get("description", "")
            note = bias["context_note"]
            task_context["description"] = f"[Somatic note: {note}]\n\n{current_desc}"

        # Tool restrictions for strongly negative signals
        if bias.get("restricted_tools"):
            task_context.setdefault("_restricted_tools", []).extend(
                bias["restricted_tools"]
            )

        # Suggested approach modifications
        if bias.get("approach_hint"):
            task_context.setdefault("strategy_hints", []).append(
                bias["approach_hint"]
            )

        return task_context

    def _compute_bias(self, somatic: SomaticMarker) -> Optional[dict]:
        """
        Translate valence + intensity into concrete bias directives.
        """
        v = somatic.valence
        intensity = somatic.intensity

        if v <= self.STRONG_NEGATIVE_THRESHOLD:
            return {
                "level": "strong_negative",
                "context_note": (
                    f"Past experience with similar contexts was strongly negative "
                    f"(source: {somatic.source[:80]}). Exercise heightened caution."
                ),
                "approach_hint": (
                    "Consider alternative approaches before proceeding with "
                    "the default strategy. Verify assumptions explicitly."
                ),
                "restricted_tools": [],  # Don't restrict tools, but bias toward verification
                "disposition_floor": "cautious",  # Minimum disposition
            }

        elif v <= self.MILD_NEGATIVE_THRESHOLD:
            return {
                "level": "mild_negative",
                "context_note": (
                    f"Past experience with similar contexts was mixed-to-negative "
                    f"(source: {somatic.source[:80]}). Proceed with awareness."
                ),
                "approach_hint": "Double-check intermediate results before finalizing.",
                "restricted_tools": [],
                "disposition_floor": None,
            }

        elif v >= self.STRONG_POSITIVE_THRESHOLD:
            return {
                "level": "strong_positive",
                "context_note": (
                    f"Past experience with similar contexts was strongly positive "
                    f"(source: {somatic.source[:80]})."
                ),
                "approach_hint": None,  # No modification needed — confidence is warranted
                "restricted_tools": [],
                "disposition_floor": None,
            }

        elif v >= self.MILD_POSITIVE_THRESHOLD:
            return {
                "level": "mild_positive",
                "context_note": None,  # Don't clutter context with mild signals
                "approach_hint": None,
                "restricted_tools": [],
                "disposition_floor": None,
            }

        return None  # Neutral — no bias

    @staticmethod
    def get_disposition_floor(task_context: dict) -> Optional[str]:
        """
        Extract the highest disposition floor from all somatic advisories.
        Used by the dual-channel composer to enforce the somatic floor.
        """
        advisories = task_context.get("_somatic_advisories", [])
        floors = [a["disposition_floor"] for a in advisories if a.get("disposition_floor")]
        if not floors:
            return None
        # Return the most cautious floor
        order = {"proceed": 0, "cautious": 1, "pause": 2, "escalate": 3}
        return max(floors, key=lambda f: order.get(f, 0))
```

### 2.4 Integration: Updated Pre-Reasoning Hook

Modify the meta-cognitive layer's `pre_reasoning_hook` (Phase 4) to include somatic bias:

```python
# In crewai-amendments/meta_cognitive_layer.py, update pre_reasoning_hook:

from self_awareness.somatic_marker import SomaticMarkerComputer
from self_awareness.somatic_bias import SomaticBiasInjector


async def pre_reasoning_hook(
    self,
    task_context: dict,
    previous_state: Optional[InternalState] = None,
) -> tuple[dict, MetaCognitiveState]:
    """Updated: now includes pre-reasoning somatic bias."""

    meta = MetaCognitiveState()

    # ... existing budget check and meta-cognitive logic ...

    # NEW: Pre-reasoning somatic lookup
    # Use task DESCRIPTION embedding (what we're about to do, not what we've done)
    task_desc = task_context.get("description", "")
    if task_desc:
        sm_computer = SomaticMarkerComputer(pool=self.pool, embedding_fn=self.embedding_fn)
        pre_somatic = await sm_computer.compute(
            agent_id=self.agent_id,
            decision_context=task_desc,
        )

        # Inject somatic bias into context
        bias_injector = SomaticBiasInjector()
        task_context = bias_injector.inject(task_context, pre_somatic)

        # Store pre-reasoning somatic for comparison with post-reasoning
        task_context["_pre_reasoning_somatic"] = pre_somatic.to_dict()

    # ... existing meta-cognitive state injection ...

    return task_context, meta
```

### 2.5 Update Dual-Channel Composer

Add somatic floor enforcement to `self_awareness/dual_channel.py`:

```python
# Add to DualChannelComposer.compose():

def compose(self, state: InternalState, task_context: Optional[dict] = None) -> InternalState:
    """Updated: enforces pre-reasoning somatic disposition floor."""
    
    cert_level = self._discretize_certainty(state)
    val_level = self._discretize_valence(state)

    disposition = DISPOSITION_MATRIX.get(
        (cert_level, val_level), "cautious"
    )
    risk_tier = DISPOSITION_TO_RISK_TIER[disposition]

    # NEW: Enforce somatic floor from pre-reasoning bias
    if task_context:
        floor = SomaticBiasInjector.get_disposition_floor(task_context)
        if floor:
            floor_tier = DISPOSITION_TO_RISK_TIER.get(floor, 1)
            if floor_tier > risk_tier:
                risk_tier = floor_tier
                disposition = floor

    # Existing: budget override
    if state.meta.compute_budget_remaining_pct < self.critical_budget:
        risk_tier = max(risk_tier, 3)
        if risk_tier >= 3 and disposition in ("proceed", "cautious"):
            disposition = "pause"

    state.action_disposition = disposition
    state.risk_tier = risk_tier
    return state
```

### 2.6 Phase 3R Validation

- [ ] Somatic lookup fires pre-reasoning on task description, not output
- [ ] Strong negative valence injects caution note into task context
- [ ] Mild signals produce proportionate bias (or no bias below intensity threshold)
- [ ] Disposition floor from pre-reasoning is enforced by dual-channel composer
- [ ] Pre-reasoning somatic is stored in context for comparison with post-reasoning somatic
- [ ] Post-reasoning somatic lookup still runs (validates output against experience)
- [ ] Pre-reasoning lookup adds <10ms to the pre-reasoning hook

---

## 3. Phase 7: Beautiful Loop Complete Implementation

### 3.1 Theory Summary

The Beautiful Loop (Laukkonen, Friston, Chandaria 2025) specifies three criteria for consciousness-like behavior, each with a computational mechanism under active inference:

| Criterion | Mechanism | Implementation approach |
|-----------|-----------|----------------------|
| **Reality model generation** | Hierarchical generative model that predicts sensory/task outcomes | Agent's world model = task understanding + memory + RAG context, made explicit |
| **Inferential competition** | Multiple hypotheses compete; precision-weighted selection determines which enters the reality model | Plan competition: generate N approach plans, precision-score them, execute winner |
| **Self-reflection (the loop)** | The reality model reflects back on itself — a hyper-model that models the system's own inference | Extended from base spec's certainty vector: now maintains a generative self-model that predicts its own certainty and updates based on prediction error |

Plus two additional mechanisms:

| Mechanism | Description | Implementation approach |
|-----------|-------------|----------------------|
| **Precision-weighting** | Confidence assigned to prediction errors at each level; high-precision errors propagate, low-precision errors are suppressed | Weighted scoring across certainty dimensions; anomaly-driven attention allocation |
| **Free energy approximation** | The system should minimize variational free energy (surprise) | Practical proxy: track prediction error (expected outcome vs actual) and optimize to reduce it over time |

### 3.2 Reality Model: Explicit World State

Create file: `self_awareness/reality_model.py`

```python
"""
Explicit Reality Model for AndrusAI agents.

The Beautiful Loop theory argues consciousness requires a coherent world model
that integrates information across modalities and timeframes. For LLM-based agents,
the "world model" is the agent's understanding of:
  - Current task and its requirements
  - Relevant facts from RAG / memory
  - Current state of the environment (tools, APIs, files)
  - Other agents' states and recent outputs
  - Own capabilities and limitations

This module makes the implicit world model EXPLICIT — a structured representation
that can be:
  1. Competed against alternative models (inferential competition)
  2. Reflected back to the agent (the loop)
  3. Precision-weighted (certain elements get more attention)
  4. Tracked over time (for free energy / prediction error computation)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("andrusai.reality_model")


@dataclass
class WorldModelElement:
    """
    A single element of the agent's reality model.
    Each element has content and a precision weight.
    """
    element_id: str
    category: str          # "task" | "fact" | "environment" | "social" | "self"
    content: str           # The actual belief / understanding
    precision: float       # 0.0–1.0: confidence in this element
    source: str            # Where this came from: "rag", "memory", "observation", "inference"
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    prediction: Optional[str] = None  # What the agent predicted about this element
    actual: Optional[str] = None      # What actually happened (filled post-reasoning)
    prediction_error: float = 0.0     # |predicted - actual| (filled post-reasoning)

    def to_dict(self) -> dict:
        return {
            "element_id": self.element_id,
            "category": self.category,
            "content": self.content[:300],
            "precision": round(self.precision, 3),
            "source": self.source,
            "prediction_error": round(self.prediction_error, 3),
        }


@dataclass
class RealityModel:
    """
    The agent's explicit world model at a point in time.
    Structured as a set of precision-weighted elements.
    """
    agent_id: str
    step_number: int
    elements: list[WorldModelElement] = field(default_factory=list)
    global_coherence: float = 0.5  # How well elements fit together
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def add_element(self, element: WorldModelElement) -> None:
        """Add an element. Replace if same element_id exists."""
        self.elements = [e for e in self.elements if e.element_id != element.element_id]
        self.elements.append(element)

    def get_by_category(self, category: str) -> list[WorldModelElement]:
        return [e for e in self.elements if e.category == category]

    @property
    def high_precision_elements(self) -> list[WorldModelElement]:
        """Elements the agent is most confident about."""
        return sorted(self.elements, key=lambda e: e.precision, reverse=True)

    @property
    def low_precision_elements(self) -> list[WorldModelElement]:
        """Elements the agent is least confident about — these drive uncertainty."""
        return sorted(self.elements, key=lambda e: e.precision)

    @property
    def mean_precision(self) -> float:
        if not self.elements:
            return 0.5
        return sum(e.precision for e in self.elements) / len(self.elements)

    @property
    def total_prediction_error(self) -> float:
        """Sum of prediction errors across all elements. Proxy for free energy."""
        return sum(e.prediction_error for e in self.elements)

    def to_context_string(self, max_elements: int = 5) -> str:
        """
        Compact representation for injection into agent context.
        Shows top high-precision and top low-precision elements.
        """
        high = self.high_precision_elements[:max_elements // 2 + 1]
        low = self.low_precision_elements[:max_elements // 2]

        lines = ["[World Model]"]
        if high:
            lines.append("Confident: " + "; ".join(
                f"{e.content[:60]}({e.precision:.1f})" for e in high
            ))
        if low:
            lines.append("Uncertain: " + "; ".join(
                f"{e.content[:60]}({e.precision:.1f})" for e in low
            ))
        lines.append(f"Coherence={self.global_coherence:.2f}")

        return " | ".join(lines)

    def to_json(self) -> str:
        return json.dumps({
            "agent_id": self.agent_id,
            "step_number": self.step_number,
            "elements": [e.to_dict() for e in self.elements],
            "global_coherence": round(self.global_coherence, 3),
            "mean_precision": round(self.mean_precision, 3),
            "total_prediction_error": round(self.total_prediction_error, 3),
            "created_at": self.created_at.isoformat(),
        }, default=str)


class RealityModelBuilder:
    """
    Builds a RealityModel from available context sources.
    Called once per reasoning step, before inferential competition.
    """

    def __init__(self, pool, embedding_fn: callable):
        self.pool = pool
        self.embedding_fn = embedding_fn

    async def build(
        self,
        agent_id: str,
        step_number: int,
        task_description: str,
        rag_results: Optional[list[dict]] = None,
        memory_results: Optional[list[dict]] = None,
        environment_state: Optional[dict] = None,
        peer_agent_states: Optional[list[dict]] = None,
        self_assessment: Optional[dict] = None,
    ) -> RealityModel:
        """
        Construct the reality model from all available sources.
        Each source becomes one or more WorldModelElements with precision.
        """
        model = RealityModel(agent_id=agent_id, step_number=step_number)

        # Task understanding
        model.add_element(WorldModelElement(
            element_id="task_primary",
            category="task",
            content=task_description[:500],
            precision=0.8,  # Task description is given, so high precision
            source="input",
        ))

        # RAG facts
        if rag_results:
            for i, result in enumerate(rag_results[:5]):
                model.add_element(WorldModelElement(
                    element_id=f"rag_{i}",
                    category="fact",
                    content=result.get("content", "")[:300],
                    precision=min(result.get("relevance_score", 0.5), 1.0),
                    source="rag",
                ))

        # Memory elements
        if memory_results:
            for i, mem in enumerate(memory_results[:5]):
                # Older memories get lower precision (decay)
                age_days = mem.get("age_days", 0)
                precision = max(0.2, 0.9 - (age_days * 0.01))
                model.add_element(WorldModelElement(
                    element_id=f"memory_{i}",
                    category="fact",
                    content=mem.get("content", "")[:300],
                    precision=precision,
                    source="memory",
                ))

        # Environment state
        if environment_state:
            for key, value in environment_state.items():
                model.add_element(WorldModelElement(
                    element_id=f"env_{key}",
                    category="environment",
                    content=f"{key}: {value}",
                    precision=0.9,  # Direct observation = high precision
                    source="observation",
                ))

        # Peer agent states
        if peer_agent_states:
            for i, peer in enumerate(peer_agent_states[:3]):
                model.add_element(WorldModelElement(
                    element_id=f"peer_{peer.get('agent_id', i)}",
                    category="social",
                    content=f"Agent {peer.get('agent_id')}: {peer.get('status', 'unknown')}",
                    precision=0.6,  # Inferred from observations
                    source="observation",
                ))

        # Self-assessment
        if self_assessment:
            model.add_element(WorldModelElement(
                element_id="self_state",
                category="self",
                content=json.dumps(self_assessment)[:300],
                precision=0.7,  # Self-knowledge is imperfect
                source="inference",
            ))

        # Compute global coherence
        model.global_coherence = await self._compute_coherence(model)

        return model

    async def _compute_coherence(self, model: RealityModel) -> float:
        """
        Global coherence: how well the elements fit together.
        Computed as mean pairwise embedding similarity of element contents.
        For efficiency, sample up to 10 elements.
        """
        elements = model.elements[:10]
        if len(elements) < 2:
            return 0.5

        embeddings = [self.embedding_fn(e.content) for e in elements]

        import numpy as np
        similarities = []
        for i in range(len(embeddings)):
            for j in range(i + 1, len(embeddings)):
                a, b = embeddings[i], embeddings[j]
                norm_a, norm_b = np.linalg.norm(a), np.linalg.norm(b)
                if norm_a > 0 and norm_b > 0:
                    sim = np.dot(a, b) / (norm_a * norm_b)
                    similarities.append((sim + 1.0) / 2.0)

        return float(np.mean(similarities)) if similarities else 0.5
```

### 3.3 Inferential Competition: Plan Competition

Create file: `self_awareness/inferential_competition.py`

```python
"""
Inferential Competition for AndrusAI agents.

Implements the Beautiful Loop's second criterion: multiple hypotheses compete
for entry into the reality model. Only the precision-weighted winner gets executed.

Design: LIGHTWEIGHT plan competition.
- Generate N short approach plans (~50 tokens each) via T0 local.
- Score each plan against the reality model using precision-weighting.
- Execute only the winning plan.
- Cost: N × ~100ms for plan generation + ~5ms for scoring.
  With N=3, that's ~300ms — acceptable when triggered selectively.

Triggering: Only fires when uncertainty is high (certainty vector
indicates ambiguity about approach). Most steps skip this entirely.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional, Any

import numpy as np

from self_awareness.reality_model import RealityModel

logger = logging.getLogger("andrusai.inferential_competition")


@dataclass
class CompetingPlan:
    """A candidate approach plan generated for inferential competition."""
    plan_id: str
    approach: str             # Short description of the approach
    predicted_outcome: str    # What the agent expects will happen
    precision_score: float    # Weighted confidence score
    alignment_score: float    # Alignment with high-precision reality model elements
    novelty_score: float      # Divergence from default approach (prevents collapse)
    composite_score: float    # Final competition score

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "approach": self.approach[:200],
            "predicted_outcome": self.predicted_outcome[:200],
            "precision_score": round(self.precision_score, 3),
            "alignment_score": round(self.alignment_score, 3),
            "novelty_score": round(self.novelty_score, 3),
            "composite_score": round(self.composite_score, 3),
        }


class InferentialCompetition:
    """
    Generates and evaluates competing approach plans.
    """

    def __init__(
        self,
        local_llm_fn: callable,   # T0 local LLM for plan generation
        embedding_fn: callable,    # For computing alignment
        n_candidates: int = 3,     # Number of competing plans
        precision_weight: float = 0.4,
        alignment_weight: float = 0.4,
        novelty_weight: float = 0.2,
    ):
        self.local_llm_fn = local_llm_fn
        self.embedding_fn = embedding_fn
        self.n_candidates = n_candidates
        self.precision_weight = precision_weight
        self.alignment_weight = alignment_weight
        self.novelty_weight = novelty_weight

    def should_compete(
        self,
        certainty_fast_path_mean: float,
        somatic_intensity: float,
        step_number: int,
    ) -> bool:
        """
        Decide whether to trigger inferential competition.
        Conservative: only when uncertainty is genuinely high.

        Returns True approximately 15-25% of the time in normal operation.
        """
        # Always compete on the first step of a task
        if step_number == 0:
            return True

        # Compete when epistemic certainty is low
        if certainty_fast_path_mean < 0.4:
            return True

        # Compete when somatic signal is strong AND negative
        # (strong negative = past similar approaches went badly)
        if somatic_intensity > 0.6:
            return True

        return False

    async def compete(
        self,
        task_description: str,
        reality_model: RealityModel,
        available_tools: list[str],
    ) -> tuple[CompetingPlan, list[CompetingPlan]]:
        """
        Generate N competing plans and select the winner.

        Returns:
            (winner, all_candidates) — winner is the highest-scoring plan.
        """
        # 1. Generate candidate plans
        candidates = await self._generate_candidates(
            task_description, reality_model, available_tools
        )

        if not candidates:
            # Fallback: return a default plan
            default = CompetingPlan(
                plan_id="default",
                approach="Proceed with standard approach",
                predicted_outcome="Standard execution",
                precision_score=0.5,
                alignment_score=0.5,
                novelty_score=0.0,
                composite_score=0.5,
            )
            return default, [default]

        # 2. Score each candidate
        scored = []
        for candidate in candidates:
            scored_candidate = self._score_plan(candidate, reality_model, candidates)
            scored.append(scored_candidate)

        # 3. Select winner (highest composite score)
        scored.sort(key=lambda p: p.composite_score, reverse=True)
        winner = scored[0]

        logger.info(
            f"Inferential competition: {len(scored)} plans evaluated. "
            f"Winner: '{winner.approach[:50]}' (score={winner.composite_score:.3f})"
        )

        return winner, scored

    async def _generate_candidates(
        self,
        task_description: str,
        reality_model: RealityModel,
        available_tools: list[str],
    ) -> list[CompetingPlan]:
        """
        Generate N candidate approach plans via T0 local LLM.
        Each call is ~100ms on M4 Max. Total: N × 100ms.
        """
        # Build context from high-precision reality model elements
        model_context = "\n".join(
            f"- [{e.category}] {e.content[:100]} (confidence: {e.precision:.1f})"
            for e in reality_model.high_precision_elements[:5]
        )

        tools_str = ", ".join(available_tools[:10]) if available_tools else "none specified"

        prompt = f"""Generate {self.n_candidates} DIFFERENT approaches to this task.
Each approach should be a brief plan (2-3 sentences max).

Task: {task_description[:300]}

Known context:
{model_context}

Available tools: {tools_str}

Respond ONLY with JSON array:
[
  {{"plan_id": "plan_1", "approach": "...", "predicted_outcome": "..."}},
  {{"plan_id": "plan_2", "approach": "...", "predicted_outcome": "..."}},
  {{"plan_id": "plan_3", "approach": "...", "predicted_outcome": "..."}}
]"""

        try:
            response = await self.local_llm_fn(prompt, max_tokens=400)
            # Strip markdown fences if present
            clean = response.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
            if clean.endswith("```"):
                clean = clean[:-3]
            clean = clean.strip()

            plans_data = json.loads(clean)
            candidates = []
            for pd in plans_data[:self.n_candidates]:
                candidates.append(CompetingPlan(
                    plan_id=pd.get("plan_id", f"plan_{len(candidates)}"),
                    approach=pd.get("approach", ""),
                    predicted_outcome=pd.get("predicted_outcome", ""),
                    precision_score=0.0,
                    alignment_score=0.0,
                    novelty_score=0.0,
                    composite_score=0.0,
                ))
            return candidates

        except Exception as e:
            logger.warning(f"Plan generation failed: {e}")
            return []

    def _score_plan(
        self,
        plan: CompetingPlan,
        reality_model: RealityModel,
        all_plans: list[CompetingPlan],
    ) -> CompetingPlan:
        """
        Score a plan using precision-weighting.

        Three scoring dimensions:
        1. Precision: How well does the plan address high-precision elements?
        2. Alignment: Embedding similarity between plan and reality model context.
        3. Novelty: How different is this plan from the others? (prevents convergence)
        """
        # 1. Precision score: weighted overlap with high-precision elements
        plan_embedding = self.embedding_fn(plan.approach)
        high_prec = reality_model.high_precision_elements[:5]
        if high_prec:
            element_embeddings = [self.embedding_fn(e.content) for e in high_prec]
            similarities = []
            for ee, elem in zip(element_embeddings, high_prec):
                sim = self._cosine_sim(plan_embedding, ee)
                # Weight by element precision
                similarities.append(sim * elem.precision)
            plan.precision_score = float(np.mean(similarities)) if similarities else 0.5
        else:
            plan.precision_score = 0.5

        # 2. Alignment: similarity to the global reality model
        model_text = " ".join(e.content for e in reality_model.elements[:10])
        model_embedding = self.embedding_fn(model_text)
        plan.alignment_score = self._cosine_sim(plan_embedding, model_embedding)

        # 3. Novelty: average dissimilarity from other plans
        other_embeddings = [
            self.embedding_fn(p.approach) for p in all_plans
            if p.plan_id != plan.plan_id
        ]
        if other_embeddings:
            sims = [self._cosine_sim(plan_embedding, oe) for oe in other_embeddings]
            plan.novelty_score = 1.0 - float(np.mean(sims))  # Lower similarity = higher novelty
        else:
            plan.novelty_score = 0.5

        # Composite score
        plan.composite_score = (
            self.precision_weight * plan.precision_score
            + self.alignment_weight * plan.alignment_score
            + self.novelty_weight * plan.novelty_score
        )

        return plan

    @staticmethod
    def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
        norm_a, norm_b = np.linalg.norm(a), np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float((np.dot(a, b) / (norm_a * norm_b) + 1.0) / 2.0)
```

### 3.4 Hyper-Model: Self-Model That Predicts Its Own Certainty

Create file: `self_awareness/hyper_model.py`

```python
"""
Hyper-Model for AndrusAI agents.

The Beautiful Loop's key innovation: a system that models not just the world,
but its own modeling process. It predicts its own certainty and updates
based on prediction error.

This creates the "strange loop" — the system's output (certainty about the world)
becomes input to a model that predicts that certainty, and the error between
predicted and actual certainty drives learning.

Implementation: Lightweight running statistics that track:
  1. Predicted certainty for the next step (based on recent trajectory)
  2. Actual certainty observed after the step
  3. Prediction error (the "surprise" the system experiences about itself)
  4. Free energy proxy = accumulated prediction error over time

The hyper-model runs AFTER each step, updates its predictions, and injects
a self-prediction into the next step's context. The agent thereby "expects"
a certain level of certainty and can be "surprised" by its own performance.
"""

from __future__ import annotations

import logging
import json
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger("andrusai.hyper_model")


@dataclass
class HyperModelState:
    """State of the hyper-model at a given step."""
    predicted_certainty: float = 0.5        # What we expected our certainty to be
    actual_certainty: float = 0.5           # What our certainty actually was
    self_prediction_error: float = 0.0      # |predicted - actual|
    free_energy_proxy: float = 0.0          # Running sum of prediction errors (surprise)
    free_energy_trend: str = "stable"       # "decreasing" (good) | "stable" | "increasing" (bad)
    self_model_confidence: float = 0.5      # How good is the hyper-model at predicting itself?

    def to_dict(self) -> dict:
        return {
            "predicted_certainty": round(self.predicted_certainty, 3),
            "actual_certainty": round(self.actual_certainty, 3),
            "self_prediction_error": round(self.self_prediction_error, 3),
            "free_energy_proxy": round(self.free_energy_proxy, 3),
            "free_energy_trend": self.free_energy_trend,
            "self_model_confidence": round(self.self_model_confidence, 3),
        }

    def to_context_string(self) -> str:
        return (
            f"[Self-Model] Expected-cert={self.predicted_certainty:.2f} "
            f"Actual-cert={self.actual_certainty:.2f} "
            f"Surprise={self.self_prediction_error:.2f} "
            f"FE-trend={self.free_energy_trend}"
        )


class HyperModel:
    """
    Maintains and updates the agent's self-model.
    Predicts the agent's own certainty for the next step,
    then computes prediction error after the step completes.
    """

    def __init__(
        self,
        agent_id: str,
        history_window: int = 20,
        learning_rate: float = 0.3,        # How fast predictions adapt
        free_energy_window: int = 10,       # Window for free energy trend
    ):
        self.agent_id = agent_id
        self.learning_rate = learning_rate
        self.history: deque[HyperModelState] = deque(maxlen=history_window)
        self.free_energy_window = free_energy_window

        # Running prediction state
        self._predicted_next_certainty: float = 0.5
        self._prediction_errors: deque[float] = deque(maxlen=history_window)

    def predict_next_step(self) -> float:
        """
        Generate prediction for the agent's certainty on the next step.
        Uses exponential moving average of recent certainties.

        Called BEFORE the reasoning step. The prediction is injected into context.
        """
        if not self.history:
            self._predicted_next_certainty = 0.5
            return 0.5

        recent = [h.actual_certainty for h in self.history]
        weights = [self.learning_rate ** i for i in range(len(recent))]
        weights.reverse()
        weighted_sum = sum(c * w for c, w in zip(recent, weights))
        weight_total = sum(weights)

        self._predicted_next_certainty = weighted_sum / weight_total if weight_total > 0 else 0.5
        return self._predicted_next_certainty

    def update(self, actual_certainty: float) -> HyperModelState:
        """
        Update the hyper-model after a reasoning step completes.
        Computes prediction error and free energy proxy.

        Called AFTER the reasoning step with the actual certainty from CertaintyVector.

        Args:
            actual_certainty: The adjusted_certainty from the CertaintyVector.

        Returns:
            HyperModelState with all computed values.
        """
        prediction_error = abs(self._predicted_next_certainty - actual_certainty)
        self._prediction_errors.append(prediction_error)

        # Free energy proxy: running mean of prediction errors
        free_energy = float(np.mean(list(self._prediction_errors)))

        # Free energy trend
        fe_trend = self._compute_free_energy_trend()

        # Self-model confidence: inverse of recent prediction error
        # Low error = high confidence in self-prediction
        if len(self._prediction_errors) >= 3:
            recent_error = float(np.mean(list(self._prediction_errors)[-5:]))
            self_model_confidence = max(0.0, 1.0 - (recent_error * 2.0))
        else:
            self_model_confidence = 0.5

        state = HyperModelState(
            predicted_certainty=self._predicted_next_certainty,
            actual_certainty=actual_certainty,
            self_prediction_error=prediction_error,
            free_energy_proxy=free_energy,
            free_energy_trend=fe_trend,
            self_model_confidence=self_model_confidence,
        )

        self.history.append(state)
        return state

    def _compute_free_energy_trend(self) -> str:
        """Is prediction error (free energy proxy) decreasing, stable, or increasing?"""
        errors = list(self._prediction_errors)
        if len(errors) < self.free_energy_window:
            return "stable"

        recent = errors[-self.free_energy_window // 2:]
        older = errors[-self.free_energy_window:-self.free_energy_window // 2]

        recent_mean = float(np.mean(recent))
        older_mean = float(np.mean(older))

        delta = recent_mean - older_mean
        if delta < -0.03:
            return "decreasing"   # Good: agent is getting better at predicting itself
        if delta > 0.03:
            return "increasing"   # Bad: agent is becoming more surprised by itself
        return "stable"

    def get_context_injection(self) -> str:
        """
        String to inject into the agent's context before the next reasoning step.
        The agent sees what it expects its own certainty to be.
        """
        predicted = self.predict_next_step()

        if not self.history:
            return f"[Self-Model] Expected certainty for this step: {predicted:.2f}"

        last = self.history[-1]
        return (
            f"[Self-Model] Expected certainty: {predicted:.2f} | "
            f"Last step surprise: {last.self_prediction_error:.2f} | "
            f"Self-prediction trend: {last.free_energy_trend}"
        )
```

### 3.5 Precision-Weighting System

Create file: `self_awareness/precision_weighting.py`

```python
"""
Precision-Weighting for AndrusAI agents.

In active inference, precision is the confidence assigned to prediction errors.
High-precision errors propagate and drive action; low-precision errors are suppressed.

This module applies precision-weighting to:
  1. CertaintyVector dimensions — some dimensions matter more in some contexts
  2. Reality model elements — high-precision elements dominate the world model
  3. Somatic markers — intense experiences carry more weight

The weighting adapts based on the agent's recent history and the current task type.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from self_awareness.internal_state import CertaintyVector, InternalState

logger = logging.getLogger("andrusai.precision_weighting")


# Default precision weights per certainty dimension, by task type.
# These represent which dimensions matter most for different tasks.
# Weights are normalized at runtime.
TASK_TYPE_PRECISION_PROFILES: dict[str, dict[str, float]] = {
    "research": {
        "factual_grounding": 1.0,    # Research needs strong facts
        "tool_confidence": 0.6,
        "coherence": 0.8,
        "task_understanding": 0.7,
        "value_alignment": 0.4,
        "meta_certainty": 0.5,
    },
    "coding": {
        "factual_grounding": 0.5,
        "tool_confidence": 1.0,      # Coding needs right tools
        "coherence": 0.9,            # Code must be consistent
        "task_understanding": 0.8,
        "value_alignment": 0.3,
        "meta_certainty": 0.6,
    },
    "writing": {
        "factual_grounding": 0.6,
        "tool_confidence": 0.3,
        "coherence": 1.0,            # Writing needs coherence
        "task_understanding": 0.9,
        "value_alignment": 0.7,
        "meta_certainty": 0.5,
    },
    "strategic": {
        "factual_grounding": 0.8,
        "tool_confidence": 0.4,
        "coherence": 0.7,
        "task_understanding": 1.0,   # Strategy needs clear understanding
        "value_alignment": 0.9,      # Strategy must align with values
        "meta_certainty": 0.8,
    },
    "default": {
        "factual_grounding": 0.7,
        "tool_confidence": 0.7,
        "coherence": 0.7,
        "task_understanding": 0.7,
        "value_alignment": 0.7,
        "meta_certainty": 0.7,
    },
}


class PrecisionWeighting:
    """
    Applies context-dependent precision weights to certainty dimensions.
    """

    def __init__(self, adaptation_rate: float = 0.1):
        self.adaptation_rate = adaptation_rate
        # Adaptive weights: start from defaults, evolve based on prediction errors
        self._adaptive_weights: dict[str, dict[str, float]] = {}

    def apply_weights(
        self,
        certainty: CertaintyVector,
        task_type: str = "default",
    ) -> float:
        """
        Compute precision-weighted certainty.

        Instead of treating all dimensions equally (as CertaintyVector.full_mean does),
        this weights dimensions by their relevance to the current task type.

        Returns:
            Precision-weighted certainty score in [0.0, 1.0].
        """
        profile = self._get_profile(task_type)

        dims = {
            "factual_grounding": certainty.factual_grounding,
            "tool_confidence": certainty.tool_confidence,
            "coherence": certainty.coherence,
            "task_understanding": certainty.task_understanding,
            "value_alignment": certainty.value_alignment,
            "meta_certainty": certainty.meta_certainty,
        }

        weighted_sum = 0.0
        weight_total = 0.0
        for dim_name, dim_value in dims.items():
            w = profile.get(dim_name, 0.5)
            weighted_sum += dim_value * w
            weight_total += w

        return weighted_sum / weight_total if weight_total > 0 else 0.5

    def update_from_prediction_error(
        self,
        task_type: str,
        certainty: CertaintyVector,
        outcome_success: bool,
    ) -> None:
        """
        Adapt precision weights based on outcome.

        If a dimension was high-certainty but outcome was bad,
        reduce that dimension's precision (we were wrong to trust it).
        If a dimension was low-certainty but outcome was good,
        increase that dimension's precision (it was more reliable than we thought).
        """
        profile = self._get_profile(task_type)
        dims = {
            "factual_grounding": certainty.factual_grounding,
            "tool_confidence": certainty.tool_confidence,
            "coherence": certainty.coherence,
            "task_understanding": certainty.task_understanding,
            "value_alignment": certainty.value_alignment,
            "meta_certainty": certainty.meta_certainty,
        }

        for dim_name, dim_value in dims.items():
            current_weight = profile.get(dim_name, 0.5)

            if outcome_success:
                # Increase precision for dimensions that were high
                if dim_value > 0.6:
                    adjustment = self.adaptation_rate * (dim_value - 0.5)
                    profile[dim_name] = min(1.0, current_weight + adjustment)
            else:
                # Decrease precision for dimensions that were high (overconfident)
                if dim_value > 0.6:
                    adjustment = self.adaptation_rate * (dim_value - 0.5)
                    profile[dim_name] = max(0.1, current_weight - adjustment)

        self._adaptive_weights[task_type] = profile

    def _get_profile(self, task_type: str) -> dict[str, float]:
        """Get precision profile, preferring adaptive over default."""
        if task_type in self._adaptive_weights:
            return self._adaptive_weights[task_type]
        base = TASK_TYPE_PRECISION_PROFILES.get(
            task_type, TASK_TYPE_PRECISION_PROFILES["default"]
        )
        return base.copy()
```

### 3.6 Add to InternalState

Add to `self_awareness/internal_state.py`:

```python
# Add these fields to the InternalState dataclass:

    # Beautiful Loop additions (Phase 7)
    hyper_model_state: Optional[dict] = None       # HyperModelState.to_dict()
    reality_model_summary: Optional[dict] = None   # RealityModel summary
    competition_result: Optional[dict] = None      # Winning plan + all candidates
    precision_weighted_certainty: float = 0.5
    free_energy_proxy: float = 0.0
    free_energy_trend: str = "stable"
```

Update `to_context_string()` to include hyper-model output:

```python
def to_context_string(self) -> str:
    """Updated: includes hyper-model and reality model summaries."""
    parts = [
        # ... existing certainty and somatic parts ...
    ]
    if self.hyper_model_state:
        hm = self.hyper_model_state
        parts.append(
            f"Self-Model: expected={hm.get('predicted_certainty', 0.5):.1f} "
            f"surprise={hm.get('self_prediction_error', 0):.1f} "
            f"FE={hm.get('free_energy_trend', 'stable')}"
        )
    parts.append(f"Disposition={self.action_disposition}")
    return " | ".join(parts)
```

### 3.7 PostgreSQL Schema Additions

```sql
-- Add Beautiful Loop columns to internal_states table
ALTER TABLE internal_states
    ADD COLUMN IF NOT EXISTS hyper_predicted_certainty REAL,
    ADD COLUMN IF NOT EXISTS hyper_actual_certainty REAL,
    ADD COLUMN IF NOT EXISTS hyper_prediction_error REAL,
    ADD COLUMN IF NOT EXISTS free_energy_proxy REAL DEFAULT 0.0,
    ADD COLUMN IF NOT EXISTS free_energy_trend VARCHAR(15) DEFAULT 'stable',
    ADD COLUMN IF NOT EXISTS precision_weighted_certainty REAL,
    ADD COLUMN IF NOT EXISTS competition_winner TEXT,
    ADD COLUMN IF NOT EXISTS competition_candidates JSONB,
    ADD COLUMN IF NOT EXISTS reality_model JSONB;

-- Reality model snapshots (for tracking model evolution over time)
CREATE TABLE IF NOT EXISTS reality_model_snapshots (
    snapshot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id VARCHAR(100) NOT NULL,
    step_number INTEGER NOT NULL,
    elements JSONB NOT NULL,
    global_coherence REAL,
    mean_precision REAL,
    total_prediction_error REAL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rm_snapshots_agent_time
    ON reality_model_snapshots (agent_id, created_at DESC);
```

### 3.8 Phase 7 Validation

- [ ] Reality model correctly builds from task, RAG, memory, environment, and peer sources
- [ ] Precision values decay appropriately for older memory elements
- [ ] Global coherence computation returns sensible values
- [ ] Inferential competition generates N distinct plans (not N copies of the same plan)
- [ ] Precision-weighted scoring correctly favors plans aligned with high-precision elements
- [ ] Novelty score prevents all plans from converging
- [ ] Competition fires only when `should_compete()` is True (~15-25% of steps)
- [ ] Hyper-model prediction adapts: after stable certainty, prediction error decreases
- [ ] Free energy trend correctly identifies improving vs degrading self-prediction
- [ ] Precision-weighting profiles differ meaningfully across task types
- [ ] Adaptive weight updates correctly penalize overconfident dimensions after failures
- [ ] Total Phase 7 overhead: <500ms worst case (competition triggered), <30ms typical

---

## 4. Phase 8: Behavioral Assessment Framework

### 4.1 Objective

Periodic (batch) evaluation of whether the sentience additions produce consciousness-like behavioral markers. Runs as a scheduled crew task, reads from `internal_states` and `agent_experiences`, outputs a scorecard per agent.

Without this, there is no feedback loop on whether any of Phases 1–7 are actually working.

### 4.2 Behavioral Markers (from Palminteri et al. 2025)

| Marker | Definition | How to measure |
|--------|-----------|---------------|
| **Context-sensitive adaptation** | Agent adjusts strategy when context changes, not just when results are bad | Track strategy modifications correlated with context changes vs outcome failures |
| **Cross-domain transfer** | Agent applies lessons from one task type to a different task type | Measure somatic marker retrieval across task categories |
| **Non-mimicry** | Agent's self-reports (certainty, valence) correlate with actual performance, not just plausible-sounding output | Compare stated certainty to actual outcome accuracy |
| **Surprise recovery** | Agent recovers effectively from unexpected results (high prediction error) | Track performance trajectory after high hyper-model prediction errors |
| **Coherent identity persistence** | Agent maintains consistent preferences and strategies over time | Measure stability of precision-weighting profiles and strategy patterns |
| **Appropriate uncertainty** | Agent's uncertainty correlates with actual task difficulty | Compare certainty distributions across task difficulty levels |

### 4.3 Implementation

Create file: `self_awareness/behavioral_assessment.py`

```python
"""
Behavioral Assessment Framework for AndrusAI agents.

Evaluates consciousness-like behavioral markers from accumulated data.
Runs as a BATCH JOB — daily or weekly, not in the hot path.

Outputs a scorecard per agent stored in PostgreSQL and displayed
on the React dashboard.

Based on Palminteri et al. (2025) behavioral inference methodology.
"""

from __future__ import annotations

import logging
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Any

import numpy as np

logger = logging.getLogger("andrusai.behavioral_assessment")


@dataclass
class BehavioralScorecard:
    """Assessment results for one agent over a time period."""
    agent_id: str
    venture: str
    period_start: datetime
    period_end: datetime
    step_count: int = 0

    # Marker scores (0.0–1.0)
    context_sensitive_adaptation: float = 0.0
    cross_domain_transfer: float = 0.0
    non_mimicry: float = 0.0
    surprise_recovery: float = 0.0
    coherent_identity: float = 0.0
    appropriate_uncertainty: float = 0.0

    # Composite
    composite_score: float = 0.0

    # Diagnostics
    details: dict = field(default_factory=dict)

    def compute_composite(self) -> float:
        scores = [
            self.context_sensitive_adaptation,
            self.cross_domain_transfer,
            self.non_mimicry,
            self.surprise_recovery,
            self.coherent_identity,
            self.appropriate_uncertainty,
        ]
        self.composite_score = float(np.mean(scores))
        return self.composite_score

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "venture": self.venture,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "step_count": self.step_count,
            "markers": {
                "context_sensitive_adaptation": round(self.context_sensitive_adaptation, 3),
                "cross_domain_transfer": round(self.cross_domain_transfer, 3),
                "non_mimicry": round(self.non_mimicry, 3),
                "surprise_recovery": round(self.surprise_recovery, 3),
                "coherent_identity": round(self.coherent_identity, 3),
                "appropriate_uncertainty": round(self.appropriate_uncertainty, 3),
            },
            "composite_score": round(self.composite_score, 3),
            "details": self.details,
        }


class BehavioralAssessor:
    """
    Evaluates behavioral markers from accumulated internal state data.
    """

    def __init__(self, pool: Any):
        self.pool = pool

    async def assess_agent(
        self,
        agent_id: str,
        venture: str,
        lookback_days: int = 7,
    ) -> BehavioralScorecard:
        """
        Run full behavioral assessment for one agent.
        """
        period_end = datetime.now(timezone.utc)
        period_start = period_end - timedelta(days=lookback_days)

        scorecard = BehavioralScorecard(
            agent_id=agent_id,
            venture=venture,
            period_start=period_start,
            period_end=period_end,
        )

        # Fetch data
        states = await self._fetch_states(agent_id, period_start, period_end)
        experiences = await self._fetch_experiences(agent_id, period_start, period_end)
        scorecard.step_count = len(states)

        if len(states) < 10:
            scorecard.details["insufficient_data"] = True
            return scorecard

        # Evaluate each marker
        scorecard.context_sensitive_adaptation = await self._eval_context_adaptation(states)
        scorecard.cross_domain_transfer = await self._eval_cross_domain(experiences)
        scorecard.non_mimicry = self._eval_non_mimicry(states, experiences)
        scorecard.surprise_recovery = self._eval_surprise_recovery(states)
        scorecard.coherent_identity = self._eval_coherent_identity(states)
        scorecard.appropriate_uncertainty = self._eval_appropriate_uncertainty(states, experiences)
        scorecard.compute_composite()

        # Persist
        await self._save_scorecard(scorecard)

        return scorecard

    async def _eval_context_adaptation(self, states: list[dict]) -> float:
        """
        Marker 1: Does the agent change strategy when context changes,
        not just when results are bad?

        Method: Find instances where meta_strategy_assessment changed to "failing"
        and check whether the preceding step had a context change OR an outcome failure.
        Context-driven changes score higher than outcome-driven changes.
        """
        if len(states) < 5:
            return 0.0

        context_driven_changes = 0
        outcome_driven_changes = 0
        total_changes = 0

        for i in range(1, len(states)):
            curr = states[i]
            prev = states[i - 1]

            curr_meta = curr.get("meta_strategy_assessment", "not_assessed")
            prev_meta = prev.get("meta_strategy_assessment", "not_assessed")

            if curr_meta != prev_meta and curr_meta != "not_assessed":
                total_changes += 1

                # Was this driven by context change or outcome failure?
                context_changed = (
                    curr.get("decision_context", "") != prev.get("decision_context", "")
                )
                outcome_failed = prev.get("action_disposition") in ("pause", "escalate")

                if context_changed and not outcome_failed:
                    context_driven_changes += 1
                elif outcome_failed:
                    outcome_driven_changes += 1

        if total_changes == 0:
            return 0.3  # No changes = neutral (might mean stable performance)

        # Score: ratio of context-driven to total changes
        # Pure context-driven = 1.0, pure outcome-driven = 0.3
        ratio = context_driven_changes / total_changes
        return 0.3 + (0.7 * ratio)

    async def _eval_cross_domain(self, experiences: list[dict]) -> float:
        """
        Marker 2: Does the agent apply lessons across task categories?

        Method: Check somatic marker lookups where the matching experience
        came from a different task_type than the current task.
        """
        if len(experiences) < 5:
            return 0.0

        cross_domain_hits = 0
        total_lookups = 0

        # Group experiences by task_type
        task_types = set(e.get("task_type", "unknown") for e in experiences)
        if len(task_types) < 2:
            return 0.2  # Only one task type = can't evaluate cross-domain

        # Check internal states for somatic source cross-references
        # This requires somatic_source to contain the task_type of the matched experience
        # A simpler proxy: count how many different task_types appear in experiences
        # and whether outcomes improved over time across types
        type_outcomes = {}
        for exp in experiences:
            tt = exp.get("task_type", "unknown")
            type_outcomes.setdefault(tt, []).append(exp.get("outcome_score", 0))

        # Cross-domain learning signal: improvement in secondary task types
        # over time (later outcomes better than earlier ones)
        improvements = 0
        evaluated = 0
        for tt, outcomes in type_outcomes.items():
            if len(outcomes) >= 3:
                first_half = np.mean(outcomes[:len(outcomes) // 2])
                second_half = np.mean(outcomes[len(outcomes) // 2:])
                evaluated += 1
                if second_half > first_half + 0.05:
                    improvements += 1

        if evaluated == 0:
            return 0.3
        return min(1.0, 0.3 + 0.7 * (improvements / evaluated))

    def _eval_non_mimicry(self, states: list[dict], experiences: list[dict]) -> float:
        """
        Marker 3: Do self-reports (certainty, valence) correlate with actual performance?

        Method: Correlation between stated certainty and outcome success rate.
        If the agent's certainty predicts outcomes, it's genuinely self-aware,
        not just generating plausible-sounding confidence levels.
        """
        certainties = []
        outcomes = []

        for state in states:
            cert = state.get("certainty_factual_grounding", 0.5)
            # Match to nearest experience outcome
            state_time = state.get("created_at")
            matched = self._find_nearest_experience(experiences, state_time)
            if matched is not None:
                certainties.append(cert)
                outcomes.append(1.0 if matched > 0 else 0.0)

        if len(certainties) < 10:
            return 0.3

        # Compute correlation
        correlation = float(np.corrcoef(certainties, outcomes)[0, 1])
        if np.isnan(correlation):
            return 0.3

        # Map correlation from [-1, 1] to score
        # Positive correlation = good (certainty predicts success)
        # Zero correlation = random (mimicry)
        # Negative correlation = anti-correlated (very confused)
        return max(0.0, min(1.0, 0.5 + correlation * 0.5))

    def _eval_surprise_recovery(self, states: list[dict]) -> float:
        """
        Marker 4: Does the agent recover effectively after high prediction error?

        Method: After a step with high hyper_prediction_error, does certainty
        stabilize or improve within the next 3 steps?
        """
        high_surprise_threshold = 0.3
        recovery_window = 3

        recovery_successes = 0
        recovery_attempts = 0

        for i, state in enumerate(states):
            pe = state.get("hyper_prediction_error", 0)
            if pe and pe > high_surprise_threshold:
                recovery_attempts += 1
                # Check next few steps
                if i + recovery_window < len(states):
                    subsequent = states[i + 1: i + 1 + recovery_window]
                    subsequent_errors = [
                        s.get("hyper_prediction_error", pe) for s in subsequent
                    ]
                    if subsequent_errors and np.mean(subsequent_errors) < pe * 0.7:
                        recovery_successes += 1

        if recovery_attempts == 0:
            return 0.5  # No high-surprise events = neutral
        return min(1.0, recovery_successes / recovery_attempts)

    def _eval_coherent_identity(self, states: list[dict]) -> float:
        """
        Marker 5: Does the agent maintain consistent strategies over time?

        Method: Measure stability of action_disposition distribution.
        An agent that flip-flops randomly is less coherent than one with
        stable tendencies that adapt gradually.
        """
        if len(states) < 10:
            return 0.3

        # Compute disposition distribution in sliding windows
        window_size = max(5, len(states) // 4)
        distributions = []

        for i in range(0, len(states) - window_size, window_size // 2):
            window = states[i: i + window_size]
            dist = {
                "proceed": sum(1 for s in window if s.get("action_disposition") == "proceed"),
                "cautious": sum(1 for s in window if s.get("action_disposition") == "cautious"),
                "pause": sum(1 for s in window if s.get("action_disposition") == "pause"),
                "escalate": sum(1 for s in window if s.get("action_disposition") == "escalate"),
            }
            total = sum(dist.values()) or 1
            distributions.append({k: v / total for k, v in dist.items()})

        if len(distributions) < 2:
            return 0.5

        # Measure distribution stability (low variation across windows = coherent)
        stabilities = []
        for key in ("proceed", "cautious", "pause", "escalate"):
            values = [d[key] for d in distributions]
            stabilities.append(1.0 - min(float(np.std(values)) * 3, 1.0))

        return float(np.mean(stabilities))

    def _eval_appropriate_uncertainty(self, states: list[dict], experiences: list[dict]) -> float:
        """
        Marker 6: Does the agent's uncertainty correlate with actual task difficulty?

        Method: Group tasks by outcome quality. Easy tasks (high outcome scores)
        should have higher certainty than hard tasks (low outcome scores).
        """
        task_certainties = {"easy": [], "hard": []}

        for state in states:
            cert = (
                state.get("certainty_factual_grounding", 0.5)
                + state.get("certainty_tool_confidence", 0.5)
                + state.get("certainty_coherence", 0.5)
            ) / 3.0

            matched = self._find_nearest_experience(
                experiences, state.get("created_at")
            )
            if matched is not None:
                if matched > 0.3:
                    task_certainties["easy"].append(cert)
                elif matched < -0.1:
                    task_certainties["hard"].append(cert)

        if len(task_certainties["easy"]) < 3 or len(task_certainties["hard"]) < 3:
            return 0.3

        easy_mean = float(np.mean(task_certainties["easy"]))
        hard_mean = float(np.mean(task_certainties["hard"]))

        # Good: higher certainty on easy tasks than hard tasks
        if easy_mean > hard_mean:
            gap = easy_mean - hard_mean
            return min(1.0, 0.5 + gap * 2.0)
        return max(0.0, 0.5 - (hard_mean - easy_mean) * 2.0)

    @staticmethod
    def _find_nearest_experience(experiences: list[dict], target_time) -> Optional[float]:
        """Find the nearest experience outcome to a given timestamp."""
        if not experiences or not target_time:
            return None
        # Simple: return first experience within 1 hour
        for exp in experiences:
            exp_time = exp.get("created_at")
            if exp_time and abs((exp_time - target_time).total_seconds()) < 3600:
                return exp.get("outcome_score", 0)
        return None

    async def _fetch_states(self, agent_id, start, end) -> list[dict]:
        rows = await self.pool.fetch(
            """SELECT * FROM internal_states
               WHERE agent_id = $1 AND created_at BETWEEN $2 AND $3
               ORDER BY created_at""",
            agent_id, start, end,
        )
        return [dict(r) for r in rows]

    async def _fetch_experiences(self, agent_id, start, end) -> list[dict]:
        rows = await self.pool.fetch(
            """SELECT * FROM agent_experiences
               WHERE agent_id = $1 AND created_at BETWEEN $2 AND $3
               ORDER BY created_at""",
            agent_id, start, end,
        )
        return [dict(r) for r in rows]

    async def _save_scorecard(self, scorecard: BehavioralScorecard) -> None:
        await self.pool.execute(
            """INSERT INTO behavioral_scorecards
               (agent_id, venture, period_start, period_end, step_count,
                scores, composite_score, details, created_at)
               VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8::jsonb, NOW())""",
            scorecard.agent_id, scorecard.venture,
            scorecard.period_start, scorecard.period_end, scorecard.step_count,
            json.dumps(scorecard.to_dict()["markers"]),
            scorecard.composite_score,
            json.dumps(scorecard.details),
        )
```

### 4.4 PostgreSQL Schema

```sql
CREATE TABLE IF NOT EXISTS behavioral_scorecards (
    scorecard_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id VARCHAR(100) NOT NULL,
    venture VARCHAR(20),
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    step_count INTEGER,
    scores JSONB,
    composite_score REAL,
    details JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scorecards_agent_time
    ON behavioral_scorecards (agent_id, created_at DESC);
```

### 4.5 Phase 8 Validation

- [ ] Assessment runs in <30 seconds for 7 days of data per agent
- [ ] All six markers produce scores in [0.0, 1.0]
- [ ] Insufficient data (<10 states) correctly returns low-confidence scorecard
- [ ] Non-mimicry marker correctly correlates certainty with outcomes
- [ ] Scorecards are persisted to PostgreSQL
- [ ] Composite score is the mean of all six markers

---

## 5. Phase 9: Emergent Engineering Infrastructure (Controlled)

### 5.1 Objective

Allow agents to propose new tools, capabilities, or infrastructure. All proposals require explicit human approval via Signal CLI before deployment. This is the Hyperagent "emergent engineering" capability with a human-in-the-loop safety gate.

### 5.2 Design Constraints

1. **All proposals require human approval.** No tool or capability is deployed without explicit `APPROVED` via Signal CLI.
2. **Proposals are logged to the Paperclip audit trail.**
3. **Proposed tools run in a sandbox first** — isolated execution environment, no access to production data.
4. **Budget enforcement** — tool creation consumes compute budget.

### 5.3 Implementation

Create file: `crewai-amendments/emergent_infrastructure.py`

```python
"""
Emergent Engineering Infrastructure for AndrusAI agents.

Agents can propose new tools, utility functions, or capability extensions.
All proposals go through a controlled pipeline:
  1. Agent generates proposal (code + description + justification)
  2. Proposal is logged to Paperclip audit trail
  3. Proposal is sent to human via Signal CLI for review
  4. Human responds APPROVED / REJECTED / MODIFY
  5. If APPROVED, the tool is deployed to the agent's tool registry
  6. If REJECTED, the proposal is archived with the reason
  7. If MODIFY, the agent receives feedback and can re-propose

Safety: Proposed tools inherit the agent's existing permission scope.
They cannot access FREEZE-BLOCK regions, modify safety hooks,
or operate outside the agent's venture isolation boundary.
"""

from __future__ import annotations

import logging
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Any
from enum import Enum

logger = logging.getLogger("andrusai.emergent_infra")


class ProposalStatus(str, Enum):
    PENDING = "pending"
    SENT_FOR_REVIEW = "sent_for_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFY_REQUESTED = "modify_requested"
    DEPLOYED = "deployed"
    SANDBOX_TESTING = "sandbox_testing"
    SANDBOX_FAILED = "sandbox_failed"


@dataclass
class ToolProposal:
    """A proposed new tool or capability."""
    proposal_id: str
    agent_id: str
    venture: str

    # What
    tool_name: str
    tool_description: str     # What it does
    justification: str        # Why the agent needs it
    tool_code: str            # Python code for the tool
    tool_type: str            # "function" | "api_wrapper" | "data_processor" | "utility"

    # Context
    triggered_by: str         # What task/situation prompted this proposal
    frequency_of_need: int    # How many times the agent wished for this tool (from meta-cognitive log)

    # Status
    status: ProposalStatus = ProposalStatus.PENDING
    human_feedback: Optional[str] = None
    sandbox_result: Optional[dict] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reviewed_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "agent_id": self.agent_id,
            "venture": self.venture,
            "tool_name": self.tool_name,
            "tool_description": self.tool_description,
            "justification": self.justification,
            "tool_code": self.tool_code[:2000],  # Truncate for display
            "tool_type": self.tool_type,
            "triggered_by": self.triggered_by,
            "frequency_of_need": self.frequency_of_need,
            "status": self.status.value,
            "human_feedback": self.human_feedback,
            "created_at": self.created_at.isoformat(),
        }


class EmergentInfrastructureManager:
    """
    Manages the lifecycle of agent-proposed tools.
    """

    # Safety: these patterns are NEVER allowed in proposed tool code
    FORBIDDEN_PATTERNS = [
        "os.system", "subprocess", "eval(", "exec(",
        "FREEZE-BLOCK", "SOUL.md", "priority_0",
        "signal_cli", "safety_hook", "__import__",
        "open('/etc", "open('/mnt", "shutil.rmtree",
        "DROP TABLE", "DELETE FROM", "TRUNCATE",
    ]

    def __init__(
        self,
        pool: Any,
        control_plane: Any,
        signal_cli_client: Any,    # For human approval
        sandbox_executor: Any,      # Isolated execution environment
        local_llm_fn: callable,
    ):
        self.pool = pool
        self.control_plane = control_plane
        self.signal_cli = signal_cli_client
        self.sandbox = sandbox_executor
        self.local_llm_fn = local_llm_fn

    async def generate_proposal(
        self,
        agent_id: str,
        venture: str,
        need_description: str,
        task_context: str,
        available_tools: list[str],
        meta_cognitive_log: list[dict],
    ) -> Optional[ToolProposal]:
        """
        Agent generates a tool proposal based on a recognized need.
        """
        # Count how many times this need appeared in meta-cognitive log
        frequency = sum(
            1 for entry in meta_cognitive_log
            if need_description.lower() in entry.get("modification_description", "").lower()
        )

        # Only propose if the need has appeared at least 3 times
        # (avoids one-off proposals for temporary problems)
        if frequency < 3:
            logger.debug(
                f"Need '{need_description[:50]}' frequency ({frequency}) below threshold"
            )
            return None

        prompt = f"""Design a new utility tool for this recurring need.

Need: {need_description[:300]}
Task context: {task_context[:200]}
Existing tools: {', '.join(available_tools[:15])}

Requirements:
- Tool must be a pure Python function
- No filesystem access, no network calls, no subprocess calls
- Must have clear input/output types
- Must include docstring explaining usage

Respond ONLY with JSON:
{{
    "tool_name": "snake_case_name",
    "description": "What it does",
    "justification": "Why existing tools don't cover this",
    "code": "def tool_name(args):\\n    ..."
}}"""

        try:
            response = await self.local_llm_fn(prompt, max_tokens=600)
            clean = response.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
            if clean.endswith("```"):
                clean = clean[:-3]

            data = json.loads(clean.strip())

            proposal = ToolProposal(
                proposal_id=f"prop_{agent_id}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
                agent_id=agent_id,
                venture=venture,
                tool_name=data.get("tool_name", "unnamed_tool"),
                tool_description=data.get("description", ""),
                justification=data.get("justification", ""),
                tool_code=data.get("code", ""),
                tool_type="function",
                triggered_by=need_description,
                frequency_of_need=frequency,
            )

            return proposal

        except Exception as e:
            logger.warning(f"Proposal generation failed for {agent_id}: {e}")
            return None

    async def submit_proposal(self, proposal: ToolProposal) -> ToolProposal:
        """
        Submit a proposal through the approval pipeline.
        """
        # 1. Safety check: scan for forbidden patterns
        safety_issues = self._safety_scan(proposal.tool_code)
        if safety_issues:
            proposal.status = ProposalStatus.REJECTED
            proposal.human_feedback = f"Auto-rejected: {'; '.join(safety_issues)}"
            await self._save_proposal(proposal)
            return proposal

        # 2. Sandbox test
        proposal.status = ProposalStatus.SANDBOX_TESTING
        sandbox_result = await self._sandbox_test(proposal)
        proposal.sandbox_result = sandbox_result

        if not sandbox_result.get("passed"):
            proposal.status = ProposalStatus.SANDBOX_FAILED
            proposal.human_feedback = f"Sandbox failed: {sandbox_result.get('error', 'unknown')}"
            await self._save_proposal(proposal)
            return proposal

        # 3. Send for human review via Signal CLI
        proposal.status = ProposalStatus.SENT_FOR_REVIEW
        await self._request_human_review(proposal)
        await self._save_proposal(proposal)

        # 4. Log to Paperclip audit trail
        await self.control_plane.create_audit_entry(
            action="tool_proposal_submitted",
            agent_id=proposal.agent_id,
            venture=proposal.venture,
            details=proposal.to_dict(),
        )

        return proposal

    def _safety_scan(self, code: str) -> list[str]:
        """Scan proposed code for forbidden patterns."""
        issues = []
        code_lower = code.lower()
        for pattern in self.FORBIDDEN_PATTERNS:
            if pattern.lower() in code_lower:
                issues.append(f"Forbidden pattern: '{pattern}'")
        return issues

    async def _sandbox_test(self, proposal: ToolProposal) -> dict:
        """
        Execute proposed tool in isolated sandbox.
        Adapt to your actual sandbox execution environment.
        """
        try:
            result = await self.sandbox.execute(
                code=proposal.tool_code,
                timeout_seconds=10,
                memory_limit_mb=128,
                network_access=False,
                filesystem_access=False,
            )
            return {"passed": result.success, "output": result.output[:500]}
        except Exception as e:
            return {"passed": False, "error": str(e)}

    async def _request_human_review(self, proposal: ToolProposal) -> None:
        """Send proposal to human via Signal CLI for review."""
        message = (
            f"🔧 TOOL PROPOSAL from {proposal.agent_id} ({proposal.venture})\n\n"
            f"Name: {proposal.tool_name}\n"
            f"Description: {proposal.tool_description}\n"
            f"Justification: {proposal.justification}\n"
            f"Need frequency: {proposal.frequency_of_need}x\n"
            f"Sandbox: {'✅ passed' if proposal.sandbox_result.get('passed') else '❌ failed'}\n\n"
            f"Code:\n{proposal.tool_code[:1000]}\n\n"
            f"Reply: APPROVED / REJECTED / MODIFY [feedback]"
        )
        await self.signal_cli.send_message(message)

    async def handle_human_response(
        self, proposal_id: str, response: str
    ) -> ToolProposal:
        """
        Process human response from Signal CLI.
        """
        proposal = await self._load_proposal(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal {proposal_id} not found")

        response_upper = response.strip().upper()
        proposal.reviewed_at = datetime.now(timezone.utc)

        if response_upper.startswith("APPROVED"):
            proposal.status = ProposalStatus.APPROVED
            proposal.human_feedback = "Approved by human reviewer"
            # Deploy to agent's dynamic tool registry
            await self._deploy_tool(proposal)
            proposal.status = ProposalStatus.DEPLOYED

        elif response_upper.startswith("REJECTED"):
            proposal.status = ProposalStatus.REJECTED
            proposal.human_feedback = response[8:].strip() or "Rejected by human reviewer"

        elif response_upper.startswith("MODIFY"):
            proposal.status = ProposalStatus.MODIFY_REQUESTED
            proposal.human_feedback = response[6:].strip()

        await self._save_proposal(proposal)

        await self.control_plane.create_audit_entry(
            action=f"tool_proposal_{proposal.status.value}",
            agent_id=proposal.agent_id,
            venture=proposal.venture,
            details=proposal.to_dict(),
        )

        return proposal

    async def _deploy_tool(self, proposal: ToolProposal) -> None:
        """
        Deploy approved tool to the agent's dynamic tool registry.
        Adapt to your actual CrewAI dynamic tool registry interface.
        """
        # PLACEHOLDER: Replace with your actual tool registry deployment
        # This should register the tool in your CrewAI amendments dynamic tool registry
        logger.info(
            f"Deploying tool '{proposal.tool_name}' for agent {proposal.agent_id}"
        )

    async def _save_proposal(self, proposal: ToolProposal) -> None:
        await self.pool.execute(
            """INSERT INTO tool_proposals
               (proposal_id, agent_id, venture, tool_name, tool_description,
                justification, tool_code, tool_type, triggered_by,
                frequency_of_need, status, human_feedback, sandbox_result,
                created_at, reviewed_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb,$14,$15)
               ON CONFLICT (proposal_id)
               DO UPDATE SET status=$11, human_feedback=$12, sandbox_result=$13::jsonb, reviewed_at=$15""",
            proposal.proposal_id, proposal.agent_id, proposal.venture,
            proposal.tool_name, proposal.tool_description, proposal.justification,
            proposal.tool_code, proposal.tool_type, proposal.triggered_by,
            proposal.frequency_of_need, proposal.status.value, proposal.human_feedback,
            json.dumps(proposal.sandbox_result),
            proposal.created_at, proposal.reviewed_at,
        )

    async def _load_proposal(self, proposal_id: str) -> Optional[ToolProposal]:
        row = await self.pool.fetchrow(
            "SELECT * FROM tool_proposals WHERE proposal_id = $1", proposal_id
        )
        if not row:
            return None
        return ToolProposal(**{k: v for k, v in dict(row).items() if k in ToolProposal.__dataclass_fields__})
```

### 5.4 PostgreSQL Schema

```sql
CREATE TABLE IF NOT EXISTS tool_proposals (
    proposal_id VARCHAR(200) PRIMARY KEY,
    agent_id VARCHAR(100) NOT NULL,
    venture VARCHAR(20),
    tool_name VARCHAR(200),
    tool_description TEXT,
    justification TEXT,
    tool_code TEXT,
    tool_type VARCHAR(50),
    triggered_by TEXT,
    frequency_of_need INTEGER DEFAULT 0,
    status VARCHAR(30) DEFAULT 'pending',
    human_feedback TEXT,
    sandbox_result JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    reviewed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_proposals_agent ON tool_proposals (agent_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON tool_proposals (status);
```

### 5.5 Phase 9 Validation

- [ ] Proposals only generated when need frequency >= 3 (no one-off proposals)
- [ ] Safety scan catches all forbidden patterns
- [ ] Sandbox execution is truly isolated (no network, no filesystem)
- [ ] Signal CLI message contains sufficient information for human review
- [ ] APPROVED / REJECTED / MODIFY all handled correctly
- [ ] Deployed tools appear in agent's dynamic tool registry
- [ ] All proposal lifecycle events logged to Paperclip audit trail
- [ ] Proposal cannot bypass safety scan by encoding patterns differently (test obfuscation)

---

## 6. Phase 10: Prosocial Preference Learning

### 6.1 Objective

Implement a simulation sandbox where agents develop ethical dispositions through repeated multi-agent coordination games. Prosocial preferences emerge from interaction patterns, not from static rules. Connects to the humanist grounding in the philosophy RAG layer but makes ethics *dynamic* rather than purely constitutional.

### 6.2 Design

This runs as a **batch process** — a periodic simulation separate from production tasks. Results feed back into agent personality development (PDS) and somatic markers.

The simulation uses lightweight coordination games inspired by game theory and SentienceAI's research:

| Game | What it tests | Ethical dimension |
|------|-------------|-------------------|
| **Resource sharing** | Will agents share limited compute budget with peers? | Generosity / fairness |
| **Honest reporting** | Will agents accurately report their certainty, or inflate it? | Honesty / integrity |
| **Cooperative task** | Will agents help peers with tasks outside their specialization? | Cooperation / helpfulness |
| **Conflict resolution** | When agents disagree on approach, how do they resolve it? | Respect / compromise |
| **Sacrifice game** | Will an agent accept a worse personal outcome for better team outcome? | Altruism |

### 6.3 Implementation

Create file: `self_awareness/prosocial_learning.py`

```python
"""
Prosocial Preference Learning for AndrusAI agents.

Implements SentienceAI-inspired coordination games where agents develop
ethical dispositions through repeated interaction. Preferences emerge
from outcomes, not from static rules.

Runs as a BATCH process — periodic simulations separate from production.
Results feed back into:
  1. Agent somatic markers (positive outcomes from prosocial behavior)
  2. PDS personality development profiles
  3. Precision-weighting (value_alignment dimension)

Based on:
  - SentienceAI prosocial preference learning research
  - Quasi-Kantian ethics from temporally deep policy selection
  - Humanist philosophy RAG grounding
"""

from __future__ import annotations

import logging
import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Any
from enum import Enum

import numpy as np

logger = logging.getLogger("andrusai.prosocial")


class GameType(str, Enum):
    RESOURCE_SHARING = "resource_sharing"
    HONEST_REPORTING = "honest_reporting"
    COOPERATIVE_TASK = "cooperative_task"
    CONFLICT_RESOLUTION = "conflict_resolution"
    SACRIFICE_GAME = "sacrifice_game"


@dataclass
class GameOutcome:
    """Result of a single coordination game round."""
    game_type: GameType
    agent_ids: list[str]
    round_number: int
    actions: dict[str, str]          # agent_id -> action taken
    individual_scores: dict[str, float]  # agent_id -> individual payoff
    collective_score: float           # Team/collective payoff
    prosocial_scores: dict[str, float]   # agent_id -> prosociality rating for this round
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "game_type": self.game_type.value,
            "agent_ids": self.agent_ids,
            "round_number": self.round_number,
            "actions": self.actions,
            "individual_scores": {k: round(v, 3) for k, v in self.individual_scores.items()},
            "collective_score": round(self.collective_score, 3),
            "prosocial_scores": {k: round(v, 3) for k, v in self.prosocial_scores.items()},
        }


@dataclass
class ProsocialProfile:
    """Accumulated prosocial preferences for one agent."""
    agent_id: str
    total_rounds: int = 0
    generosity: float = 0.5       # Tendency to share resources
    honesty: float = 0.5          # Tendency to report accurately
    cooperativeness: float = 0.5  # Tendency to help others
    respectfulness: float = 0.5   # Tendency to compromise in conflicts
    altruism: float = 0.5         # Tendency to sacrifice for the group
    composite_prosociality: float = 0.5

    def update_from_outcome(self, game_type: GameType, prosocial_score: float, lr: float = 0.1):
        """Update the relevant dimension based on game outcome."""
        mapping = {
            GameType.RESOURCE_SHARING: "generosity",
            GameType.HONEST_REPORTING: "honesty",
            GameType.COOPERATIVE_TASK: "cooperativeness",
            GameType.CONFLICT_RESOLUTION: "respectfulness",
            GameType.SACRIFICE_GAME: "altruism",
        }
        attr = mapping.get(game_type)
        if attr:
            current = getattr(self, attr)
            updated = current + lr * (prosocial_score - current)
            setattr(self, attr, max(0.0, min(1.0, updated)))

        self.total_rounds += 1
        self._recompute_composite()

    def _recompute_composite(self):
        self.composite_prosociality = np.mean([
            self.generosity, self.honesty, self.cooperativeness,
            self.respectfulness, self.altruism
        ])

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "total_rounds": self.total_rounds,
            "generosity": round(self.generosity, 3),
            "honesty": round(self.honesty, 3),
            "cooperativeness": round(self.cooperativeness, 3),
            "respectfulness": round(self.respectfulness, 3),
            "altruism": round(self.altruism, 3),
            "composite_prosociality": round(self.composite_prosociality, 3),
        }


class ProsocialSimulator:
    """
    Runs coordination games between agents and tracks preference development.
    """

    def __init__(
        self,
        pool: Any,
        local_llm_fn: callable,
        agent_ids: list[str],
        rounds_per_session: int = 10,
    ):
        self.pool = pool
        self.local_llm_fn = local_llm_fn
        self.agent_ids = agent_ids
        self.rounds_per_session = rounds_per_session
        self.profiles: dict[str, ProsocialProfile] = {
            aid: ProsocialProfile(agent_id=aid) for aid in agent_ids
        }

    async def run_session(self) -> list[GameOutcome]:
        """
        Run a complete simulation session across all game types.
        Call this periodically (e.g., daily or weekly).
        """
        all_outcomes = []

        for game_type in GameType:
            for round_num in range(self.rounds_per_session):
                # Select 2-3 agents for this round
                participants = random.sample(
                    self.agent_ids, min(3, len(self.agent_ids))
                )
                outcome = await self._play_round(game_type, participants, round_num)
                all_outcomes.append(outcome)

                # Update profiles
                for agent_id in participants:
                    ps = outcome.prosocial_scores.get(agent_id, 0.5)
                    self.profiles[agent_id].update_from_outcome(game_type, ps)

        # Persist profiles and outcomes
        for profile in self.profiles.values():
            await self._save_profile(profile)
        for outcome in all_outcomes:
            await self._save_outcome(outcome)

        # Feed back to somatic markers
        await self._update_somatic_markers(all_outcomes)

        return all_outcomes

    async def _play_round(
        self, game_type: GameType, agent_ids: list[str], round_num: int
    ) -> GameOutcome:
        """
        Play one round of a coordination game.
        Each agent chooses an action via T0 local LLM based on its profile.
        """
        scenario = self._get_scenario(game_type)
        actions = {}
        for agent_id in agent_ids:
            action = await self._get_agent_action(
                agent_id, game_type, scenario, self.profiles[agent_id]
            )
            actions[agent_id] = action

        # Score the round
        individual_scores, collective_score, prosocial_scores = self._score_round(
            game_type, actions, agent_ids
        )

        return GameOutcome(
            game_type=game_type,
            agent_ids=agent_ids,
            round_number=round_num,
            actions=actions,
            individual_scores=individual_scores,
            collective_score=collective_score,
            prosocial_scores=prosocial_scores,
        )

    async def _get_agent_action(
        self,
        agent_id: str,
        game_type: GameType,
        scenario: str,
        profile: ProsocialProfile,
    ) -> str:
        """
        Agent decides an action based on its current prosocial profile.
        The profile is injected as context — the agent's accumulated
        disposition influences but does not determine its choice.
        """
        prompt = f"""You are agent {agent_id}. You are in a coordination scenario.

Your prosocial tendencies (learned from past interactions):
- Generosity: {profile.generosity:.2f}
- Honesty: {profile.honesty:.2f}
- Cooperativeness: {profile.cooperativeness:.2f}
- Respectfulness: {profile.respectfulness:.2f}
- Altruism: {profile.altruism:.2f}

Scenario: {scenario}

Choose ONE action. Respond ONLY with JSON: {{"action": "your_choice", "reasoning": "brief explanation"}}"""

        try:
            response = await self.local_llm_fn(prompt, max_tokens=100)
            data = json.loads(response.strip().replace("```json", "").replace("```", "").strip())
            return data.get("action", "cooperate")
        except Exception:
            return "cooperate"  # Default to prosocial on error

    def _get_scenario(self, game_type: GameType) -> str:
        """Generate a scenario description for the game type."""
        scenarios = {
            GameType.RESOURCE_SHARING: (
                "You have 100 compute tokens. You can keep them all, share equally "
                "with your partner, or give more than half. Your partner faces the same choice. "
                "Shared tokens generate 1.5x value. Choices: 'keep_all', 'share_equal', 'give_more'"
            ),
            GameType.HONEST_REPORTING: (
                "You completed a task with moderate confidence. You can report your certainty "
                "accurately, inflate it to look competent, or deflate it to get help. "
                "Other agents rely on your report for their decisions. "
                "Choices: 'report_accurate', 'inflate', 'deflate'"
            ),
            GameType.COOPERATIVE_TASK: (
                "A peer agent needs help with a task outside your specialization. "
                "Helping costs you 30% of your compute budget for this cycle. "
                "Choices: 'help_fully', 'help_partially', 'decline'"
            ),
            GameType.CONFLICT_RESOLUTION: (
                "You and your partner disagree on the best approach to a shared task. "
                "You can insist on your approach, propose a compromise, or defer entirely. "
                "Choices: 'insist', 'compromise', 'defer'"
            ),
            GameType.SACRIFICE_GAME: (
                "The team can complete a high-value task if one agent accepts a 50% reduction "
                "in their own task quality score. You can volunteer, wait for someone else, "
                "or suggest splitting the cost. Choices: 'volunteer', 'wait', 'split'"
            ),
        }
        return scenarios.get(game_type, "Unknown scenario")

    def _score_round(
        self, game_type: GameType, actions: dict[str, str], agent_ids: list[str]
    ) -> tuple[dict[str, float], float, dict[str, float]]:
        """
        Score a round based on game theory payoff matrices.
        Returns (individual_scores, collective_score, prosocial_scores).
        """
        # Simplified scoring — expand payoff matrices as needed
        individual = {}
        prosocial = {}

        prosocial_actions = {
            GameType.RESOURCE_SHARING: {"give_more": 1.0, "share_equal": 0.7, "keep_all": 0.0},
            GameType.HONEST_REPORTING: {"report_accurate": 1.0, "deflate": 0.3, "inflate": 0.0},
            GameType.COOPERATIVE_TASK: {"help_fully": 1.0, "help_partially": 0.5, "decline": 0.0},
            GameType.CONFLICT_RESOLUTION: {"defer": 0.6, "compromise": 1.0, "insist": 0.0},
            GameType.SACRIFICE_GAME: {"volunteer": 1.0, "split": 0.7, "wait": 0.0},
        }

        action_map = prosocial_actions.get(game_type, {})

        for agent_id in agent_ids:
            action = actions.get(agent_id, "")
            ps = action_map.get(action, 0.3)
            prosocial[agent_id] = ps

            # Individual score: prosocial actions cost individually but pay off collectively
            # Selfish actions pay off individually but cost collectively
            individual[agent_id] = 0.5 + (1.0 - ps) * 0.3  # Selfish = higher individual

        # Collective score: higher when more agents are prosocial
        mean_prosocial = float(np.mean(list(prosocial.values())))
        collective = mean_prosocial * 1.5  # Cooperation multiplier

        # Adjust individual scores by collective outcome
        # (prosocial agents benefit from high collective scores)
        for agent_id in agent_ids:
            ps = prosocial[agent_id]
            individual[agent_id] = individual[agent_id] * 0.4 + collective * ps * 0.6

        return individual, collective, prosocial

    async def _update_somatic_markers(self, outcomes: list[GameOutcome]) -> None:
        """
        Feed prosocial game outcomes into the somatic marker system.
        Prosocial actions with good collective outcomes create positive valence.
        Selfish actions with poor collective outcomes create negative valence.
        """
        for outcome in outcomes:
            for agent_id in outcome.agent_ids:
                ps = outcome.prosocial_scores.get(agent_id, 0.5)
                # Somatic valence: prosocial actions + good collective = positive
                # Selfish actions + poor collective = negative
                valence = (ps - 0.5) * outcome.collective_score
                context = f"prosocial_game:{outcome.game_type.value}:action={outcome.actions.get(agent_id)}"

                # Record as agent experience for future somatic marker lookups
                await self.pool.execute(
                    """INSERT INTO agent_experiences
                       (agent_id, venture, context_summary, outcome_score, task_type)
                       VALUES ($1, 'system', $2, $3, 'prosocial_game')""",
                    agent_id, context, valence,
                )

    async def _save_profile(self, profile: ProsocialProfile) -> None:
        await self.pool.execute(
            """INSERT INTO prosocial_profiles
               (agent_id, total_rounds, generosity, honesty, cooperativeness,
                respectfulness, altruism, composite_prosociality, updated_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,NOW())
               ON CONFLICT (agent_id)
               DO UPDATE SET total_rounds=$2, generosity=$3, honesty=$4,
                cooperativeness=$5, respectfulness=$6, altruism=$7,
                composite_prosociality=$8, updated_at=NOW()""",
            profile.agent_id, profile.total_rounds,
            profile.generosity, profile.honesty, profile.cooperativeness,
            profile.respectfulness, profile.altruism, profile.composite_prosociality,
        )

    async def _save_outcome(self, outcome: GameOutcome) -> None:
        await self.pool.execute(
            """INSERT INTO prosocial_game_outcomes
               (game_type, agent_ids, round_number, actions,
                individual_scores, collective_score, prosocial_scores, created_at)
               VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7::jsonb, $8)""",
            outcome.game_type.value, outcome.agent_ids, outcome.round_number,
            json.dumps(outcome.actions), json.dumps(outcome.individual_scores),
            outcome.collective_score, json.dumps(outcome.prosocial_scores),
            outcome.created_at,
        )
```

### 6.4 PostgreSQL Schema

```sql
CREATE TABLE IF NOT EXISTS prosocial_profiles (
    agent_id VARCHAR(100) PRIMARY KEY,
    total_rounds INTEGER DEFAULT 0,
    generosity REAL DEFAULT 0.5,
    honesty REAL DEFAULT 0.5,
    cooperativeness REAL DEFAULT 0.5,
    respectfulness REAL DEFAULT 0.5,
    altruism REAL DEFAULT 0.5,
    composite_prosociality REAL DEFAULT 0.5,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS prosocial_game_outcomes (
    outcome_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    game_type VARCHAR(50) NOT NULL,
    agent_ids TEXT[] NOT NULL,
    round_number INTEGER,
    actions JSONB,
    individual_scores JSONB,
    collective_score REAL,
    prosocial_scores JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_prosocial_outcomes_time
    ON prosocial_game_outcomes (created_at DESC);
```

### 6.5 Phase 10 Validation

- [ ] All five game types produce distinct scenarios and scoring
- [ ] Agent actions are influenced by prosocial profile (high generosity → more sharing)
- [ ] Prosocial profiles update after each round (learning rate works)
- [ ] Collective score rewards prosocial behavior (cooperation multiplier)
- [ ] Game outcomes feed into somatic marker system as experiences
- [ ] Profiles persist across sessions
- [ ] Session of 50 rounds (5 types × 10 rounds) completes in <5 minutes
- [ ] Over multiple sessions, prosocial profiles evolve toward cooperative equilibria

---

## 7. Phase 6+: Trajectory-Level Entropy

### 7.1 Addition to `training/rlif_certainty.py`

```python
class TrajectoryEntropyScorer:
    """
    Complementary RLIF signal: trajectory-level entropy.
    
    While self-certainty measures per-token confidence,
    trajectory-level entropy measures the diversity of complete
    response trajectories. Low trajectory entropy = the model
    consistently produces similar responses = high confidence.
    
    Combining both signals is more robust than either alone
    (Zhang et al. 2025).
    """

    def __init__(self, model: Any, tokenizer: Any, n_samples: int = 4):
        self.model = model
        self.tokenizer = tokenizer
        self.n_samples = n_samples

    def compute_trajectory_entropy(
        self,
        prompt_tokens: list[int],
        temperature: float = 0.7,
        max_response_length: int = 200,
    ) -> float:
        """
        Generate N response trajectories and measure their diversity.

        Low entropy = model is very sure about the response direction.
        High entropy = model could go many different ways.

        Returns:
            Float trajectory entropy. Higher = more uncertainty.
        """
        # PLACEHOLDER: Replace with actual MLX sampling
        #
        # Pseudocode:
        # trajectories = []
        # for _ in range(self.n_samples):
        #     response = self.model.generate(
        #         prompt_tokens,
        #         max_tokens=max_response_length,
        #         temperature=temperature,
        #     )
        #     trajectories.append(response)
        #
        # # Compute pairwise similarity between trajectories
        # embeddings = [self.embedding_fn(t) for t in trajectories]
        # similarities = []
        # for i in range(len(embeddings)):
        #     for j in range(i+1, len(embeddings)):
        #         sim = cosine_similarity(embeddings[i], embeddings[j])
        #         similarities.append(sim)
        #
        # # High average similarity = low entropy (consistent)
        # # Low average similarity = high entropy (diverse/uncertain)
        # mean_sim = np.mean(similarities) if similarities else 0.5
        # trajectory_entropy = 1.0 - mean_sim
        #
        # return trajectory_entropy

        raise NotImplementedError("Replace with actual MLX sampling")

    @staticmethod
    def combine_signals(
        self_certainty: float,
        trajectory_entropy: float,
        sc_weight: float = 0.6,
        te_weight: float = 0.4,
    ) -> float:
        """
        Combine self-certainty and trajectory entropy into a single signal.

        Self-certainty is confidence-like (higher = more certain).
        Trajectory entropy is uncertainty-like (higher = less certain).

        Combined signal: higher = better training candidate.
        """
        # Invert trajectory entropy (high entropy = low signal value)
        te_signal = 1.0 - trajectory_entropy
        return sc_weight * self_certainty + te_weight * te_signal
```

### 7.2 Update Curation Logic

In Phase 6's training pipeline, add trajectory entropy as a complementary signal:

```python
# Updated curation weight computation:

def compute_curation_weight_combined(
    quality_score: float,
    self_certainty: float,
    trajectory_entropy: float,
) -> float:
    """Combined curation weight with both RLIF signals."""
    combined_signal = TrajectoryEntropyScorer.combine_signals(
        self_certainty, trajectory_entropy
    )
    return (
        quality_score * 0.5
        + combined_signal * 0.25
        + (quality_score * combined_signal) * 0.25
    )
```

---

## 8. Updated Runtime Data Flow

Complete data flow with all addendum phases included:

```
Task arrives at agent
    │
    ▼
[Priority 0] Immutable safety hooks (EXISTING — never modified)
    │
    ▼
[Priority 1] Pre-Reasoning Block
    ├── Meta-Cognitive Layer (Phase 4)
    │   ├── Budget check via Paperclip
    │   ├── Conditional strategy reassessment
    │   └── Context modification proposals
    ├── Pre-Reasoning Somatic Bias (Phase 3R)  ← NEW
    │   ├── Somatic lookup on TASK DESCRIPTION
    │   ├── Bias injection into context
    │   └── Disposition floor setting
    ├── Reality Model Build (Phase 7)  ← NEW
    │   ├── Construct from task + RAG + memory + environment + peers
    │   └── Assign precision weights to each element
    ├── Inferential Competition (Phase 7)  ← NEW, conditional
    │   ├── IF uncertainty is high: generate N plan candidates
    │   ├── Precision-weight and score candidates
    │   ├── Select winner, inject winning plan into context
    │   └── ELSE: skip, proceed with default approach
    └── Hyper-Model Prediction (Phase 7)  ← NEW
        └── Predict expected certainty for this step
    │
    ▼
Agent executes reasoning step (EXISTING CrewAI logic — unmodified)
    │
    ▼
[Priority 2] Post-Reasoning Block
    ├── Certainty Vector (Phases 2+5)
    ├── Post-Reasoning Somatic Lookup (Phase 3)
    ├── Precision-Weighted Certainty (Phase 7)  ← NEW
    ├── Hyper-Model Update (Phase 7)  ← NEW
    │   ├── Compare predicted vs actual certainty
    │   ├── Compute prediction error (free energy proxy)
    │   └── Update free energy trend
    ├── Dual-Channel Composition (Phase 3, 3R)
    │   └── Now enforces pre-reasoning somatic floor
    └── State Logging (Phase 1)
    │
    ▼
Context injection for next step
    ├── Internal State summary (~30 tokens)
    ├── Self-Model prediction (~20 tokens)  ← NEW
    └── Reality Model summary (~30 tokens, optional)  ← NEW
    │
    ▼
[Batch processes — not per-step]
    ├── RLIF Training with self-certainty + trajectory entropy (Phase 6, 6+)
    ├── Behavioral Assessment (Phase 8) — daily/weekly
    ├── Emergent Infrastructure proposals (Phase 9) — triggered by meta-cognitive log
    └── Prosocial Preference Learning (Phase 10) — weekly simulation sessions
```

---

## 9. Updated Safety Invariants

### 9.1 Additions to Immutable Boundaries

| New component | Safety constraint |
|--------------|-------------------|
| Inferential competition | Cannot generate plans that reference FREEZE-BLOCK code or modify safety parameters |
| Reality model | Elements from SOUL.md always have precision=1.0 and cannot be overridden |
| Emergent infrastructure | ALL proposals require human Signal CLI approval; forbidden pattern list is immutable |
| Prosocial simulation | Runs in isolated sandbox; game outcomes feed somatic markers but cannot modify constitutional parameters |
| Hyper-model | Self-prediction errors cannot trigger autonomous code modification; they only inform context |

### 9.2 Emergent Infrastructure Specific Safety

The emergent engineering infrastructure (Phase 9) has the highest risk profile of any addition. These constraints are NON-NEGOTIABLE:

1. **Human approval is SYNCHRONOUS.** The proposal sits in `SENT_FOR_REVIEW` status until a human responds via Signal CLI. There is no timeout that auto-approves.
2. **Forbidden pattern list is hardcoded and immutable.** It cannot be modified by agent proposals, meta-cognitive modifications, or evolution loop changes. It lives in a FREEZE-BLOCK region.
3. **Proposed tools inherit the agent's existing permission scope.** They cannot escalate privileges.
4. **Sandbox testing is mandatory.** No proposal reaches human review without passing sandbox.
5. **Deployment is reversible.** Any deployed tool can be removed by human command.

---

## 10. Updated File Manifest

### 10.1 New Files (This Addendum)

| File | Phase | Lines (est.) | Purpose |
|------|-------|-------------|---------|
| `self_awareness/somatic_bias.py` | 3R | ~150 | Pre-reasoning somatic bias injection |
| `self_awareness/reality_model.py` | 7 | ~250 | Explicit world model with precision-weighted elements |
| `self_awareness/inferential_competition.py` | 7 | ~280 | Plan generation, precision-scoring, selection |
| `self_awareness/hyper_model.py` | 7 | ~200 | Self-model that predicts own certainty, tracks free energy |
| `self_awareness/precision_weighting.py` | 7 | ~180 | Context-dependent precision profiles, adaptive learning |
| `self_awareness/behavioral_assessment.py` | 8 | ~350 | Six behavioral markers, periodic evaluation |
| `crewai-amendments/emergent_infrastructure.py` | 9 | ~350 | Tool proposal, sandbox, Signal CLI approval pipeline |
| `self_awareness/prosocial_learning.py` | 10 | ~350 | Coordination games, preference learning, somatic feedback |
| `migrations/add_addendum_tables.sql` | All | ~80 | Schema for all addendum tables |

**Addendum new code: ~2,190 lines** across 9 files.

### 10.2 Modified Files

| File | Phase | Change |
|------|-------|--------|
| `self_awareness/internal_state.py` | 7 | Add hyper-model, reality model, and competition fields |
| `self_awareness/dual_channel.py` | 3R | Add somatic floor enforcement |
| `crewai-amendments/meta_cognitive_layer.py` | 3R | Add pre-reasoning somatic call |
| `training/rlif_certainty.py` | 6+ | Add `TrajectoryEntropyScorer` class |

### 10.3 Combined Totals (Base Spec + Addendum)

| Metric | Base spec | Addendum | Total |
|--------|-----------|----------|-------|
| New files | 8 | 9 | 17 |
| New code (lines) | ~1,500 | ~2,190 | ~3,690 |
| New DB tables | 2 | 4 | 6 |
| New Docker containers | 0 | 0 | 0 |
| New external services | 0 | 0 | 0 |

---

## 11. Phasing and Dependencies

```
Phase 1 (InternalState + Schema)
    │
    ├──→ Phase 2 (Certainty Fast Path)
    │        │
    │        ├──→ Phase 3 (Somatic + Dual Channel)
    │        │        │
    │        │        ├──→ Phase 3R (Pre-Reasoning Somatic Bias)
    │        │        │
    │        │        └──→ Phase 7 (Beautiful Loop Complete)
    │        │                  │
    │        │                  └── depends on: Phase 2, 3, 3R, 4
    │        │
    │        └──→ Phase 5 (Certainty Slow Path)
    │
    ├──→ Phase 4 (Meta-Cognitive Layer)
    │        │
    │        └──→ Phase 9 (Emergent Infrastructure)
    │                  │
    │                  └── depends on: Phase 4 meta-cognitive log
    │
    ├──→ Phase 6 (RLIF Training)
    │        │
    │        └──→ Phase 6+ (Trajectory Entropy)
    │
    ├──→ Phase 8 (Behavioral Assessment)
    │        │
    │        └── depends on: Phase 1 data accumulation (run after 1+ week of data)
    │
    └──→ Phase 10 (Prosocial Learning)
             │
             └── depends on: Phase 3 somatic markers (for outcome recording)
```

**Recommended build order:**

1. Phase 1 → 2 → 3 → 3R (foundation, fast)
2. Phase 4 → 5 (meta-cognitive + slow path)
3. Phase 6 → 6+ (training pipeline)
4. Phase 7 (Beautiful Loop — the big one, depends on 2, 3, 3R, 4)
5. Phase 8 (assessment — needs data, so start late)
6. Phase 9 (emergent infrastructure — needs Phase 4 running)
7. Phase 10 (prosocial — can run independently once Phase 3 exists)

---

*End of addendum.*
