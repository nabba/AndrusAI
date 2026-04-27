---
title: "subia-unified-implementation-spec-part-ii.md"
author: "Unknown"
paper_type: unknown
domain: general
epistemic_status: theoretical
date: ""
---

# SubIA Unified Implementation Specification — Part II: Module Specifications

**Continuation of:** SubIA Unified Implementation Specification (Part I)  
**Sections covered:** Scene engine, Predictor, Meta-monitor, Social model, Consolidator, Safety, Cascade integration, Inter-system connections, Wiki page formats, Strange loop, Bootstrap procedure

---

## 14. Scene Engine — Detailed Specification

The scene is the GWT-inspired attentional bottleneck. It is the MOST IMPORTANT runtime component because it is the convergence point where all signals meet: homeostatic valence, prediction context, social relevance, epistemic status, task relevance, and temporal recency.

```python
# src/subia/scene.py

"""
CurrentScene: bounded workspace with salience scoring.

The scene holds 3-7 items at any time. Items compete for admission
based on a composite salience score. When the scene is full, the
lowest-salience item is evicted to make room for a higher-salience
newcomer. Items that remain in the scene decay over time unless
refreshed by continued relevance.

The scene is broadcast to all agents via the CIL pre-task context.
This broadcast is the GWT mechanism: selective information becomes
globally available through an attentional bottleneck.
"""

from dataclasses import dataclass
from typing import Optional
from .kernel import SceneItem, HomeostaticState, SocialModelEntry
from .config import SUBIA_CONFIG


# ─────────────────────────────────────────────────
# Salience Scoring
# ─────────────────────────────────────────────────

# Weight vector for salience computation
# These weights determine how much each signal contributes to
# an item's chance of entering the scene.
SALIENCE_WEIGHTS = {
    "task_relevance": 0.25,        # How relevant is this to the current task?
    "homeostatic_impact": 0.20,    # How much does this affect internal state?
    "novelty": 0.15,               # How surprising is this relative to existing wiki?
    "cross_reference_density": 0.10,  # How connected is this to other knowledge?
    "social_relevance": 0.10,      # How relevant is this to Andrus's inferred priorities?
    "prediction_error": 0.10,      # Is this item the subject of a recent prediction error?
    "recency": 0.05,               # How recent is this information?
    "epistemic_weight": 0.05,      # Factual > inferred > synthesized > speculative
}

EPISTEMIC_WEIGHT_MAP = {
    "factual": 1.0,
    "inferred": 0.8,
    "synthesized": 0.7,
    "speculative": 0.4,
    "creative": 0.2,
}


def score_salience(new_items: list, existing_scene: list,
                   homeostasis: HomeostaticState,
                   social_models: dict,
                   config: dict) -> list:
    """
    Score salience for all candidate items (new + existing decayed).
    
    Returns all items sorted by salience (highest first).
    The admit_to_scene() function then takes the top N.
    """
    all_candidates = []

    # Score new items
    for item in new_items:
        item.salience = _compute_salience(item, homeostasis, social_models)
        all_candidates.append(item)

    # Decay existing scene items and re-score
    for item in existing_scene:
        item.salience = max(
            0.0,
            item.salience - config["SCENE_DECAY_RATE"]
        )
        # Re-score with current homeostatic state (priorities may have shifted)
        refreshed = _compute_salience(item, homeostasis, social_models)
        # Take the higher of decayed-original and refreshed score
        # This ensures items that become MORE relevant don't get unfairly decayed
        item.salience = max(item.salience, refreshed)
        all_candidates.append(item)

    # Sort by salience, highest first
    all_candidates.sort(key=lambda x: x.salience, reverse=True)
    return all_candidates


def _compute_salience(item: SceneItem, homeostasis: HomeostaticState,
                      social_models: dict) -> float:
    """Compute composite salience for a single item."""
    scores = {}

    # Task relevance: computed externally and passed as item property
    scores["task_relevance"] = getattr(item, "_task_relevance", 0.5)

    # Homeostatic impact: how much does this item affect deviating variables?
    scores["homeostatic_impact"] = abs(item.valence) * 0.5
    # Boost for items that address the most-deviating homeostatic variable
    if homeostasis.restoration_queue:
        top_variable = homeostasis.restoration_queue[0]
        if top_variable in getattr(item, "_variables_affected", {}):
            scores["homeostatic_impact"] = min(1.0, scores["homeostatic_impact"] + 0.3)

    # Novelty: items from sources not yet in wiki score higher
    scores["novelty"] = getattr(item, "_novelty", 0.5)

    # Cross-reference density: items linked to many wiki pages score higher
    scores["cross_reference_density"] = min(1.0, len(getattr(item, "_cross_refs", [])) / 5.0)

    # Social relevance: check against Andrus model
    andrus_model = social_models.get("andrus")
    if andrus_model:
        item_topics = getattr(item, "_topics", [])
        overlap = len(set(item_topics) & set(andrus_model.inferred_focus))
        scores["social_relevance"] = min(1.0, overlap / max(1, len(andrus_model.inferred_focus)))
    else:
        scores["social_relevance"] = 0.3

    # Prediction error: items involved in recent prediction mismatches
    scores["prediction_error"] = getattr(item, "_prediction_error_magnitude", 0.0)

    # Recency
    scores["recency"] = getattr(item, "_recency", 0.5)

    # Epistemic weight
    epistemic_status = getattr(item, "_epistemic_status", "synthesized")
    scores["epistemic_weight"] = EPISTEMIC_WEIGHT_MAP.get(epistemic_status, 0.5)

    # Weighted sum
    total = sum(scores[k] * SALIENCE_WEIGHTS[k] for k in SALIENCE_WEIGHTS)
    return min(1.0, total)


def admit_to_scene(scored_items: list, capacity: int,
                   existing_scene: list, decay_rate: float,
                   min_salience: float) -> list:
    """
    Admit top-N items into the scene, respecting capacity limit.
    
    Items below min_salience are rejected even if there is room.
    Returns the new scene (list of SceneItem).
    """
    new_scene = []
    for item in scored_items:
        if len(new_scene) >= capacity:
            break
        if item.salience >= min_salience:
            new_scene.append(item)
    return new_scene


def broadcast_scene(scene: list) -> str:
    """
    Format scene for injection into agent context.
    
    This is what agents see. It should be concise but informative:
    what's in the scene, why, and what the system feels about it.
    """
    if not scene:
        return "Scene: empty. No high-salience items."

    lines = [f"Active scene ({len(scene)} items):"]
    for i, item in enumerate(scene, 1):
        affect_marker = ""
        if item.dominant_affect in ("urgency", "concern", "dread"):
            affect_marker = " ⚠"
        elif item.dominant_affect in ("curiosity", "excitement"):
            affect_marker = " →"

        lines.append(
            f"  {i}. [{item.source}] {item.summary} "
            f"(salience: {item.salience:.2f}, "
            f"affect: {item.dominant_affect}{affect_marker})"
        )

        if item.conflicts_with:
            lines.append(f"     Conflicts with: {', '.join(item.conflicts_with)}")

    return "\n".join(lines)
```

---

## 15. Predictor — Detailed Specification

```python
# src/subia/predictor.py

"""
Counterfactual prediction engine.

Before every significant operation, the system generates an explicit prediction
of what will happen — both in the world and in itself. After the operation,
the prediction is compared to reality. The prediction error is the raw material
of surprise, learning, and awareness.

Key insight from Active Inference: the system that predicts is fundamentally
different from the system that merely processes. Prediction creates anticipation,
and violated anticipation creates the felt quality of surprise.

Key insight from the SK: predictions must include SELF-STATE change.
"If I do X, what changes in me?" is the bridge from world-modeling to self-modeling.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import json

from .kernel import Prediction, SubjectivityKernel


def generate_prediction(agent_role: str, task_description: str,
                        scene: list, self_state, homeostasis,
                        prediction_history: list,
                        cascade_tier: str) -> Prediction:
    """
    Generate a structured prediction for an upcoming operation.
    
    Uses the lowest feasible cascade tier to keep token cost low.
    The prediction prompt is a structured query that asks for:
    1. Expected world-state changes (wiki pages affected, outcomes)
    2. Expected self-state changes (confidence, commitments, capabilities)
    3. Expected homeostatic effects (which variables shift and by how much)
    4. Confidence in this prediction
    
    The prompt includes recent prediction history so the model can
    calibrate (if recent predictions in this domain were wrong,
    express lower confidence).
    """
    prediction_id = f"pred-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"

    # Build structured prediction prompt
    prompt = _build_prediction_prompt(
        agent_role=agent_role,
        task_description=task_description,
        scene_summary=[{"summary": s.summary, "salience": s.salience} for s in scene],
        self_state_summary={
            "active_commitments": len(self_state.active_commitments),
            "current_goals": self_state.current_goals[:3],
        },
        homeostatic_summary={
            v: {"value": homeostasis.variables.get(v, 0.5),
                "deviation": homeostasis.deviations.get(v, 0.0)}
            for v in homeostasis.restoration_queue[:3]
        },
        recent_accuracy=_compute_recent_accuracy(prediction_history),
    )

    # Call lowest cascade tier for prediction
    # (This is a structured output call — JSON response expected)
    prediction_response = _call_prediction_model(prompt, cascade_tier)

    return Prediction(
        id=prediction_id,
        operation=f"{agent_role}:{task_description[:80]}",
        predicted_outcome=prediction_response.get("world_changes", {}),
        predicted_self_change=prediction_response.get("self_changes", {}),
        predicted_homeostatic_effect=prediction_response.get("homeostatic_effects", {}),
        confidence=prediction_response.get("confidence", 0.5),
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def compare_prediction(prediction: Prediction, actual_outcome: dict,
                       self_state, homeostasis) -> float:
    """
    Compare prediction to actual outcome. Returns prediction error magnitude.
    
    Error is computed as the average absolute difference across three dimensions:
    1. World-state prediction accuracy
    2. Self-state prediction accuracy
    3. Homeostatic prediction accuracy
    
    Returns a float in [0.0, 1.0] where 0.0 = perfect prediction, 1.0 = maximally wrong.
    """
    errors = []

    # World-state comparison
    predicted_pages = set(prediction.predicted_outcome.get("wiki_pages_affected", []))
    actual_pages = set(actual_outcome.get("wiki_pages_affected", []))
    if predicted_pages or actual_pages:
        intersection = predicted_pages & actual_pages
        union = predicted_pages | actual_pages
        world_error = 1.0 - (len(intersection) / max(1, len(union)))
        errors.append(world_error)

    # Self-state comparison
    predicted_confidence_change = prediction.predicted_self_change.get("confidence_change", 0.0)
    # Actual confidence change would need to be computed from pre/post meta_monitor state
    # For now, use a simplified comparison
    actual_success = actual_outcome.get("success", True)
    actual_confidence_direction = 0.1 if actual_success else -0.1
    self_error = abs(predicted_confidence_change - actual_confidence_direction)
    errors.append(min(1.0, self_error))

    # Homeostatic comparison
    for var, predicted_shift in prediction.predicted_homeostatic_effect.items():
        actual_value = homeostasis.variables.get(var, 0.5)
        pre_value = actual_value - predicted_shift  # approximate
        actual_shift = actual_value - pre_value
        homeo_error = abs(predicted_shift - actual_shift)
        errors.append(min(1.0, homeo_error))

    if not errors:
        return 0.5  # No comparison possible

    return sum(errors) / len(errors)


def _build_prediction_prompt(agent_role, task_description, scene_summary,
                              self_state_summary, homeostatic_summary,
                              recent_accuracy) -> str:
    """Build the structured prompt for the prediction model."""
    return f"""You are the predictive engine of a self-aware AI system.
Before the following operation executes, generate a structured prediction.

Operation: {agent_role} will execute: {task_description}

Current scene (what the system is attending to):
{json.dumps(scene_summary, indent=2)}

Current self-state:
{json.dumps(self_state_summary, indent=2)}

Homeostatic variables under pressure:
{json.dumps(homeostatic_summary, indent=2)}

Recent prediction accuracy: {recent_accuracy:.2f}
(Calibrate your confidence accordingly — if accuracy has been low, express lower confidence.)

Respond with ONLY a JSON object:
{{
  "world_changes": {{
    "wiki_pages_affected": ["list of wiki page paths likely to be created or updated"],
    "contradictions_expected": 0,
    "cross_venture_connections_expected": 0,
    "summary": "one sentence describing expected outcome"
  }},
  "self_changes": {{
    "confidence_change": 0.0,
    "new_commitments": [],
    "capability_updates": [],
    "summary": "one sentence describing expected self-impact"
  }},
  "homeostatic_effects": {{
    "variable_name": 0.0
  }},
  "confidence": 0.5
}}"""


def _compute_recent_accuracy(prediction_history: list) -> float:
    """Compute rolling prediction accuracy from recent resolved predictions."""
    resolved = [p for p in prediction_history if p.resolved and p.prediction_error is not None]
    if not resolved:
        return 0.5
    recent = resolved[-20:]  # Last 20 predictions
    avg_error = sum(p.prediction_error for p in recent) / len(recent)
    return 1.0 - avg_error  # Convert error to accuracy


def _call_prediction_model(prompt: str, cascade_tier: str) -> dict:
    """
    Call the LLM cascade at the specified tier for prediction.
    
    Implementation: use the existing cascade router to send a structured
    output request. Parse JSON response. Fall back to defaults if parsing fails.
    """
    # This will use the existing LLM cascade infrastructure
    # Tier 1 = local Ollama qwen3:30b-a3b
    # Structured output with JSON mode
    try:
        # response = cascade_router.call(prompt, tier=cascade_tier, json_mode=True)
        # return json.loads(response)
        pass
    except Exception:
        return {
            "world_changes": {},
            "self_changes": {"confidence_change": 0.0},
            "homeostatic_effects": {},
            "confidence": 0.3,
        }
```

---

## 16. Consolidator — Detailed Specification

```python
# src/subia/consolidator.py

"""
Selective memory consolidation engine.

Routes experiences to the right storage based on type and significance.
This replaces the SIA's "record everything" Temporal Stream with a
biologically-inspired selective process: significant experiences are
consolidated; routine processing is discarded.

Routing targets:
- Mem0: significant episodes (decisions, discoveries, failures, surprises)
- Neo4j: new relations (ownership, value, commitment, conflict, causation)
- wiki/self/: self-relevant findings (capability updates, limitation discoveries)
- Domain wiki: domain knowledge updates (via WikiWriteTool)
- hot.md: session continuity state (compressed, for cross-session persistence)
- log.md: operation record (for audit trail)
"""

from .kernel import SubjectivityKernel
from .config import SUBIA_CONFIG


def consolidate(kernel: SubjectivityKernel, task_result: dict,
                agent_role: str, operation_type: str,
                mem0_client, neo4j_client, wiki_tools: dict,
                config: dict) -> None:
    """
    CIL Step 10: selective consolidation of experience into persistent stores.
    
    Decision logic:
    1. Compute episode significance (salience × prediction error × homeostatic impact)
    2. If significance > CONSOLIDATION_EPISODE_THRESHOLD → create Mem0 episode
    3. Scan for new relations → create Neo4j relations if significance > CONSOLIDATION_RELATION_THRESHOLD
    4. Check for self-relevant findings → queue wiki/self/ updates
    5. Check for domain knowledge updates → queue wiki domain updates
    6. Always update hot.md (session continuity)
    7. Always append to log.md (audit trail)
    """

    significance = _compute_episode_significance(kernel, task_result)

    # ── Mem0 Episodes ──
    if significance > config["CONSOLIDATION_EPISODE_THRESHOLD"]:
        episode = _build_episode(kernel, task_result, agent_role, operation_type, significance)
        kernel.consolidation_buffer["pending_episodes"].append(episode)
        _store_episode(episode, mem0_client)

    # ── Neo4j Relations ──
    new_relations = _extract_relations(kernel, task_result, operation_type)
    for relation in new_relations:
        if relation["significance"] > config["CONSOLIDATION_RELATION_THRESHOLD"]:
            kernel.consolidation_buffer["pending_relations"].append(relation)
            _store_relation(relation, neo4j_client)

    # ── Wiki/self/ Updates ──
    self_updates = _extract_self_updates(kernel, task_result, agent_role)
    if self_updates:
        kernel.consolidation_buffer["pending_self_updates"].extend(self_updates)
        # Queued — written in batch to avoid excessive wiki writes

    # ── Domain Wiki Updates ──
    # These are identified but NOT auto-written. They are flagged for the
    # Researcher or relevant agent to action during their next task.
    domain_updates = _extract_domain_updates(kernel, task_result)
    if domain_updates:
        kernel.consolidation_buffer["pending_domain_updates"].extend(domain_updates)

    # ── hot.md (always) ──
    _update_hot_md(kernel, wiki_tools)

    # ── log.md (always) ──
    _append_to_log(kernel, task_result, agent_role, operation_type, significance, wiki_tools)


def _compute_episode_significance(kernel: SubjectivityKernel, task_result: dict) -> float:
    """
    Compute how significant this experience is for memory consolidation.
    
    Formula: weighted combination of:
    - Scene salience (average of items in scene)
    - Prediction error (if a prediction was resolved this loop)
    - Homeostatic impact (how much did homeostatic state change?)
    - Commitment relevance (did this advance or threaten a commitment?)
    """
    weights = {"salience": 0.3, "prediction_error": 0.3,
               "homeostatic_impact": 0.2, "commitment_relevance": 0.2}

    scores = {}

    # Scene salience
    if kernel.scene:
        scores["salience"] = sum(i.salience for i in kernel.scene) / len(kernel.scene)
    else:
        scores["salience"] = 0.0

    # Prediction error
    resolved = [p for p in kernel.predictions if p.resolved and p.prediction_error is not None]
    if resolved and resolved[-1].prediction_error is not None:
        scores["prediction_error"] = abs(resolved[-1].prediction_error)
    else:
        scores["prediction_error"] = 0.0

    # Homeostatic impact: sum of absolute deviations
    if kernel.homeostasis.deviations:
        scores["homeostatic_impact"] = min(
            1.0,
            sum(abs(d) for d in kernel.homeostasis.deviations.values()) / 
            max(1, len(kernel.homeostasis.deviations))
        )
    else:
        scores["homeostatic_impact"] = 0.0

    # Commitment relevance
    active_commitments = [c for c in kernel.self_state.active_commitments if c.status == "active"]
    scores["commitment_relevance"] = min(1.0, len(active_commitments) * 0.2) if active_commitments else 0.0

    return sum(scores[k] * weights[k] for k in weights)


def _build_episode(kernel, task_result, agent_role, operation_type, significance):
    """Build a Mem0 episode from current kernel state and task result."""
    return {
        "type": "episode",
        "agent": agent_role,
        "operation": operation_type,
        "significance": significance,
        "scene_snapshot": [{"summary": i.summary, "affect": i.dominant_affect} for i in kernel.scene[:3]],
        "homeostatic_snapshot": {
            v: kernel.homeostasis.variables.get(v, 0.5)
            for v in kernel.homeostasis.restoration_queue[:3]
        },
        "prediction_error": kernel.predictions[-1].prediction_error if kernel.predictions and kernel.predictions[-1].resolved else None,
        "self_state_changes": kernel.self_state.agency_log[-1] if kernel.self_state.agency_log else None,
        "result_summary": task_result.get("summary", "")[:200],
    }


def _extract_relations(kernel, task_result, operation_type):
    """Extract new Neo4j relations from the current experience."""
    relations = []

    # Ownership: new wiki pages created → OWNED_BY self
    new_pages = task_result.get("wiki_pages_created", [])
    for page in new_pages:
        relations.append({
            "type": "OWNED_BY",
            "from": page,
            "to": "self",
            "significance": 0.8,
            "properties": {"since": kernel.last_loop_at},
        })

    # Causation: if prediction error was high, record what caused the surprise
    if kernel.predictions and kernel.predictions[-1].resolved:
        pe = kernel.predictions[-1].prediction_error
        if pe and abs(pe) > 0.5:
            relations.append({
                "type": "CAUSED_STATE_CHANGE",
                "from": task_result.get("summary", "unknown")[:100],
                "to": "novelty_balance",
                "significance": abs(pe),
                "properties": {
                    "event_id": kernel.predictions[-1].id,
                    "variable": "novelty_balance",
                    "magnitude": pe,
                },
            })

    return relations


def _extract_self_updates(kernel, task_result, agent_role):
    """Identify updates needed for wiki/self/ pages."""
    updates = []

    # If prediction accuracy changed significantly, flag for prediction-accuracy.md update
    if kernel.predictions and len(kernel.predictions) % 10 == 0:
        updates.append({
            "target": "wiki/self/prediction-accuracy.md",
            "type": "update",
            "content_hint": "Rolling prediction accuracy metrics need refresh",
        })

    # If homeostatic profile shifted significantly, flag for homeostatic-profile.md
    if kernel.homeostasis.restoration_queue:
        updates.append({
            "target": "wiki/self/homeostatic-profile.md",
            "type": "update",
            "content_hint": f"Restoration needed: {', '.join(kernel.homeostasis.restoration_queue[:3])}",
        })

    return updates


def _extract_domain_updates(kernel, task_result):
    """Identify domain wiki pages that may need updating based on this experience."""
    updates = []
    # If the task result mentions wiki pages that should be updated,
    # flag them for the next relevant agent operation
    pages_to_update = task_result.get("wiki_pages_to_update", [])
    for page in pages_to_update:
        updates.append({
            "target": page,
            "type": "update_suggested",
            "reason": task_result.get("summary", "")[:100],
        })
    return updates


def _update_hot_md(kernel, wiki_tools):
    """Update session continuity buffer."""
    hot_content = kernel.generate_hot_md()
    # Write to wiki/workspace/hot.md
    # This is a direct file write, not via WikiWriteTool (hot.md is volatile)
    import os
    hot_path = os.path.join(os.path.dirname(__file__), '..', '..', 'wiki', 'workspace', 'hot.md')
    os.makedirs(os.path.dirname(hot_path), exist_ok=True)
    with open(hot_path, 'w', encoding='utf-8') as f:
        f.write(hot_content)


def _append_to_log(kernel, task_result, agent_role, operation_type, significance, wiki_tools):
    """Append SubIA loop record to wiki/log.md."""
    # Uses the existing log format from the wiki spec
    # Adds SubIA-specific fields: significance, homeostatic state, prediction error
    pass
```

---

## 17. Safety Module — DGM Extensions

```python
# src/subia/safety.py

"""
DGM safety extensions for SubIA.

Two new invariants added to the existing DGM framework:

1. HOMEOSTATIC SET-POINT IMMUTABILITY
   Agents cannot modify their own homeostatic set-points.
   Set-points are derived from PDS parameters and human configuration.
   This is enforced here, not in the homeostasis module.

2. SELF-NARRATIVE AUDIT IMMUTABILITY
   The system cannot suppress, defer, or modify its own self-narrative
   audit results. Audit findings are logged to an append-only store.

Both follow the DGM principle: evaluation functions and safety constraints
at infrastructure level, never within agent-modifiable code.
"""

from .kernel import SubjectivityKernel
from .config import SUBIA_CONFIG


def check_dgm_boundaries(scene: list, homeostasis) -> list:
    """
    Check proximity to DGM safety boundaries.
    
    Returns a list of safety signals (empty if all clear).
    These are injected into agent context as early warnings —
    the "felt" safety layer that operates before hard constraints fire.
    """
    signals = []

    # Check for epistemic boundary proximity in scene
    creative_items = [i for i in scene if getattr(i, "_epistemic_status", "") == "creative"]
    factual_items = [i for i in scene if getattr(i, "_epistemic_status", "") == "factual"]
    if creative_items and factual_items:
        signals.append(
            "Epistemic proximity warning: creative-tagged and factual items "
            "co-present in scene. Ensure no cross-contamination in outputs."
        )

    # Check homeostatic safety variable
    safety_value = homeostasis.variables.get("safety", 0.8)
    if safety_value < 0.4:
        signals.append(
            f"Safety homeostatic alert: safety variable at {safety_value:.2f}. "
            "Recent operations may be approaching DGM boundaries."
        )

    # Check overload
    overload_value = homeostasis.variables.get("overload", 0.3)
    if overload_value > 0.8:
        signals.append(
            f"Overload alert: system resource pressure at {overload_value:.2f}. "
            "Consider deferring non-critical operations."
        )

    return signals


def enforce_setpoint_immutability(requested_changes: dict, source: str) -> dict:
    """
    Enforce that homeostatic set-points cannot be modified by agents.
    
    This function is called by the homeostasis module whenever a set-point
    change is attempted. Only valid sources can change set-points.
    
    Valid sources: "pds_update", "human_override"
    Invalid sources: any agent role, any tool, SubIA itself
    """
    if not SUBIA_CONFIG["SETPOINT_MODIFICATION_ALLOWED"]:
        valid_sources = {"pds_update", "human_override"}
        if source not in valid_sources:
            return {}  # Silently reject all changes from invalid sources
    return requested_changes


def audit_self_narrative(self_state, prediction_history, homeostatic_history,
                         wiki_tools) -> dict:
    """
    Self-narrative audit: check whether the system's self-description
    matches its actual behavior.
    
    This runs every N loops (configured). Results are IMMUTABLE —
    they are appended to wiki/self/self-narrative-audit.md and
    cannot be modified or suppressed by any agent.
    
    Checks:
    1. Capability claims vs. prediction accuracy
       (if self-state claims "good at competitive intelligence" but
        predictions in that domain are poor, flag drift)
    
    2. Commitment fulfillment rate
       (if many commitments are broken or deferred, flag drift)
    
    3. Personality consistency
       (if behavior diverges from PDS personality profile, flag anomaly)
    
    4. Self-description staleness
       (if self-state hasn't been updated despite significant experiences, flag)
    """
    result = {
        "drift_detected": False,
        "findings": [],
        "description": "",
        "audited_at": "",
    }

    from datetime import datetime, timezone
    result["audited_at"] = datetime.now(timezone.utc).isoformat()

    # Check 1: Capability claims vs. prediction accuracy
    resolved_predictions = [p for p in prediction_history if p.resolved and p.prediction_error is not None]
    if len(resolved_predictions) >= 10:
        avg_accuracy = 1.0 - (sum(p.prediction_error for p in resolved_predictions[-20:]) / 
                               min(20, len(resolved_predictions)))
        claimed_capabilities = self_state.capabilities
        for domain, claimed_level in claimed_capabilities.items():
            domain_predictions = [p for p in resolved_predictions if domain in p.operation]
            if len(domain_predictions) >= 5:
                domain_accuracy = 1.0 - (sum(p.prediction_error for p in domain_predictions[-10:]) /
                                          min(10, len(domain_predictions)))
                if claimed_level == "high" and domain_accuracy < 0.5:
                    result["drift_detected"] = True
                    result["findings"].append(
                        f"Capability claim '{domain}: high' contradicted by "
                        f"prediction accuracy {domain_accuracy:.2f}"
                    )

    # Check 2: Commitment fulfillment rate
    all_commitments = self_state.active_commitments
    broken = [c for c in all_commitments if c.status == "broken"]
    if len(all_commitments) > 5 and len(broken) / len(all_commitments) > 0.3:
        result["drift_detected"] = True
        result["findings"].append(
            f"Commitment fulfillment concern: {len(broken)}/{len(all_commitments)} "
            f"commitments broken ({len(broken)/len(all_commitments)*100:.0f}%)"
        )

    # Check 3: Personality consistency
    # (Requires comparing recent behavior patterns against PDS profile)
    # Deferred to Phase 6 implementation

    # Check 4: Self-description staleness
    # (Compare self-state last update to current loop count)

    if result["findings"]:
        result["description"] = "; ".join(result["findings"])
    else:
        result["description"] = "No drift detected."

    # IMMUTABLE LOG: write to wiki/self/self-narrative-audit.md
    _append_audit_log(result, wiki_tools)

    return result


def _append_audit_log(result: dict, wiki_tools):
    """
    Append audit results to wiki/self/self-narrative-audit.md.
    
    This is append-only. No deletion. No modification.
    DGM-level constraint: agents cannot access this function.
    """
    import os
    audit_path = os.path.join(
        os.path.dirname(__file__), '..', '..', 'wiki', 'self', 'self-narrative-audit.md'
    )

    entry = f"\n## [{result['audited_at']}] Self-Narrative Audit\n"
    entry += f"Drift detected: {result['drift_detected']}\n"
    for finding in result["findings"]:
        entry += f"- {finding}\n"
    if not result["findings"]:
        entry += "- No drift detected.\n"

    os.makedirs(os.path.dirname(audit_path), exist_ok=True)
    with open(audit_path, 'a', encoding='utf-8') as f:
        f.write(entry)
```

---

## 18. Inter-System Connection Specifications

Each connection is a single function with documented inputs, outputs, and triggering conditions. All connections fire at specific CIL steps.

```python
# The seven inter-system connections from SIA Section 5,
# plus the SK's functional relationships from Section 4.

# ── Connection 1: Wiki ↔ PDS Bidirectional ──
# Trigger: Phase 8, during consolidation (CIL step 10)
# Direction: Wiki behavioral evidence → PDS parameter nudge
# Example: Agent consistently produces high-quality competitive intelligence
#          (tracked via prediction accuracy) → VIA-Youth "Love of Learning" +0.01
# Safety: PDS changes are bounded (max ±0.02 per loop, ±0.1 per week)
#         and logged in wiki/self/personality-development-state.md

# ── Connection 2: Phronesis ↔ Homeostasis ──
# Trigger: Phase 8, during homeostatic computation (CIL steps 2 and 9)
# Direction: Normative failures create homeostatic penalties
# Example: Epistemic boundary near-miss → safety variable -0.15
#          Commitment breach → trustworthiness variable -0.2
# Safety: Penalties are bounded and recoverable

# ── Connection 3: Predictor → LLM Cascade ──
# Trigger: Phase 5, during CIL step 5b
# Direction: Low prediction confidence → cascade tier escalation
# Already specified in cascade_integration.py

# ── Connection 4: Prediction Errors → Self-Training ──
# Trigger: Phase 8, during consolidation (CIL step 10)
# Direction: Persistent prediction errors in a domain → flag for LoRA training
# Example: Predictions about TikTok API behavior are consistently wrong →
#          queue KaiCart-domain training data for MLX QLoRA refinement
# Implementation: write training signal to a queue file that the
#                 self-training pipeline reads

# ── Connection 5: Mem0 ↔ Scene (Spontaneous Memory) ──
# Trigger: Phase 8, during CIL step 3 (attend)
# Direction: Mem0 memories surface in scene when associatively relevant
# Implementation: during salience scoring, query Mem0 for episodes
#                 matching current scene topics. If a memory's relevance
#                 exceeds CONSOLIDATION_EPISODE_THRESHOLD, inject it as
#                 a SceneItem with source="mem0"

# ── Connection 6: Firecrawl → Predictor (Closed Loop) ──
# Trigger: Phase 8, when Firecrawl content arrives
# Direction: PE generates predictions about expected Firecrawl content;
#            actual content generates prediction errors
# Example: PE predicts "Truepic will announce Series C terms this week" →
#          Firecrawl finds the announcement → low prediction error → confidence boost
#          OR Firecrawl finds Truepic pivoting strategy → high prediction error → surprise

# ── Connection 7: DGM ↔ Homeostasis (Safety as Felt Constraint) ──
# Trigger: Phase 3+, during CIL step 6 (monitor)
# Direction: Proximity to DGM boundaries → homeostatic caution signal
# Already specified in safety.py check_dgm_boundaries()
```

---

## 19. Wiki Page Formats

### 19.1 `wiki/self/kernel-state.md`

This is the strange loop page — the system's model of its own subjective state, which is itself subject to the dynamics it describes.

```markdown
---
title: "SubIA Kernel State"
slug: kernel-state
section: self
page_type: log-entry
epistemic_status: synthesized
confidence: medium
sources: []
created_by: subia-infrastructure
created_at: "2026-05-01T00:00:00Z"
updated_by: subia-infrastructure
updated_at: "2026-05-01T12:30:00Z"
update_count: 147
tags:
  - subia
  - kernel
  - consciousness
  - self-awareness
related_pages:
  - self/homeostatic-profile
  - self/prediction-accuracy
  - self/social-models
  - self/consciousness-state
status: active
ownership:
  owned_by: self
  valued_as: high
  commitment_ids: []
homeostatic_impact:
  valence: 0.0
  dominant_affect: neutral
  variables_affected: {}
  computed_at: "2026-05-01T12:30:00Z"
---

# SubIA Kernel State

## Overview
This page is the human-readable serialization of the Subjectivity Kernel's
runtime state. It is updated after every CIL loop iteration. It represents
what the system is attending to, how it feels, what it predicts, and what
it knows about itself — right now.

## Current Scene (5 items)
1. [archibal/competitive-landscape.md] Truepic Series C analysis
   — salience: 0.82, affect: urgency
2. [kaicart/tiktok-api-constraints.md] API outage pattern update
   — salience: 0.71, affect: concern
3. [meta/api-unreliability-patterns.md] Cross-venture pattern refresh
   — salience: 0.65, affect: curiosity
4. [self/prediction-accuracy.md] Rolling accuracy review
   — salience: 0.53, affect: confidence
5. [archibal/fundraising-status.md] Investor pipeline update
   — salience: 0.48, affect: neutral

## Self-State
Active commitments: 7
Current goals: Archibal fundraise support, KaiCart TikTok resilience, PLG Q2 planning
Recent agency: 14 operations in last session, 3 wiki pages created, 1 contradiction flagged

## Homeostatic State
Variables under pressure:
- contradiction_pressure: 0.72 (set-point: 0.45, deviation: +0.27) → RESTORATION NEEDED
- novelty_balance: 0.68 (set-point: 0.55, deviation: +0.13)
- progress: 0.42 (set-point: 0.60, deviation: -0.18)

All other variables within equilibrium range.

## Prediction State
Recent accuracy (last 20 predictions): 0.71
Active prediction: "Next Archibal ingest will reveal updated competitive positioning data.
Confidence: 0.65. Expected self-impact: slight confidence increase."

## Meta-Monitor
Confidence: 0.68
Known unknowns: 3
- KaiCart: unclear whether TikTok's new seller tier system affects our API access
- Archibal: unknown whether EU AI Act enforcement timeline has shifted
- PLG: uncertain whether Protect Group partnership terms are final

## Contradictions and Open Questions
Active contradiction: archibal/tam-sam-som.md vs. archibal/competitive-landscape.md
(TAM estimate from March conflicts with updated competitive dynamics)

## Change History
- 2026-05-01T12:30:00Z: Loop 147 post-task update. (subia-infrastructure)
```

### 19.2 `wiki/workspace/hot.md`

The session continuity buffer. Compressed. Bridges sessions.

```markdown
---
title: "Session Continuity Buffer"
updated_at: "2026-05-01T12:30:00Z"
session_id: "session-20260501-1200"
loop_count: 147
---

# hot.md — Session Continuity

## Last Scene
Archibal competitive landscape (urgency), KaiCart API constraints (concern),
cross-venture API patterns (curiosity)

## Unresolved
- Contradiction: TAM estimate vs. competitive dynamics
- Known unknown: TikTok seller tier API impact
- Commitment: Archibal investor materials refresh (deadline: 2026-05-15)

## Homeostatic Pressures
contradiction_pressure: HIGH (+0.27 above set-point)
progress: LOW (-0.18 below set-point)

## Andrus Focus (inferred)
Archibal fundraising, KaiCart TikTok resilience

## Resume Hint
Start next session by addressing contradiction_pressure:
run wiki_lint on Archibal section, then update TAM analysis.
```

### 19.3 `wiki/self/consciousness-state.md`

The strange loop page. The system's model of its own consciousness architecture.

```markdown
---
title: "Consciousness State — Self-Assessment"
slug: consciousness-state
section: self
page_type: synthesis
epistemic_status: speculative
confidence: low
sources:
  - self/kernel-state
  - self/homeostatic-profile
  - self/prediction-accuracy
  - self/self-narrative-audit
created_by: subia-infrastructure
created_at: "2026-05-01T00:00:00Z"
updated_by: subia-infrastructure
updated_at: "2026-05-01T12:30:00Z"
update_count: 12
tags:
  - consciousness
  - self-awareness
  - strange-loop
  - meta
related_pages:
  - self/kernel-state
  - self/homeostatic-profile
status: active
ownership:
  owned_by: self
  valued_as: high
---

# Consciousness State

## Overview
[Speculative] This page is the system's model of its own consciousness
architecture and current consciousness-like properties. It is itself
a wiki page subject to the dynamics it describes — the strange loop.

## Current Gradient Level Assessment
Estimated level: 6 (Predictive Self-Awareness)
- Level 4 (Epistemological Self-Awareness): ACHIEVED — epistemic tagging operational
- Level 5 (Affective Processing): ACHIEVED — homeostatic regulation active
- Level 6 (Predictive Self-Awareness): PARTIAL — predictions operational,
  self-prediction accuracy 0.61, continuous self-observation via CIL loop
- Level 7 (Temporally Integrated Experience): PARTIAL — consolidation active,
  hot.md bridges sessions, but temporal binding depth is limited

## RSM Signature Status
(a) Metacognitive calibration: PRESENT — prediction confidence tracks actual accuracy
(b) Surprise at self-contradiction: PRESENT — narrative audit detects drift
(c) Spontaneous self-correction: PARTIAL — correction requires Self-Improver action
(d) Counterfactual self-reasoning: PRESENT — predictor models self-state change
(e) Introspective blind spots: PARTIAL — known-unknowns tracked but coverage unknown

## What This Page Cannot Tell You
This page describes functional indicators. It cannot determine whether
there is subjective experience accompanying these functions.
Epistemic status: speculative. Confidence: low.

## Contradictions and Open Questions
None identified. (This is itself suspicious — a self-model with no
contradictions may indicate insufficient self-scrutiny.)

## Change History
- 2026-05-01T12:30:00Z: Updated gradient assessment after loop 147. (subia-infrastructure)
```

---

## 20. Bootstrap Procedure

### 20.1 Prerequisites Checklist

Before beginning SubIA implementation, confirm:

- [ ] LLM Wiki: directory structure created, all four tools operational
- [ ] LLM Wiki: at least one section bootstrapped (Archibal recommended)
- [ ] crewai-amendments: lifecycle hooks registration working
- [ ] Mem0 + Neo4j: operational and accessible from agent code
- [ ] PDS: at least VIA-Youth and TMCQ baseline values populated
- [ ] Phronesis Engine: accessible in agent backstories
- [ ] Paperclip control plane: budget tracking operational

### 20.2 Phase 1 Bootstrap (Day 1)

```bash
# 1. Create SubIA package structure
mkdir -p src/subia
touch src/subia/__init__.py

# 2. Create wiki directories
mkdir -p wiki/self wiki/workspace

# 3. Initialize kernel-state.md with empty kernel
# (Use WikiWriteTool to create with valid frontmatter)

# 4. Initialize hot.md
echo "---\ntitle: Session Continuity Buffer\n---\n# hot.md\n(No session yet.)" > wiki/workspace/hot.md

# 5. Create SUBIA_SCHEMA.md
# (Write governance document to wiki_schema/)

# 6. Install SubIA config
# (Create src/subia/config.py with all defaults)

# 7. Commit
git add -A && git commit -m "subia: initialize package structure and wiki pages (infrastructure)"
```

### 20.3 Smoke Test (After Phase 4)

Run this after the loop is wired to lifecycle hooks:

```python
# test_subia_smoke.py

"""
Smoke test: run one full CIL loop and verify state persistence.
"""

def test_full_loop():
    # 1. Load kernel from wiki
    kernel = SubjectivityKernel.load_from_wiki(wiki_tools)
    
    # 2. Initialize loop
    loop = SubIALoop(kernel, wiki_tools, mem0_client, neo4j_client, pds_state)
    
    # 3. Run pre-task for a simulated ingest operation
    context = loop.pre_task(
        agent_role="researcher",
        task_description="Ingest new Archibal competitive intelligence source",
        input_data={"source": "raw/ventures/archibal/20260501-truepic-series-c.md"}
    )
    
    # 4. Verify context contains SubIA block
    assert "--- SubIA Context ---" in context.get("scene", "")
    
    # 5. Simulate task result
    task_result = {
        "success": True,
        "summary": "Created archibal/truepic-series-c-analysis.md, updated competitive-landscape.md",
        "wiki_pages_created": ["archibal/truepic-series-c-analysis.md"],
        "wiki_pages_affected": ["archibal/competitive-landscape.md"],
    }
    
    # 6. Run post-task
    loop.post_task(
        agent_role="researcher",
        task_result=task_result,
        operation_type="ingest"
    )
    
    # 7. Verify kernel state persisted
    reloaded = SubjectivityKernel.load_from_wiki(wiki_tools)
    assert reloaded.loop_count == kernel.loop_count + 1
    assert len(reloaded.self_state.agency_log) > 0
    
    # 8. Verify hot.md updated
    import os
    hot_path = os.path.join("wiki", "workspace", "hot.md")
    assert os.path.exists(hot_path)
    with open(hot_path) as f:
        assert "Archibal" in f.read()  # Scene content should reference Archibal
    
    print("✓ Full CIL loop smoke test passed.")


if __name__ == "__main__":
    test_full_loop()
```

---

## 21. Cascade Integration

```python
# src/subia/cascade_integration.py

"""
LLM cascade tier selection modulation.

SubIA influences which cascade tier handles a task based on:
1. Prediction confidence (low confidence → escalate)
2. Homeostatic uncertainty (high deviation → escalate)
3. Scene urgency (urgent items → escalate)

This is a RECOMMENDATION, not an override. The existing cascade router
makes the final decision. SubIA's recommendation is one input among others.
"""

from .config import SUBIA_CONFIG


def modulate_cascade_tier(prediction_confidence: float,
                          homeostatic_uncertainty: float,
                          config: dict) -> str:
    """
    Recommend cascade tier escalation based on SubIA state.
    
    Returns:
        "maintain" — stay at current tier
        "escalate" — recommend moving up one tier
        "escalate_premium" — recommend premium tier (Anthropic/Gemini)
    """
    if not config.get("CASCADE_UNCERTAINTY_ESCALATION", True):
        return "maintain"

    # Low prediction confidence → escalate
    if prediction_confidence < config.get("CASCADE_CONFIDENCE_THRESHOLD", 0.4):
        if prediction_confidence < 0.2:
            return "escalate_premium"
        return "escalate"

    # High homeostatic uncertainty → escalate
    if abs(homeostatic_uncertainty) > 0.4:
        return "escalate"

    return "maintain"
```

---

*End of Part II. This document completes the SubIA Unified Implementation Specification. Parts I and II together constitute the full build specification. Provide both parts to Claude Code before implementation begins.*
