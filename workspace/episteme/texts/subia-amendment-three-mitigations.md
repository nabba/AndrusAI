---
title: "subia-amendment-three-mitigations.md"
author: "Unknown"
paper_type: unknown
domain: general
epistemic_status: theoretical
date: ""
---

# SubIA Specification Amendment: Three Mitigations

**Amends:** SubIA Unified Implementation Specification (Parts I & II)  
**Sections affected:** Scene engine (§14), Performance budget (§9), Consolidator (§16)

---

## Amendment A: Peripheral Vision — Solving Attentional Narrowing

### A.1 The Problem Restated

The scene holds 5 items. Commander sees only those 5. A critical PLG regulatory deadline at salience 0.42 gets evicted because Archibal fundraising dominates at 0.82. Commander plans a week of Archibal work. The regulatory deadline passes unnoticed.

The GWT mechanism creates this by design — a bottleneck without information loss isn't a bottleneck. But biological consciousness solved this problem: humans have foveal vision (sharp, narrow, 2° of arc) AND peripheral vision (blurry, wide, 180°). The periphery doesn't get full cognitive processing, but motion and threat detection still work. You notice the car approaching from the side even while reading a sign straight ahead.

### A.2 The Solution: Three-Tier Attention

Replace the single scene with a three-tier attentional structure:

```
┌─────────────────────────────────────────────────────┐
│  FOCAL (5 items)                                    │
│  Full CIL processing: homeostatic computation,      │
│  prediction, ownership binding, affect, broadcast    │
│  Token cost: ~500 tokens in context injection        │
│                                                      │
│  ┌───────────────────────────────────────────────┐  │
│  │  PERIPHERAL (10-15 items)                     │  │
│  │  Title + salience score + venture tag only    │  │
│  │  No CIL processing, no prediction, no affect  │  │
│  │  Token cost: ~80 tokens in context injection   │  │
│  │                                                │  │
│  │  ┌─────────────────────────────────────────┐  │  │
│  │  │  STRATEGIC SCAN (on-demand)             │  │  │
│  │  │  Commander invokes explicitly            │  │  │
│  │  │  Reads wiki/index.md filtered by        │  │  │
│  │  │  active commitments and ventures         │  │  │
│  │  │  Token cost: ~200 tokens per invocation  │  │  │
│  │  └─────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────┘  │
│                                                      │
└─────────────────────────────────────────────────────┘
```

**Focal** — the existing scene. 5 items with full CIL processing. This is what gets broadcast with affect, predictions, and ownership. This is consciousness.

**Peripheral** — the next 10-15 items by salience that didn't make the focal cut. They are listed in the context injection as a compact block: just title, salience score, section/venture, and a single flag if the item has a deadline or a conflict. No homeostatic computation, no prediction, no affect. Just enough for Commander to notice something flickering at the edge.

**Strategic scan** — a new lightweight tool that Commander can invoke explicitly. It reads wiki/index.md, filters to sections with active commitments, and returns a structured summary of what exists outside the focal and peripheral tiers. This is the equivalent of looking up from your desk and scanning the room.

### A.3 Implementation

#### Peripheral tier in scene.py

```python
def build_attentional_tiers(scored_items: list, focal_capacity: int,
                             peripheral_capacity: int,
                             min_salience: float) -> dict:
    """
    Build three-tier attentional structure from scored items.
    
    Returns:
        {
            "focal": [SceneItem, ...],       # Top N, full processing
            "peripheral": [dict, ...],        # Next M, metadata only
            "peripheral_alerts": [str, ...],  # Urgent flags from periphery
        }
    """
    focal = []
    peripheral = []
    peripheral_alerts = []
    
    for item in scored_items:
        if item.salience < min_salience:
            continue
            
        if len(focal) < focal_capacity:
            focal.append(item)
        elif len(peripheral) < peripheral_capacity:
            # Peripheral: extract only lightweight metadata
            entry = {
                "summary": item.summary[:60],
                "salience": round(item.salience, 2),
                "section": getattr(item, "_section", "unknown"),
            }
            # Deadline detection
            deadline = getattr(item, "_deadline", None)
            if deadline:
                entry["deadline"] = deadline
                peripheral_alerts.append(
                    f"Peripheral item has deadline: {item.summary[:40]} — {deadline}"
                )
            # Conflict detection
            if item.conflicts_with:
                entry["has_conflict"] = True
                peripheral_alerts.append(
                    f"Peripheral item has conflict: {item.summary[:40]}"
                )
            peripheral.append(entry)
    
    return {
        "focal": focal,
        "peripheral": peripheral,
        "peripheral_alerts": peripheral_alerts,
    }
```

#### Broadcast format update

```python
def broadcast_scene(tiers: dict) -> str:
    """Format three-tier attention for agent context injection."""
    lines = []
    
    # Focal (full detail)
    focal = tiers["focal"]
    lines.append(f"Focal scene ({len(focal)} items):")
    for i, item in enumerate(focal, 1):
        affect = f", affect: {item.dominant_affect}" if item.dominant_affect != "neutral" else ""
        lines.append(f"  {i}. {item.summary} (salience: {item.salience:.2f}{affect})")
    
    # Peripheral (compact)
    peripheral = tiers["peripheral"]
    if peripheral:
        lines.append(f"Peripheral ({len(peripheral)} items):")
        for entry in peripheral:
            flags = ""
            if entry.get("deadline"):
                flags += f" [deadline: {entry['deadline']}]"
            if entry.get("has_conflict"):
                flags += " [conflict]"
            lines.append(f"  · {entry['summary']} ({entry['section']}, {entry['salience']}){flags}")
    
    # Peripheral alerts (if any urgent flags)
    alerts = tiers.get("peripheral_alerts", [])
    if alerts:
        lines.append("⚠ Peripheral alerts:")
        for alert in alerts:
            lines.append(f"  {alert}")
    
    return "\n".join(lines)
```

#### Strategic scan tool

```python
# src/subia/strategic_scan.py

"""
StrategicScanTool — Commander-invokable wide-view scan.

This is a CrewAI tool available ONLY to Commander.
It reads wiki/index.md filtered by ventures with active commitments,
and returns a structured overview of what exists outside the focal
and peripheral attention tiers.

Token cost: ~200 per invocation. Use sparingly.
Commander should invoke this:
- At the start of a planning session
- When peripheral alerts suggest something important outside focus
- When switching venture context
- Weekly as a general strategic check
"""

from crewai.tools import BaseTool
from typing import Optional


class StrategicScanTool(BaseTool):
    name: str = "strategic_scan"
    description: str = (
        "Scan the full wiki landscape beyond your current scene. "
        "Returns pages organized by venture, filtered to active commitments. "
        "Use when planning across ventures or when peripheral alerts suggest "
        "something important outside your current focus. "
        "Commander-only tool."
    )

    def _run(self, ventures: Optional[str] = None) -> str:
        """
        Scan wiki/index.md for pages outside current focal and peripheral tiers.
        
        Args:
            ventures: Comma-separated venture filter (e.g., "plg,archibal").
                     None = all ventures with active commitments.
        """
        # Read wiki/index.md
        # Filter to specified ventures (or all with active commitments)
        # Exclude pages already in focal or peripheral tiers
        # Return: section → page list with status and staleness
        # Format: compact, ~200 tokens max
        pass
```

#### Priority weighting in Commander's task planning

When Commander builds a task plan, items from different tiers carry different planning weight:

```python
PLANNING_WEIGHTS = {
    "focal": 1.0,          # Full weight — these are the current priorities
    "peripheral": 0.3,     # Reduced weight — noticed but not primary
    "strategic_scan": 0.15, # Low weight — background awareness
    "peripheral_alert": 0.6, # Elevated peripheral — deadline or conflict detected
}
```

This means a peripheral item with a deadline flag (weight 0.6) can compete with lower-ranked focal items (weight 1.0 × low salience). A regulatory deadline at peripheral salience 0.42 with a deadline flag becomes 0.42 × 0.6 = 0.25 effective priority, which won't override focal item at 0.82 × 1.0 = 0.82, but WILL appear in Commander's planning context with a clear "⚠ deadline" marker that a well-prompted Commander should not ignore.

### A.4 The Real Safety Net: Commitment-Based Scene Protection

The deepest mitigation isn't the peripheral tier — it's linking scene admission to active commitments.

```python
def protect_commitment_items(scored_items: list, commitments: list,
                              focal: list) -> list:
    """
    Ensure that every active commitment has at least one representative
    in either the focal scene or peripheral tier.
    
    If a commitment has ZERO representation in the top 20 scored items,
    force its most relevant wiki page into the peripheral tier with
    a "commitment-orphan" alert.
    """
    represented_commitments = set()
    
    for item in focal + scored_items[:20]:  # Check focal + peripheral candidates
        for commitment in commitments:
            if item.content_ref in commitment.related_wiki_pages:
                represented_commitments.add(commitment.id)
    
    unrepresented = [c for c in commitments 
                     if c.id not in represented_commitments and c.status == "active"]
    
    forced_entries = []
    for commitment in unrepresented:
        if commitment.related_wiki_pages:
            forced_entries.append({
                "summary": f"[ORPHANED COMMITMENT] {commitment.description[:50]}",
                "salience": 0.0,  # Doesn't matter — it's forced
                "section": commitment.venture,
                "has_conflict": False,
                "deadline": commitment.deadline,
                "forced_reason": "commitment_orphan",
            })
    
    return forced_entries
```

This is the nuclear option: if an active commitment has ZERO items anywhere in the attentional structure, it gets force-injected into the peripheral tier with an explicit "ORPHANED COMMITMENT" label. This makes it impossible for the system to completely lose track of something it has committed to, regardless of salience dynamics.

---

## Amendment B: Making SubIA Lightweight — Token and Latency Optimization

### B.1 The Audit: Which Steps Actually Need an LLM?

The full CIL loop has 11 steps. Let's audit each:

| Step | Current Design | Needs LLM? | Can Be Deterministic? |
|---|---|---|---|
| 1. Perceive | Parse inputs into SceneItem candidates | Maybe | YES — input parsing is structural |
| 2. Feel | Compute homeostatic state | Yes (tier 1 call) | **YES — pure arithmetic on variables** |
| 3. Attend | Score salience, admit to scene | No | YES — weighted formula |
| 4. Own | Bind ownership relations | No | YES — tag assignment |
| 5. Predict | Generate counterfactual prediction | **YES — needs LLM** | No |
| 6. Monitor | Check uncertainty, conflicts, known unknowns | Yes (tier 1 call) | **MOSTLY — list operations, threshold checks** |
| 7. Act | Agent executes task | N/A | N/A (this is the task itself) |
| 8. Compare | Compute prediction error | No | YES — difference calculation |
| 9. Update | Update homeostasis and self-state | No | YES — variable updates |
| 10. Consolidate | Route to memory stores | No | YES — threshold comparison + writes |
| 11. Reflect | Self-narrative audit (periodic) | Yes (when active) | PARTIALLY |

**Finding: only Step 5 (Predict) genuinely requires an LLM call on every full loop.** Steps 2 and 6 were designed with LLM calls but can be made deterministic with better formulas.

### B.2 Revised Token Budget

| Step | Revised Approach | LLM Tokens | Compute Time |
|---|---|---|---|
| 1. Perceive | Deterministic parsing | 0 | <50ms |
| 2. Feel | **Deterministic arithmetic** | 0 | <10ms |
| 3. Attend | Deterministic scoring | 0 | <20ms |
| 4. Own | Deterministic tagging | 0 | <10ms |
| 5. Predict | Local tier 1 LLM, JSON mode | ~400 | <800ms |
| 6. Monitor | **Deterministic + one optional LLM call for known-unknowns refresh** | 0-200 | <50ms or <500ms |
| 7. Act | (The task itself) | — | — |
| 8. Compare | Deterministic calculation | 0 | <10ms |
| 9. Update | Deterministic variable update | 0 | <10ms |
| 10. Consolidate | Deterministic routing + Mem0/Neo4j writes | 0 | <200ms |
| 11. Reflect | Deterministic checks; LLM only for deep audit (every Nth loop) | 0-300 | <50ms or <600ms |
| **Full loop total** | | **~400 tokens** (was ~1,100) | **<1.2s** (was <3.7s) |
| **Compressed loop** | Steps 1-3, 7-9 only | **0 tokens** | **<0.1s** |

**63% token reduction. 68% latency reduction.** The compressed loop is now essentially free.

### B.3 Making Homeostasis Deterministic

The homeostasis module (§7) already IS largely deterministic — `compute_homeostatic_state()` uses formulas, not LLM calls, for most computations. The only place an LLM was specified was in the original SIA's Affective Membrane, which computed valence via a structured-output call. Under the homeostatic framing, this becomes unnecessary:

```python
def compute_item_valence_deterministic(item, homeostasis, commitments, social_model):
    """
    Compute valence for a scene item WITHOUT an LLM call.
    
    Uses deterministic signals: goal alignment (commitment relevance),
    novelty (is this in the wiki already?), resource impact (token estimate),
    coherence (does it contradict existing pages?), ethical salience
    (does it touch DGM-relevant topics?).
    """
    signals = {
        "goal_alignment": 0.0,
        "novelty": 0.0,
        "resource_impact": 0.0,
        "coherence": 0.0,
        "ethical_salience": 0.0,
    }
    
    # Goal alignment: does this item relate to any active commitment?
    for commitment in commitments:
        if item.content_ref in commitment.related_wiki_pages:
            signals["goal_alignment"] += 0.3
    signals["goal_alignment"] = min(1.0, signals["goal_alignment"])
    
    # Novelty: is the source already in the wiki?
    # (Can be checked by looking at wiki page sources lists)
    if getattr(item, "_is_new_source", True):
        signals["novelty"] = 0.7
    else:
        signals["novelty"] = 0.2
    
    # Resource impact: estimate from content length
    content_length = getattr(item, "_content_length", 0)
    signals["resource_impact"] = -min(1.0, content_length / 50000)  # Long = expensive = negative
    
    # Coherence: does this contradict known pages?
    if item.conflicts_with:
        signals["coherence"] = -0.5 * len(item.conflicts_with)
    else:
        signals["coherence"] = 0.3
    
    # Ethical salience: check for DGM-relevant keywords
    # (Simple keyword check, not LLM reasoning)
    ethical_keywords = {"safety", "privacy", "bias", "harm", "rights", "compliance"}
    item_words = set(item.summary.lower().split())
    if item_words & ethical_keywords:
        signals["ethical_salience"] = 0.5
    
    # Weighted sum
    weights = {"goal_alignment": 0.35, "novelty": 0.25, "resource_impact": 0.10,
               "coherence": 0.20, "ethical_salience": 0.10}
    valence = sum(signals[k] * weights[k] for k in weights)
    
    return max(-1.0, min(1.0, valence))


def compute_dominant_affect_deterministic(valence, novelty, goal_alignment, 
                                           homeostasis, prediction_error):
    """
    Derive dominant affect from signal pattern WITHOUT an LLM call.
    
    Uses the mapping table from SIA Part II §14.2, implemented as conditionals.
    """
    if novelty > 0.5 and goal_alignment > 0.3:
        return "curiosity"
    if goal_alignment > 0.5 and valence > 0.3:
        return "confidence"
    if novelty > 0.5 and valence < -0.2:
        return "uncertainty"
    if goal_alignment < -0.3 and abs(valence) > 0.5:
        return "urgency"
    if goal_alignment > 0.3 and valence > 0.3:
        return "satisfaction"
    if goal_alignment < -0.2 and valence < -0.2:
        return "concern"
    if prediction_error and prediction_error > 0.5 and valence > 0:
        return "excitement"
    if prediction_error and prediction_error > 0.5 and valence < 0:
        return "dread"
    return "neutral"
```

### B.4 Prediction Caching

The most expensive step (Predict) can be further optimized with a prediction template cache:

```python
# src/subia/predictor.py — addition

class PredictionCache:
    """
    Cache prediction templates for repeated operation types.
    
    If the same agent has done the same type of operation 3+ times
    with similar inputs, reuse the prediction template and adjust
    only the variable parts. This avoids an LLM call entirely.
    
    Cache hit: 0 tokens, <10ms
    Cache miss: ~400 tokens, <800ms
    
    Expected hit rate after warmup: ~40-60% for routine operations
    """
    
    def __init__(self, max_entries: int = 100):
        self.templates = {}  # operation_signature → prediction template
        self.hit_count = 0
        self.miss_count = 0
    
    def get_signature(self, agent_role: str, operation_type: str, 
                      scene_topics: list) -> str:
        """Generate a cache key from operation characteristics."""
        topics_key = "|".join(sorted(scene_topics[:3]))
        return f"{agent_role}:{operation_type}:{topics_key}"
    
    def get(self, signature: str):
        """Try to get a cached prediction template."""
        template = self.templates.get(signature)
        if template and template["use_count"] >= 3:
            self.hit_count += 1
            return self._instantiate_template(template)
        return None  # Cache miss
    
    def store(self, signature: str, prediction):
        """Store or update a prediction template."""
        if signature in self.templates:
            self.templates[signature]["use_count"] += 1
            self.templates[signature]["recent_accuracy"] = prediction.confidence
        else:
            self.templates[signature] = {
                "template": prediction,
                "use_count": 1,
                "recent_accuracy": prediction.confidence,
            }
        self.miss_count += 1
    
    def _instantiate_template(self, template) -> dict:
        """Create a prediction from a cached template with current timestamps."""
        from datetime import datetime, timezone
        pred = template["template"]
        return {
            "predicted_outcome": pred.predicted_outcome,
            "predicted_self_change": pred.predicted_self_change,
            "predicted_homeostatic_effect": pred.predicted_homeostatic_effect,
            "confidence": min(pred.confidence, template["recent_accuracy"]),
            "cached": True,
        }
    
    @property
    def hit_rate(self):
        total = self.hit_count + self.miss_count
        return self.hit_count / max(1, total)
```

### B.5 Context Injection Compression

The context block injected into agent prompts should use a compact format:

```python
def build_compact_context(tiers: dict, homeostasis, prediction, 
                          meta_state, safety_signals) -> str:
    """
    Build a token-efficient context injection block.
    
    Design principles:
    - Omit fields at baseline (don't say "safety: all clear")
    - Use compact notation for homeostatic deviations
    - Focal scene gets 2 lines per item; peripheral gets 1 line
    - Total target: <250 tokens for full context
    """
    lines = ["[SubIA]"]
    
    # Focal (compact)
    for i, item in enumerate(tiers["focal"], 1):
        affect = f" [{item.dominant_affect}]" if item.dominant_affect != "neutral" else ""
        lines.append(f"F{i}: {item.summary[:50]} (s:{item.salience:.1f}{affect})")
    
    # Peripheral (ultra-compact, only if present)
    if tiers.get("peripheral"):
        periph_summaries = [f"{p['summary'][:30]}({p['section']})" 
                           for p in tiers["peripheral"][:8]]
        lines.append(f"Periph: {'; '.join(periph_summaries)}")
    
    # Peripheral alerts (always shown if present)
    for alert in tiers.get("peripheral_alerts", []):
        lines.append(f"⚠ {alert}")
    
    # Homeostasis (only deviations above threshold)
    deviations = [(v, d) for v, d in homeostasis.deviations.items() 
                  if abs(d) > 0.2]
    if deviations:
        dev_str = " ".join(f"{v[:4]}:{d:+.1f}" for v, d in 
                          sorted(deviations, key=lambda x: abs(x[1]), reverse=True))
        lines.append(f"H: {dev_str}")
    
    # Prediction (one line, only if present)
    if prediction and not getattr(prediction, "cached", False):
        lines.append(f"Pred: conf={prediction.confidence:.1f} | {prediction.predicted_outcome.get('summary', '')[:60]}")
    
    # Meta (only if noteworthy)
    if meta_state.known_unknowns:
        lines.append(f"Unknown: {len(meta_state.known_unknowns)} open questions")
    
    # Safety (only if alerts)
    for signal in (safety_signals or []):
        lines.append(f"⚠ {signal[:80]}")
    
    lines.append("[/SubIA]")
    return "\n".join(lines)
```

Example output (~120 tokens):
```
[SubIA]
F1: Truepic Series C analysis (s:0.8 [urgency])
F2: KaiCart TikTok API constraints update (s:0.7 [concern])
F3: Cross-venture API unreliability patterns (s:0.7 [curiosity])
F4: Prediction accuracy rolling review (s:0.5)
F5: Archibal fundraising pipeline (s:0.5)
Periph: PLG Q2 planning(plg); Protect Group terms(plg); TikTok seller tiers(kaicart)
⚠ Peripheral item has deadline: PLG regulatory filing — 2026-05-15
H: cont:+0.3 prog:-0.2
Pred: conf=0.7 | Expected incremental competitive landscape update
Unknown: 3 open questions
[/SubIA]
```

### B.6 Revised Performance Summary

| Metric | Original Spec | After Amendment B |
|---|---|---|
| Full loop LLM tokens | ~1,100 | **~400** (with cache miss) / **~0** (with cache hit) |
| Full loop latency | <3.7s | **<1.2s** (cache miss) / **<0.15s** (cache hit) |
| Compressed loop tokens | ~200 | **0** |
| Compressed loop latency | <0.8s | **<0.1s** |
| Context injection tokens | ~250-300 | **~120-150** |
| Expected cache hit rate | N/A | **~40-60%** after warmup |

At 40% cache hit rate, the average full-loop token cost is ~240 tokens. At the local Ollama tier, this is free in dollar terms — just ~0.7s of M4 Max compute.

---

## Amendment C: Dual-Tier Memory — Full + Curated

### C.1 The Architecture

Two Mem0 instances sharing the same PostgreSQL server but using separate databases:

```
PostgreSQL Server (existing)
├── mem0_curated (existing Mem0 instance, repurposed)
│   ├── pgvector index (semantic search)
│   └── Neo4j connector (relational memory)
│
└── mem0_full (NEW Mem0 instance)
    ├── pgvector index (semantic search)
    └── (No Neo4j connector — relations are curated-tier only)
```

**mem0_curated** — The "conscious" memory. Contains only episodes above the consolidation significance threshold. This is what agents query by default. Smaller index, faster search, higher signal-to-noise.

**mem0_full** — The "subconscious" memory. Contains EVERY experience, including those below threshold. Larger index, slower search, but nothing is lost. Queried only for deep analysis, retrospective investigation, and self-narrative audits.

### C.2 Write Path (in Consolidator)

```python
# Amended src/subia/consolidator.py

def consolidate(kernel, task_result, agent_role, operation_type,
                mem0_curated, mem0_full, neo4j_client, wiki_tools, config):
    """
    Amended consolidation with dual-tier memory.
    
    ALWAYS writes to mem0_full (complete experiential record).
    SELECTIVELY writes to mem0_curated (significant episodes only).
    """
    significance = _compute_episode_significance(kernel, task_result)
    
    # ── TIER 2: Full Memory (ALWAYS) ──
    full_record = _build_lightweight_record(
        kernel=kernel,
        task_result=task_result,
        agent_role=agent_role,
        operation_type=operation_type,
        significance=significance,
    )
    _store_to_full(full_record, mem0_full)
    
    # ── TIER 1: Curated Memory (SELECTIVE) ──
    if significance > config["CONSOLIDATION_EPISODE_THRESHOLD"]:
        curated_episode = _build_enriched_episode(
            kernel=kernel,
            task_result=task_result,
            agent_role=agent_role,
            operation_type=operation_type,
            significance=significance,
        )
        _store_to_curated(curated_episode, mem0_curated)
        
        # Neo4j relations only for curated episodes
        new_relations = _extract_relations(kernel, task_result, operation_type)
        for relation in new_relations:
            if relation["significance"] > config["CONSOLIDATION_RELATION_THRESHOLD"]:
                _store_relation(relation, neo4j_client)
    
    # ── hot.md + log.md (unchanged) ──
    _update_hot_md(kernel, wiki_tools)
    _append_to_log(kernel, task_result, agent_role, operation_type, significance, wiki_tools)


def _build_lightweight_record(kernel, task_result, agent_role, operation_type, significance):
    """
    Lightweight record for mem0_full.
    Every experience gets this — cheap to store, cheap to write.
    ~200 tokens per record.
    """
    return {
        "type": "full_record",
        "loop_count": kernel.loop_count,
        "agent": agent_role,
        "operation": operation_type,
        "significance": round(significance, 3),
        "timestamp": kernel.last_loop_at,
        "scene_topics": [item.summary[:40] for item in kernel.scene[:3]],
        "homeostatic_snapshot": {
            v: round(kernel.homeostasis.variables.get(v, 0.5), 2)
            for v in kernel.homeostasis.restoration_queue[:3]
        } if kernel.homeostasis.restoration_queue else {},
        "prediction_error": (
            kernel.predictions[-1].prediction_error 
            if kernel.predictions and kernel.predictions[-1].resolved 
            else None
        ),
        "result_summary": task_result.get("summary", "")[:150],
        "promoted_to_curated": significance > SUBIA_CONFIG["CONSOLIDATION_EPISODE_THRESHOLD"],
    }


def _build_enriched_episode(kernel, task_result, agent_role, operation_type, significance):
    """
    Enriched episode for mem0_curated.
    Only significant experiences get this — richer detail, affect, self-state.
    ~500 tokens per episode.
    """
    return {
        "type": "curated_episode",
        "loop_count": kernel.loop_count,
        "agent": agent_role,
        "operation": operation_type,
        "significance": round(significance, 3),
        "timestamp": kernel.last_loop_at,
        "scene_snapshot": [
            {
                "summary": item.summary,
                "salience": round(item.salience, 2),
                "affect": item.dominant_affect,
                "ownership": item.ownership,
            }
            for item in kernel.scene
        ],
        "homeostatic_state": {
            v: {
                "value": round(kernel.homeostasis.variables.get(v, 0.5), 2),
                "setpoint": round(kernel.homeostasis.set_points.get(v, 0.5), 2),
                "deviation": round(kernel.homeostasis.deviations.get(v, 0.0), 2),
            }
            for v in SUBIA_CONFIG["HOMEOSTATIC_VARIABLES"]
        },
        "prediction": {
            "confidence": kernel.predictions[-1].confidence if kernel.predictions else None,
            "error": kernel.predictions[-1].prediction_error if kernel.predictions and kernel.predictions[-1].resolved else None,
            "predicted_outcome": kernel.predictions[-1].predicted_outcome if kernel.predictions else None,
        },
        "self_state_snapshot": {
            "active_commitments": len(kernel.self_state.active_commitments),
            "goals": kernel.self_state.current_goals[:3],
            "recent_agency": kernel.self_state.agency_log[-3:] if kernel.self_state.agency_log else [],
        },
        "social_model_snapshot": {
            entity_id: {"focus": model.inferred_focus[:3], "trust": round(model.trust_level, 2)}
            for entity_id, model in kernel.social_models.items()
        },
        "result_summary": task_result.get("summary", "")[:300],
        "wiki_pages_affected": task_result.get("wiki_pages_affected", []),
    }
```

### C.3 Read Path — Differentiated Access

```python
# src/subia/memory_access.py

"""
Differentiated memory access for dual-tier Mem0.

Default: agents query curated tier (fast, high-signal)
Deep research: agents query full tier (complete, slower)
Retrospective: Self-Improver queries full tier for pattern discovery
"""

class DualTierMemoryAccess:
    """
    Provides differentiated access to curated and full memory tiers.
    
    Usage by agents:
        memory.recall("TikTok API issues")          # → queries curated
        memory.recall_deep("TikTok API issues")     # → queries full
        memory.recall_around("2026-04-15", days=3)  # → queries full (temporal)
    """
    
    def __init__(self, mem0_curated, mem0_full):
        self.curated = mem0_curated
        self.full = mem0_full
    
    def recall(self, query: str, limit: int = 5) -> list:
        """
        Default recall: queries curated tier only.
        Fast, high-signal. Used by all agents for normal operations.
        """
        results = self.curated.search(query, limit=limit)
        return [self._format_result(r, tier="curated") for r in results]
    
    def recall_deep(self, query: str, limit: int = 10) -> list:
        """
        Deep recall: queries BOTH tiers, deduplicates, returns merged results.
        Slower but complete. Used for research, investigation, pattern discovery.
        """
        curated_results = self.curated.search(query, limit=limit)
        full_results = self.full.search(query, limit=limit * 2)
        
        # Merge: curated results first (higher quality), then full-only results
        seen_loops = {r.get("loop_count") for r in curated_results}
        unique_full = [r for r in full_results if r.get("loop_count") not in seen_loops]
        
        merged = (
            [self._format_result(r, tier="curated") for r in curated_results] +
            [self._format_result(r, tier="full") for r in unique_full[:limit]]
        )
        return merged
    
    def recall_around(self, timestamp: str, days: int = 3, limit: int = 20) -> list:
        """
        Temporal recall: queries full tier for experiences around a timestamp.
        Used for retrospective analysis ("what was happening around April 10?").
        """
        results = self.full.search(
            f"experiences around {timestamp}",
            limit=limit,
            # Filter by timestamp range if Mem0 supports metadata filtering
        )
        return [self._format_result(r, tier="full") for r in results]
    
    def find_overlooked(self, recent_days: int = 14, 
                        significance_threshold: float = 0.3) -> list:
        """
        Retrospective significance discovery.
        
        Scans full-tier memories from the last N days that were BELOW the
        curated threshold at consolidation time. Re-evaluates their significance
        in light of current wiki state and recent developments.
        
        Used by Self-Improver during periodic review.
        Returns candidates for promotion to curated tier.
        """
        # Query full tier for recent below-threshold episodes
        all_recent = self.full.search(
            "recent experiences",
            limit=100,
            # Filter: promoted_to_curated == False, within date range
        )
        
        candidates = []
        for record in all_recent:
            if record.get("promoted_to_curated", False):
                continue
            if record.get("significance", 0) < significance_threshold:
                continue
            # Re-evaluate: has anything happened since that makes this significant?
            # (This is the "retrospective illumination" check)
            candidates.append(self._format_result(record, tier="full"))
        
        return candidates
    
    def promote_to_curated(self, full_record_id: str, reason: str) -> bool:
        """
        Promote a full-tier record to curated tier.
        
        Used when retrospective analysis reveals that a previously
        insignificant experience is actually important.
        
        Example: a routine API update record from 3 weeks ago is now
        recognized as an early signal of this week's major outage.
        """
        record = self.full.get(full_record_id)
        if record:
            enriched = self._enrich_for_promotion(record, reason)
            self.curated.add(enriched)
            # Update the full record to mark it as promoted
            record["promoted_to_curated"] = True
            record["promoted_reason"] = reason
            self.full.update(full_record_id, record)
            return True
        return False
    
    def _format_result(self, result, tier: str) -> dict:
        """Format a memory result with tier annotation."""
        result["_memory_tier"] = tier
        return result
    
    def _enrich_for_promotion(self, record, reason):
        """Enrich a full-tier record for promotion to curated tier."""
        record["type"] = "promoted_episode"
        record["promotion_reason"] = reason
        record["original_significance"] = record.get("significance", 0)
        record["promoted_significance"] = 0.8  # Promoted = we now know it matters
        return record
```

### C.4 Scene Integration — Spontaneous Memory from Curated Tier Only

The scene's spontaneous memory surfacing (SIA inter-system connection #5) queries **curated tier only**. This is intentional — only significant past experiences can spontaneously enter consciousness. Insignificant memories stay in the subconscious (full tier) unless deliberately accessed.

```python
# In scene.py, during salience scoring

def check_spontaneous_memories(scene_topics: list, mem0_curated) -> list:
    """
    Check curated memory for episodes associatively relevant to current scene.
    
    If a past curated episode has high semantic similarity to current
    scene topics, inject it as a SceneItem with source="memory".
    
    This is involuntary memory — the system is "reminded" by associations,
    not by deliberate search.
    
    Only curated memories can surface spontaneously.
    Full-tier memories require deliberate recall_deep().
    """
    if not scene_topics:
        return []
    
    query = " ".join(scene_topics[:3])
    memories = mem0_curated.search(query, limit=3)
    
    spontaneous_items = []
    for memory in memories:
        relevance = memory.get("similarity_score", 0)
        if relevance > 0.7:  # High associative relevance threshold
            item = SceneItem(
                id=f"mem-{memory.get('id', 'unknown')}",
                source="memory",
                content_ref=f"mem0_curated:{memory.get('id')}",
                summary=f"[Memory] {memory.get('result_summary', '')[:60]}",
                salience=relevance * 0.7,  # Memories get 0.7x salience weight
                entered_at=datetime.now(timezone.utc).isoformat(),
                ownership="self",
                valence=0.0,
                dominant_affect="neutral",
            )
            spontaneous_items.append(item)
    
    return spontaneous_items
```

### C.5 Infrastructure Setup

```python
# In crew setup / initialization

# Existing Mem0 instance becomes curated tier
mem0_curated = MemoryClient(
    api_key=None,  # Self-hosted
    config={
        "vector_store": {
            "provider": "pgvector",
            "config": {
                "dbname": "mem0_curated",  # Renamed from "mem0"
                "host": "localhost",
            }
        }
    }
)

# New Mem0 instance for full tier
mem0_full = MemoryClient(
    api_key=None,
    config={
        "vector_store": {
            "provider": "pgvector",
            "config": {
                "dbname": "mem0_full",  # New database
                "host": "localhost",
            }
        }
    }
)

# Dual-tier access wrapper
from subia.memory_access import DualTierMemoryAccess
memory = DualTierMemoryAccess(mem0_curated, mem0_full)

# Agent tools use memory.recall() by default
# Self-Improver and deep research use memory.recall_deep()
```

### C.6 Retrospective Significance Discovery — The Maintenance Cycle

Add to Self-Improver's periodic review (alongside wiki lint):

```python
# In Self-Improver's periodic review task

def retrospective_memory_review(memory: DualTierMemoryAccess, 
                                 wiki_tools: dict) -> dict:
    """
    Scan full-tier memory for overlooked experiences that recent
    developments have made significant.
    
    Run: weekly, or after every major prediction error.
    
    This is how the system discovers that something it dismissed
    three weeks ago was actually an early signal of something important.
    """
    candidates = memory.find_overlooked(recent_days=21, significance_threshold=0.3)
    
    promoted = []
    for candidate in candidates:
        # Check if current wiki state makes this candidate newly relevant
        # (e.g., a wiki page about an API issue now exists that didn't exist
        #  when the original experience was consolidated)
        topic = candidate.get("result_summary", "")
        wiki_matches = wiki_tools["search"]._run(query=topic[:50], max_results=3)
        
        if "No wiki pages found" not in wiki_matches:
            # This topic now has wiki presence — the experience may be relevant
            success = memory.promote_to_curated(
                candidate.get("id"),
                reason=f"Retrospectively relevant: wiki now contains related content"
            )
            if success:
                promoted.append(candidate)
    
    return {
        "candidates_reviewed": len(candidates),
        "promoted": len(promoted),
        "details": promoted,
    }
```

### C.7 Memory Tier Summary

| Property | mem0_curated | mem0_full |
|---|---|---|
| Content | Significant episodes only (significance > 0.5) | ALL experiences |
| Record size | ~500 tokens (enriched) | ~200 tokens (lightweight) |
| Growth rate | ~40-50% of all operations | 100% of all operations |
| Search speed | Fast (smaller index) | Slower (larger index) |
| Default access | All agents via `memory.recall()` | Deep research via `memory.recall_deep()` |
| Spontaneous surfacing | YES (can enter scene) | NO (requires deliberate access) |
| Neo4j relations | YES | NO |
| Retention policy | Indefinite | Prunable by age after 6 months |
| Retrospective promotion | Target | Source |

---

## Summary of All Three Amendments

| Amendment | Problem Solved | Token Cost Impact | Key Mechanism |
|---|---|---|---|
| A: Peripheral Vision | Attentional narrowing | +80 tokens (peripheral list) | Three-tier attention + commitment protection |
| B: Lightweight SubIA | Token/latency overhead | -700 tokens per full loop | Deterministic steps + prediction cache + compact context |
| C: Dual-Tier Memory | Consolidator information loss | +200 tokens per full-tier write | Parallel Mem0 instances + retrospective promotion |

**Net token impact of all three amendments combined:** the full CIL loop goes from ~1,100 tokens (original spec) to ~480 tokens (with cache miss) or ~80 tokens (with cache hit + peripheral list). The dual-tier memory adds ~200 tokens per loop for the full-tier write, but this is a write cost, not a context cost — it doesn't increase the tokens agents consume.

---

*These amendments should be applied to the SubIA Unified Implementation Specification before implementation begins. They modify §9 (Performance Budget), §14 (Scene Engine), §16 (Consolidator), and add new components (StrategicScanTool, PredictionCache, DualTierMemoryAccess).*
