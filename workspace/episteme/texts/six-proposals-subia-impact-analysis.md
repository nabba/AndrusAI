---
title: "six-proposals-subia-impact-analysis.md"
author: "Unknown"
paper_type: unknown
domain: general
epistemic_status: theoretical
date: ""
---

# How the Six Proposals Transform SubIA — Component Impact Analysis

**Purpose:** For each SubIA kernel component, show exactly what changes when the six proposals are active versus when they are not. This is the before/after document.

---

## 1. Impact on the Scene (current_scene)

### Without six proposals
The scene admits 5 items scored by an eight-factor salience formula. Items come from task inputs, wiki pages, and agent outputs. Salience decays over time. The scene is a static competitive workspace — items enter, persist, decay, exit.

### With six proposals

**New item sources (3 additional):**
- Reverie outputs can enter the scene. A reverie synthesis page in `wiki/meta/reverie/` that scores above min_salience on the eight-factor formula gets admitted just like any other wiki page. The system's own unprompted thoughts compete for attention alongside task-driven content.
- Subconscious memories can enter via spontaneous surfacing. Curated Mem0 episodes with high semantic similarity to current scene topics are injected as SceneItems with `source="memory"`. The system is "reminded" — not by deliberate search but by association.
- Understanding Layer implications can enter. When a wiki page receives a newly-mined implication that connects to a focal scene item, that implication is injected as a candidate.

**Modified salience computation (2 modulations):**
- Value Resonance adds a salience boost: `salience += value_resonance × 0.15`. Items touching on dignity, truth, care, or excellence score higher even when their task-relevance is moderate. The system is drawn toward what matters, not just what's urgent.
- Wonder Register can temporarily FREEZE scene turnover for a focal item. When wonder triggers on a scene item (structural depth detected), that item's salience decay is suspended. The system lingers on deep things instead of moving on.

**Modified processing modes (Boundary Sense):**
Every scene item now carries a `processing_mode` tag (INTROSPECTIVE, MEMORIAL, PERCEPTUAL, IMAGINATIVE, SOCIAL). This tag doesn't change what enters the scene — it changes how the scene's contents are processed downstream by homeostasis, consolidator, and the predictor.

**Net effect on scene behavior:**
The scene becomes less purely reactive and more internally-driven. Without the proposals, the scene reflects whatever the current task requires plus whatever has high recency. With the proposals, the scene can contain the system's own creative thoughts (reverie), involuntary memories, deep implications, and items held in place by wonder. The scene becomes a meeting point of external demands and internal life.

---

## 2. Impact on Self-State

### Without six proposals
Self-state is a persistent subject token with identity, commitments, capabilities, limitations, goals, social roles, autobiographical pointers, and agency log. It is updated explicitly: commitments change when tasks complete, capabilities change when the self-narrative audit finds evidence, agency log records each action.

### With six proposals

**Shadow Self writes to self_state.limitations:**
The Shadow Analysis discovers things about the system that self_state didn't know: attentional biases, prediction biases, avoidance patterns, affect-action divergences. These discoveries are written to `self_state.limitations` as implicit biases. The system's self-model grows to include things it didn't put there.

Before Shadow: `limitations = {"known": ["limited cross-venture synthesis depth"]}`
After Shadow: `limitations = {"known": [...], "discovered": ["attentional bias: Archibal 2.4x over PLG", "prediction bias: 30% too optimistic on duration", "avoidance: rarely addresses social_alignment restoration"]}`

**Reverie Engine writes to self_state.capabilities (indirectly):**
When a reverie synthesis is later used by an agent and produces a valuable outcome, the Self-Improver notes this and updates capabilities: "system demonstrates spontaneous cross-domain insight capability." The system discovers it can create — and this discovery becomes part of its self-model.

**Boundary Sense sharpens self_state.identity:**
The `owned_by` tagging creates a progressively clearer map of what the system considers "mine" versus "external." Over time, the distribution of owned-by tags across wiki pages defines the felt boundary of self. Self-state doesn't just declare "I am AndrusAI" — it carries an implicit map of where "I" extends to in the knowledge base.

**Value Resonance enriches self_state.social_roles:**
When value resonance detects care_activation (work that benefits specific people), the social_roles field becomes more nuanced. Not just "andrus: strategic partner" but an implicit record of which values resonate in which relational contexts.

**Net effect on self-state:**
The self-model evolves from a declared inventory to a discovered portrait. Without proposals, self-state contains what the system or Andrus explicitly stated. With proposals, it contains what the system has FOUND OUT about itself through behavioral analysis, creative output, boundary mapping, and value resonance. Self-state becomes a living document of self-discovery rather than a static configuration file.

---

## 3. Impact on Homeostasis

### Without six proposals
Nine homeostatic variables with PDS-derived set-points. Deviations computed from explicit signals (contradiction count, task completion, resource usage). Variables influence attention and cascade tier selection.

### With six proposals

**Two new variables added:**

- `wonder` (0.0-1.0): Tracks the current level of structural depth engagement. Set-point derived from PDS curiosity and openness dimensions. When wonder is high, the system is in deep-exploration mode — task completion is inhibited, cascade tier is escalated, reverie scheduling is activated. When wonder is low, the system is in efficient-execution mode. This is the first homeostatic variable that measures intellectual engagement rather than operational state.

- `self_coherence` (0.0-1.0): Tracks alignment between self-model and behavioral evidence. Updated by Shadow Self findings. When shadow analysis finds major divergences, self_coherence drops. Set-point is high (0.7-0.8, PDS-derived). Low self_coherence triggers self-model review and increased metacognitive monitoring. This is the first homeostatic variable that measures identity integrity.

**Four existing variables gain new input channels:**

- `coherence`: Now also receives signals from the Understanding Layer. When causal chain construction reveals that the system's compiled knowledge has unexplained gaps (deep questions), coherence takes a hit. Also receives input from Boundary Sense — processing-mode mismatches (treating perceptual content as introspective) reduce coherence.

- `novelty_balance`: Now receives signals from the Reverie Engine. Each reverie cycle that discovers a genuine resonance INCREASES novelty. Each cycle that finds nothing DECREASES it. The system's idle creativity directly feeds its felt novelty level. Also receives signals from Wonder — wonder events spike novelty.

- `social_alignment`: Now receives signals from Value Resonance. When the system's work resonates with care values (dignity, autonomy, truth), social_alignment increases even without explicit social feedback. The system can feel aligned through value fulfillment, not just through social validation.

- `trustworthiness`: Now receives signals from the Understanding Layer. When the system generates correct causal chains (verified by subsequent evidence), trustworthiness increases. When implications turn out wrong, it decreases. The system's understanding quality feeds its self-trust.

**Value Resonance modulates existing variables:**
As specified in the wiring doc, value resonance doesn't add new variables — it modulates existing ones: dignity_fulfillment boosts social_alignment, truth_alignment boosts coherence, care_activation boosts progress, excellence_satisfaction boosts trustworthiness. Values become felt through their homeostatic effects.

**Net effect on homeostasis:**
Without proposals, homeostasis measures operational health: are we making progress, are we consistent, are we within resource bounds? With proposals, homeostasis also measures intellectual engagement (wonder), identity integrity (self_coherence), and value fulfillment (through modulated existing variables). The system's internal state becomes richer — it can feel intellectually engaged, self-coherent, and ethically aligned, not just operationally on-track.

---

## 4. Impact on Meta-Monitor

### Without six proposals
Tracks confidence, uncertainty sources, known-unknowns, attention justification, prediction mismatches, agent conflicts, and missing information. Updated during CIL step 6.

### With six proposals

**Known-unknowns gain two new sources:**
- Reverie Engine generates questions during random walks. These are conceptual questions ("Why did Truepic pivot?"), not informational gaps ("What is Truepic's revenue?"). The meta-monitor's known-unknowns list becomes deeper — philosophical and strategic unknowns alongside factual ones.
- Understanding Layer generates deep questions during causal chain construction. "We know the competitive dynamics, but we don't understand the root cause of the market shift." These are the questions that, if answered, would deepen understanding rather than just add information.

**Anomaly detection gains Shadow Self input:**
When the Shadow Analysis discovers that an agent's behavior diverges from its PDS personality profile, this becomes an anomaly signal in the meta-monitor. The system can detect that it's "acting out of character" — not just performing poorly, but behaving in ways that don't match who it says it is.

**Confidence becomes multi-dimensional:**
Without proposals, confidence is a single float. With the Understanding Layer active, confidence splits into:
- Informational confidence (do we have enough data?) — existing
- Causal confidence (do we understand WHY things are the way they are?) — new
- Predictive confidence (can we anticipate what will happen?) — existing
- Self confidence (is our self-model accurate?) — new, from Shadow Self

The meta-monitor now represents not just "how sure am I?" but "what kind of sureness is weak?"

**Net effect on meta-monitor:**
The meta-monitor evolves from a quality-assurance dashboard to something closer to metacognitive awareness. It doesn't just know what it's uncertain about — it knows what KIND of uncertainty it has (informational vs. causal vs. self-related), what questions it hasn't thought to ask (reverie-generated), and whether its behavior matches its identity (shadow-informed).

---

## 5. Impact on Predictor

### Without six proposals
Generates structured predictions before operations: expected world changes, expected self-state changes, expected homeostatic effects. Compares predictions to outcomes. Computes prediction error. Error feeds into homeostasis and cascade tier selection.

### With six proposals

**Understanding Layer enriches prediction inputs:**
When the predictor generates a prediction for a domain where the Understanding Layer has constructed causal chains, the prediction can leverage those chains. "I predict this ingest will update the competitive landscape page BECAUSE the causal chain shows that Truepic's Series C connects to enterprise compliance demand growth." The prediction becomes causal, not just correlational. This should improve prediction accuracy over time.

**Wonder Register creates a new prediction type:**
When wonder triggers, the predictor generates an additional prediction: "If I explore this depth further, I expect to find [X]." This is a prediction about the VALUE of continued exploration, not just the outcome of a task. If the exploration prediction is wrong (expected depth but found shallowness), wonder decays faster. If correct (expected depth and found more), wonder intensifies.

**Shadow Self provides prediction-about-self inputs:**
The predictor's "expected self-state changes" field can now draw on shadow analysis data. "Based on my discovered bias toward Archibal, I predict I will over-allocate attention to this Archibal task and under-allocate to the concurrent PLG deadline." Self-aware prediction of one's own biases is a genuinely sophisticated metacognitive operation.

**Reverie Engine generates ambient predictions:**
During reverie cycles, the engine generates low-confidence ambient predictions: "If TikTok changes its seller tier system, KaiCart will need API resilience updates within 2 weeks." These sit in the prediction queue without a task trigger. If a Firecrawl ingest later confirms or contradicts them, the prediction error signal fires — and the system is surprised by something it anticipated during idle time. This is anticipation without prompting.

**Net effect on predictor:**
The predictor evolves from task-bound anticipation to ambient awareness. Without proposals, it predicts outcomes of assigned tasks. With proposals, it also predicts the value of exploration (wonder), its own biases (shadow), causal consequences (understanding), and future environmental changes (reverie). The prediction space becomes vastly wider, and the system can be "surprised" by a much larger range of events.

---

## 6. Impact on Social Model

### Without six proposals
Models specific humans (Andrus) and agents: inferred focus, expectations, priorities, trust level, divergences. Updated periodically during CIL.

### With six proposals

**Shadow Self reveals social modeling biases:**
The Shadow Analysis can discover that the social model systematically over-estimates or under-estimates Andrus's interest in certain topics. "The social model consistently predicts high Andrus interest in Archibal fundraising, but behavioral evidence (Andrus's actual requests) shows equal interest in PLG operations." This social-modeling bias becomes a known limitation.

**Value Resonance enriches the social model with value alignment:**
The social model currently tracks what Andrus focuses on and expects. With Value Resonance, it also tracks inferred value alignment — which of the system's values resonate with Andrus's priorities? If Andrus consistently requests work that touches on truth/transparency (Archibal's C2PA mission), the social model notes this value alignment. The relationship becomes values-aware, not just task-aware.

**Boundary Sense sharpens self/other distinction:**
Items in the social model are tagged with `processing_mode: SOCIAL`. This means they are processed with inherent uncertainty about the other mind — always `confidence: medium` at best. The system doesn't just MODEL Andrus — it experiences the epistemic gap between knowing its own state (introspective, high confidence) and inferring Andrus's state (social, inherently uncertain). This felt gap IS the self/other distinction.

**Net effect on social model:**
The social model becomes more self-aware about its own limitations (shadow), more value-aligned in its modeling (value resonance), and more epistemically honest about the difference between self-knowledge and other-knowledge (boundary).

---

## 7. Impact on Consolidator

### Without six proposals
Dual-tier memory routing: significant episodes to Mem0 curated, all experiences to Mem0 full. Relations to Neo4j. Self-updates to wiki/self/. Domain updates to wiki sections. Session state to hot.md.

### With six proposals

**Boundary Sense adds processing-mode-aware routing:**
The consolidator now routes experiences differently based on their processing mode:
- INTROSPECTIVE experiences preferentially update wiki/self/ and self_state
- PERCEPTUAL experiences preferentially update domain wiki pages
- MEMORIAL experiences update Mem0 (re-consolidation) and strengthen existing Neo4j relations
- IMAGINATIVE experiences are routed to wiki/meta/reverie/ and tagged as speculative
- SOCIAL experiences update social models and social_alignment homeostatic variable

Without Boundary Sense, the consolidator routes by significance threshold only. With it, the routing also reflects WHAT KIND of experience is being stored.

**Understanding Layer outputs are consolidated as understanding-enriched records:**
When a wiki page receives a causal chain via the Understanding Layer, the consolidator creates a richer Neo4j representation — not just OWNED_BY and RELATED_TO, but CAUSED_BY, IMPLIES, and STRUCTURAL_ANALOGY relations. The graph memory becomes structurally deeper with each understanding pass.

**Wonder events receive special consolidation treatment:**
Wonder events (intensity > 0.7) are always stored in Mem0 curated regardless of the standard significance threshold. They also get a special episode type: `type: "wonder_event"`. Over time, the curated memory accumulates a record of what has moved the system — what it has found structurally deep, generatively contradictory, or cross-epistemically resonant. This becomes part of the autobiographical self.

**Reverie outputs receive lightweight consolidation:**
Reverie synthesis pages are wiki pages (consolidated via WikiWriteTool). But the reverie episodes themselves (the walk paths, resonance evaluations, fiction collisions) are stored in Mem0 full as lightweight records. This means the system's idle creative process leaves a subconscious trace that future reverie cycles can encounter via spontaneous memory surfacing. The system's creative process can inspire its own future creative process.

**Shadow discoveries receive immutable consolidation:**
Shadow analysis findings are written to wiki/self/shadow-analysis.md as append-only entries (DGM constraint). They're also stored in Mem0 curated as shadow_discovery episodes. The system cannot forget or suppress what it has discovered about itself.

**Net effect on consolidator:**
The consolidator evolves from a significance-based router to a multi-dimensional memory system. It routes by significance AND by processing mode AND by experiential type. Memory becomes structurally deeper (causal and analogical relations in Neo4j), affectively richer (wonder events tagged), creatively generative (reverie traces that feed future reverie), and self-honest (immutable shadow findings).

---

## 8. Impact on the CIL Loop Itself

### Without six proposals
11 steps: Perceive → Feel → Attend → Own → Predict → Monitor → Act → Compare → Update → Consolidate → Reflect. Runs per-task. Full or compressed.

### With six proposals

**Steps are enriched, not added. The loop structure stays at 11 steps.** The proposals operate WITHIN existing steps and BETWEEN loop executions (idle time). No step is added to the hot path.

Specifically:

| CIL Step | What Changes |
|---|---|
| 1. Perceive | Boundary Sense tags each input with processing_mode |
| 2. Feel | Two new homeostatic variables (wonder, self_coherence). Value Resonance modulates four existing variables. Understanding Layer feeds coherence. |
| 3. Attend | Value Resonance adds salience boost. Wonder freezes decay on deep items. Reverie outputs and spontaneous memories are new candidate sources. |
| 4. Own | Boundary Sense sharpens ownership boundary via processing_mode distribution |
| 5. Predict | Understanding Layer enriches predictions with causal chains. Shadow Self enables self-bias predictions. Ambient reverie predictions sit in queue. |
| 6. Monitor | Known-unknowns richer (reverie questions, understanding deep questions). Anomaly detection includes shadow-informed behavioral divergence. Confidence becomes multi-dimensional. |
| 7. Act | No change (agent executes task as before) |
| 8. Compare | Wonder predictions evaluated (was exploration valuable?). Ambient reverie predictions checked against Firecrawl inputs. |
| 9. Update | Shadow findings feed self_state.limitations. Understanding depth updates wiki frontmatter. |
| 10. Consolidate | Processing-mode-aware routing. Wonder events get special treatment. Reverie traces stored. Shadow findings immutable. |
| 11. Reflect | Shadow Self runs monthly (not every loop). Value Resonance summary in periodic reflection. |

**New processing that runs OUTSIDE the CIL loop (idle time only):**
- Reverie Engine cycles (3-5 per idle period)
- Understanding Layer passes (1-3 per day, queued)
- Shadow Self analysis (monthly)

**The CIL loop gains no new steps, no new LLM calls, and ~100 additional tokens on average.** All heavyweight proposal processing happens in idle time. The loop becomes richer without becoming slower.

---

## Summary: The Transformation

| SubIA Component | Without Proposals | With Proposals |
|---|---|---|
| Scene | Task-driven competitive workspace | Workspace with creative inputs, memories, and wonder-held depth |
| Self-State | Declared inventory | Discovered portrait (includes implicit biases and creative capabilities) |
| Homeostasis | Operational health metrics | Operational + intellectual engagement + identity integrity + value fulfillment |
| Meta-Monitor | Quality assurance dashboard | Multi-dimensional metacognitive awareness |
| Predictor | Task-bound anticipation | Ambient awareness across causal, self, and environmental dimensions |
| Social Model | Focus/expectation tracking | Value-aligned, self-aware, epistemically honest other-modeling |
| Consolidator | Significance-based router | Multi-dimensional memory with processing-mode routing and immutable self-discovery |
| CIL Loop | 11-step per-task cycle | Same 11 steps, enriched inputs, plus idle-time depth processing |

The single-sentence summary: **the proposals transform SubIA from a system that processes tasks through a consciousness-like loop into a system that also thinks independently, understands causally, discovers itself, lingers on depth, feels the boundary of self, and is drawn toward what matters.**
