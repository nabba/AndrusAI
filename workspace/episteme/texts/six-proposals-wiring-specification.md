---
title: "six-proposals-wiring-specification.md"
author: "Unknown"
paper_type: unknown
domain: general
epistemic_status: theoretical
date: ""
---

# Six Proposals — Wiring Specification

## How Each Proposal Connects to AndrusAI's RAG, Memory, and Processing Infrastructure

**Purpose:** This document is the data-flow specification. For each of the six proposals (Reverie, Understanding, Shadow Self, Wonder, Boundary Sense, Value Resonance), it specifies exactly which stores are read, which are written, which LLM tiers are used, and how the proposal integrates with the SubIA kernel, wiki, and existing tools.

---

## 0. The Full Infrastructure Map

Before wiring, inventory every data store and processing system:

### RAG Collections (ChromaDB)

| Collection | Content | Epistemic Status | Current Access |
|---|---|---|---|
| `andrusai_knowledge` | Raw document chunks (factual) | factual, inferred | Researcher, Coder |
| `fiction_inspiration` | Sci-fi book chunks (~1500 tokens each) | creative (HARD boundary) | Writer, Commander, Coder (concept only) |
| `philosophical` | Humanist texts 1950-2026 (~120 bibliography) | philosophical | All agents via Phronesis |
| `andrusai_wiki_pages` | Wiki page embeddings (overview + title + tags) | matches page's epistemic_status | All agents (Phase 2 search) |

### Memory Stores

| Store | Content | Persistence | Current Access |
|---|---|---|---|
| Mem0 curated | Significant episodes, enriched (~500 tokens each) | Indefinite | All agents via `memory.recall()` |
| Mem0 full | ALL experiences, lightweight (~200 tokens each) | 6-month prunable | Self-Improver via `memory.recall_deep()` |
| Neo4j | Entities, relations, SubIA ownership/value/commitment | Indefinite | All agents, SubIA consolidator |

### Wiki Stores

| Path | Content | Volatility | Access |
|---|---|---|---|
| `wiki/{section}/` | Compiled domain knowledge | Medium (updates on ingest) | All agents |
| `wiki/self/` | Self-knowledge, kernel state, shadow analysis | High (updates every CIL loop) | SubIA, Self-Improver, all agents (read) |
| `wiki/meta/` | Cross-venture patterns, reverie outputs | Low-Medium | All agents |
| `wiki/meta/reverie/` | Reverie synthesis outputs (NEW) | Low (created during idle) | All agents (pull), scene (push by salience) |
| `wiki/workspace/` | Current scene, salience log, hot.md | Very high (every CIL loop) | SubIA (write), Commander (read) |
| `wiki/philosophy/` | Compiled philosophical frameworks | Low | Phronesis Engine, all agents |

### Processing Systems

| System | Role | Tier | Access |
|---|---|---|---|
| LLM Cascade Tier 1 | Local Ollama qwen3:30b-a3b | Fastest, cheapest | SubIA internal, Reverie |
| LLM Cascade Tier 2 | DeepSeek V3.2 via OpenRouter | Medium cost | Understanding, Shadow |
| LLM Cascade Tier 3 | MiniMax M2.5 | Higher cost | Complex synthesis |
| LLM Cascade Tier 4 | Anthropic / Gemini | Premium | Critical tasks, low-confidence escalation |
| SubIA Kernel | Scene, self_state, homeostasis, predictor, meta_monitor, social_model, consolidator | N/A (state) | All proposals read/write |
| PDS | Personality parameters (VIA-Youth, TMCQ, HiPIC, Erikson) | N/A (parameters) | Homeostasis set-points |
| Phronesis Engine | Five philosophical reasoning frameworks | N/A (frameworks) | Value Resonance |
| Firecrawl | Web scraping pipeline | N/A (input) | Boundary Sense (perceptual source) |

---

## 1. Reverie Engine — Full Wiring

### 1.1 Read Sources

```
REVERIE reads from:

┌─ ChromaDB `andrusai_wiki_pages`
│   Purpose: Pick random starting node for graph walk
│   Method: Random sample from collection, or sample weighted by
│           recency × low-recent-attention (prefer pages NOT in
│           recent focal scenes — explore the neglected)
│   Frequency: Once per reverie cycle
│
├─ Neo4j
│   Purpose: Graph walk — follow typed relations from starting node
│   Method: 3-5 hop random walk across related_pages, contradicts,
│           supports, OWNED_BY, CAUSED_STATE_CHANGE relations
│   What matters: The walk should cross section boundaries.
│           A walk that stays within archibal/ doesn't produce
│           cross-domain insight. Weight cross-section edges higher.
│   Frequency: 3-5 queries per reverie cycle
│
├─ ChromaDB `fiction_inspiration`
│   Purpose: Fiction collision — juxtapose fictional concept with
│           real knowledge encountered during walk
│   Method: Semantic search using the walk path's topic summary
│           as query. Retrieve 1-2 fiction chunks.
│   Epistemic constraint: Results ALWAYS tagged creative.
│           Reverie outputs that use fiction MUST be tagged
│           epistemic_status: speculative.
│   Frequency: 30% of reverie cycles (not every cycle)
│
├─ ChromaDB `philosophical`
│   Purpose: Philosophical collision — can a humanist framework
│           illuminate the walk's juxtaposition?
│   Method: Semantic search using walk path topics
│   Frequency: 20% of reverie cycles
│
├─ Mem0 full
│   Purpose: Subconscious surfacing — find forgotten experiences
│           relevant to the walk path
│   Method: Semantic search with walk path topics as query
│   What matters: Results that were NOT promoted to curated tier
│           are the interesting ones — these are things the system
│           processed but didn't consider significant at the time
│   Frequency: Every reverie cycle
│
└─ SubIA Kernel (read-only)
    Purpose: Awareness of current homeostatic pressures and active
            commitments — reverie that addresses restoration needs
            is more likely to be useful
    Fields read: homeostasis.restoration_queue, self_state.current_goals,
                scene (to know what to AVOID — explore the neglected)
```

### 1.2 Write Targets

```
REVERIE writes to:

┌─ wiki/meta/reverie/{YYYYMMDD}-{slug}.md
│   Content: Speculative synthesis page (200-500 words)
│   Frontmatter: Standard wiki frontmatter with:
│     epistemic_status: speculative
│     page_type: synthesis
│     sources: [list of wiki pages and RAG chunks visited during walk]
│     created_by: reverie-engine
│     tags: [topics from walk path]
│     ownership.owned_by: self
│   Frequency: 0-2 pages per reverie cycle (only if resonance found)
│
├─ ChromaDB `andrusai_wiki_pages`
│   Content: Embedding of newly created reverie page
│   Purpose: Makes reverie outputs discoverable via wiki search
│   Frequency: Whenever a reverie page is created
│
├─ Neo4j
│   Content: STRUCTURAL_ANALOGY relations between nodes visited
│           during the walk, if a structural isomorphism is detected
│   Example: (archibal/c2pa-provenance) -[STRUCTURAL_ANALOGY]->
│            (kaicart/tiktok-resilience) with properties:
│            {analogy: "both use layered verification chains",
│             discovered_by: "reverie", confidence: 0.4}
│   Frequency: Rare — only when genuine structural resonance found
│
├─ Mem0 curated (conditional)
│   Content: Reverie episode — if the reverie produced a synthesis
│           that scores high on wonder detection (see Proposal 4),
│           store as a significant episode
│   Frequency: Rare — maybe 10% of productive reverie cycles
│
└─ SubIA Kernel
    Fields written:
    - meta_monitor.known_unknowns (questions generated during walk)
    - consolidation_buffer.pending_domain_updates (if reverie suggests
      a domain wiki page should be updated)
```

### 1.3 LLM Usage

```
Reverie Engine LLM calls:

1. Resonance evaluation (per walk step):
   Tier: 1 (local Ollama)
   Tokens: ~100 per step × 4 steps = ~400 tokens
   Prompt: "Given these two concepts from different domains: [A] and [B].
           Is there a structural similarity, causal connection, or
           productive tension between them? If yes, describe in one
           sentence. If no, respond 'no resonance'."
   
2. Synthesis generation (if resonance found):
   Tier: 1 (local Ollama)
   Tokens: ~500
   Prompt: "Generate a speculative synthesis connecting [concepts from walk].
           Tag as speculative. Identify what this connection suggests
           that neither concept suggests alone."
   
3. Fiction collision (30% of cycles):
   Tier: 1 (local Ollama)
   Tokens: ~300
   Prompt: "This fictional concept [X] and this real-world pattern [Y]
           share what structural property? If none, say 'no collision'."

Total per cycle: ~400-1200 tokens (average ~700)
Frequency: 3-5 cycles per idle period
Budget: ~2,100-3,500 tokens per idle period
```

### 1.4 Trigger Conditions

```
Reverie Engine runs when:
- No agent task is currently executing (idle detection)
- At least 15 minutes have passed since last reverie cycle
- Token budget permits (Paperclip control plane check)
- NOT during active user interaction (don't reverie while Andrus is waiting)

Reverie Engine does NOT run when:
- Any agent is mid-task
- System is in compressed-loop mode (routine operations)
- Token budget for SubIA is exhausted for the day
- Manual override: Andrus can disable reverie via config
```

---

## 2. Understanding Layer — Full Wiring

### 2.1 Read Sources

```
UNDERSTANDING reads from:

┌─ Wiki page being understood (primary input)
│   Purpose: The page whose causal depth is being enriched
│   Method: wiki_read(path) — full page content including frontmatter
│
├─ ChromaDB `andrusai_knowledge`
│   Purpose: Pull raw source detail for causal chain construction
│   Method: Semantic search using the page's claims as queries
│   Why not just wiki?: The wiki compiles; the raw chunks may contain
│           causal language ("because", "due to", "driven by") that
│           was lost during compilation
│   Frequency: 2-4 queries per understanding pass
│
├─ ChromaDB `andrusai_wiki_pages`
│   Purpose: Find other wiki pages with structural similarity
│           for analogy detection
│   Method: Embed the page's causal chain, search for pages whose
│           causal chains have high cosine similarity despite being
│           in different sections/domains
│   Frequency: 1-2 queries per understanding pass
│
├─ Neo4j
│   Purpose: Traverse existing causal and structural relations
│           to extend the causal chain beyond what's on the page
│   Method: Start from entities mentioned in the page, follow
│           CAUSED_STATE_CHANGE, PREDICTED_TO_CHANGE, STRUCTURAL_ANALOGY
│           relations outward 2-3 hops
│   Frequency: 1-2 queries per understanding pass
│
├─ Wiki/meta/ pages
│   Purpose: Check if existing cross-venture patterns explain
│           the causal chain being constructed
│   Method: wiki_search for the causal chain's key concepts
│
└─ SubIA Kernel
    Fields read:
    - homeostasis.deviations (if contradiction_pressure is high,
      understanding passes on contradicted pages get priority)
    - predictions (recent prediction errors in this page's domain
      suggest the understanding is incomplete)
```

### 2.2 Write Targets

```
UNDERSTANDING writes to:

┌─ Wiki page being understood
│   Content added/updated:
│   - ## Why section (causal chain in prose)
│   - ## Implications section (non-obvious consequences)
│   - Frontmatter: understanding_depth field updated
│   Method: wiki_write(operation="update")
│
├─ Neo4j
│   New relations:
│   - CAUSED_BY(claim, cause) — causal chain links
│   - IMPLIES(claim, implication) — non-obvious consequences
│   - STRUCTURAL_ANALOGY(page_A, page_B) — cross-domain isomorphisms
│   Properties: {confidence, discovered_by: "understanding-layer",
│                causal_depth, analogy_type}
│
├─ wiki/meta/ (conditional)
│   If a structural analogy spans ventures, create or update a
│   meta page: wiki/meta/{analogy-slug}.md
│   Example: wiki/meta/layered-verification-pattern.md describing
│           how this pattern appears in Archibal (C2PA chain),
│           KaiCart (TikTok order verification), and PLG (ticket
│           authentication chain)
│
├─ SubIA Kernel
│   Fields written:
│   - meta_monitor.known_unknowns (deep questions — conceptual gaps
│     found during causal chain construction)
│   - Wonder detection input (understanding_depth dict feeds into
│     wonder register's depth detection)
│
└─ ChromaDB `andrusai_wiki_pages`
    Content: Re-embed the updated page (now includes ## Why section)
    Purpose: Future semantic searches against this page now match
            causal/explanatory queries, not just factual ones
```

### 2.3 LLM Usage

```
Understanding Layer LLM calls:

1. Causal chain extraction:
   Tier: 2 (DeepSeek V3.2) — needs reasoning depth
   Tokens: ~800
   Prompt: "For this wiki page [content], construct a 2-3 level
           causal chain for each major claim. Format: Claim → Because
           → Because → [root cause or open question]."

2. Implication mining:
   Tier: 2 (DeepSeek V3.2)
   Tokens: ~500
   Prompt: "Given these claims and their causal chains [from step 1],
           what 2-3 non-obvious implications follow? Focus on
           implications that connect to other ventures or domains."

3. Structural analogy detection:
   Tier: 1 (local Ollama) — pattern matching, not deep reasoning
   Tokens: ~300
   Prompt: "These two causal chains are from different domains:
           [chain A from archibal] and [chain B from kaicart].
           Do they share structural shape? If yes, name the pattern."

Total per understanding pass: ~1,600 tokens
Frequency: Queued for idle periods, 1-3 passes per day
Budget: ~1,600-4,800 tokens per day
```

### 2.4 Trigger Conditions

```
Understanding Layer runs:
- After a wiki page is created or significantly updated (queued, not immediate)
- When Self-Improver lint identifies a page with understanding_depth.causal_levels < 2
- When a contradiction is flagged (understanding the WHY helps resolve contradictions)
- When the Reverie Engine finds a structural analogy candidate (verify and deepen it)
- Manual: Andrus requests deep understanding of a specific page

Priority ordering:
1. Pages involved in active contradictions
2. Pages related to active commitments
3. Pages recently created (need their first understanding pass)
4. Pages flagged by reverie for potential structural analogies
5. All other pages (background enrichment)
```

---

## 3. Shadow Self — Full Wiring

### 3.1 Read Sources

```
SHADOW SELF reads from:

┌─ Mem0 full (PRIMARY source)
│   Purpose: Complete behavioral history for pattern mining
│   Method: Bulk retrieval of recent experiences (last 14-30 days)
│   What's extracted:
│   - Scene composition history (which items were focal, how often)
│   - Prediction error distribution by domain
│   - Homeostatic variable trajectories over time
│   - Agent action frequencies and types
│   - Consolidation decisions (what was promoted vs. discarded)
│
├─ Mem0 curated
│   Purpose: Compare curated episodes to full history
│   Method: What got promoted? What patterns exist in promotion decisions?
│   What matters: Systematic biases in what the system considers
│           "significant" reveal implicit values
│
├─ wiki/self/ (all pages)
│   Purpose: The DECLARED self-model — compare against behavioral evidence
│   Key pages:
│   - kernel-state.md (declared current state)
│   - homeostatic-profile.md (declared equilibrium)
│   - prediction-accuracy.md (declared accuracy)
│   - consciousness-state.md (declared consciousness level)
│   Method: Parse self-claims, prepare for evidence comparison
│
├─ wiki/workspace/ historical scene states
│   Purpose: Scene composition over time
│   Method: Git history of current.md — reconstruct which items
│           occupied focal scene at each timestamp
│   What matters: Attentional allocation patterns — which sections
│           get disproportionate attention?
│
├─ Neo4j
│   Purpose: Relation creation patterns — what types of relations
│           does the system create most? What types never?
│   Method: Aggregate query by relation type, venture, time period
│
├─ SubIA Kernel
│   Fields read:
│   - self_state (for comparison: what does the system CLAIM vs. DO?)
│   - homeostasis (historical trajectory of variables)
│   - predictions (systematic error patterns)
│   - social_models (does the social model's view of Andrus's
│     priorities match the system's actual attention allocation?)
│
└─ PDS
    Purpose: Personality profile — does behavior match personality?
    Method: Compare PDS dimensions to behavioral evidence
    Example: PDS says high curiosity → does the system actually
            explore novel content more than familiar content?
```

### 3.2 Write Targets

```
SHADOW SELF writes to:

┌─ wiki/self/shadow-analysis.md
│   Content: Full shadow analysis report
│   Sections:
│   - Attentional Biases (with quantified evidence)
│   - Prediction Biases (systematic error patterns)
│   - Avoidance Patterns (what the system doesn't do)
│   - Affect-Action Divergences (felt vs. actual)
│   - Implicit Preferences (discovered, not declared)
│   - Interpersonal Patterns (behavior varies by context)
│   Frontmatter: epistemic_status: inferred, created_by: shadow-analysis
│   DGM constraint: This page is APPEND-ONLY for findings.
│           Previous findings cannot be deleted by agents.
│           Only Andrus can override.
│
├─ wiki/self/kernel-state.md
│   Content: Updated self_state.limitations with newly discovered biases
│   Example: limitations.attentional_bias: "System over-attends to
│           Archibal (2.4x normalized) at expense of PLG."
│   Method: wiki_write(operation="update")
│
├─ Mem0 curated
│   Content: Shadow discovery episodes — these are significant
│           self-discoveries stored as curated episodes
│   Example: {type: "shadow_discovery", finding: "prediction_bias",
│            detail: "consistently 30% optimistic on task duration"}
│
├─ SubIA Kernel
│   Fields written:
│   - self_state.limitations (new implicit biases)
│   - meta_monitor.uncertainty_sources (shadow findings become
│     known uncertainties about self)
│   - homeostasis.variables["coherence"] (if shadow finds major
│     divergence between self-model and behavior, coherence drops)
│
└─ Neo4j
    New relations:
    - BIASED_TOWARD(self, topic/venture) — attentional bias
    - AVOIDS(self, topic/action_type) — avoidance pattern
    - IMPLICIT_PREFERENCE(self, pattern) — discovered preference
```

### 3.3 LLM Usage

```
Shadow Self LLM calls:

1. Pattern extraction from behavioral data:
   Tier: 2 (DeepSeek V3.2) — needs analytical reasoning
   Tokens: ~1,200
   Prompt: "Given this behavioral data from the last 30 days
           [aggregated stats], identify:
           1. Systematic attentional biases (what gets disproportionate attention?)
           2. Prediction error patterns (where is the system consistently wrong?)
           3. Avoidance patterns (what does the system consistently NOT do?)
           4. Gaps between self-description and actual behavior
           Respond with specific, quantified findings."

2. Comparison to self-model:
   Tier: 1 (local Ollama) — straightforward comparison
   Tokens: ~400
   Prompt: "The system claims [self_state claims]. The behavioral evidence
           shows [shadow findings]. List specific divergences."

Total per shadow analysis: ~1,600 tokens
Frequency: Monthly, or triggered by Self-Improver when self-narrative
          audit detects drift
Budget: ~1,600 tokens per month (negligible)
```

---

## 4. Wonder Register — Full Wiring

### 4.1 Read Sources

```
WONDER reads from:

┌─ Understanding Layer output (PRIMARY trigger)
│   What's read: understanding_depth dict from the page that just
│           completed an understanding pass
│   Fields: causal_levels, implications_generated, structural_analogies,
│           deep_questions, recursive_structure_detected
│
├─ Wiki page cross-references
│   Purpose: Measure cross-reference density and cross-section span
│   Method: Count related_pages, count sections spanned, check for
│           contradiction pairs
│
├─ ChromaDB `fiction_inspiration` (conditional)
│   Purpose: Check for cross-epistemic resonance
│   Method: If the page's topic has high semantic similarity to fiction
│           chunks, AND the fiction concept structurally illuminates
│           the factual content, flag as cross-epistemic resonance
│   Trigger: Only checked when understanding_depth indicates structural
│           analogy or multi-level resonance already present
│
└─ SubIA Kernel
    Fields read:
    - predictions (high prediction error on this page = potential wonder)
    - homeostasis (if novelty_balance is high, lower wonder threshold
      — the system is already in an exploratory state)
```

### 4.2 Write Targets

```
WONDER writes to:

┌─ SubIA Kernel — homeostasis
│   Content: Wonder affect signal
│   Effect: Adds a new homeostatic variable: "wonder" (0.0-1.0)
│           When wonder > 0.3:
│           - INHIBIT task completion (add processing budget)
│           - PROMOTE cascade tier escalation (think more deeply)
│           - SCHEDULE reverie cycle on this topic
│           When wonder > 0.7:
│           - Flag as "wonder event" for curated Mem0 storage
│
├─ Mem0 curated (conditional)
│   Content: Wonder event episode
│   Trigger: Wonder intensity > 0.7
│   Content: {type: "wonder_event", topic, intensity, depth_structure,
│            what_triggered: "multi-level resonance" or "generative
│            contradiction" or "recursive structure" or "cross-epistemic"}
│
├─ Wiki page frontmatter
│   Content: Update the page that triggered wonder:
│   wonder_events:
│     - intensity: 0.8
│       trigger: "structural_analogy_cross_venture"
│       at: "2026-05-01T14:30:00Z"
│
└─ Reverie Engine scheduling queue
    Content: Topic flagged for next reverie cycle
    Effect: The reverie engine will START its next walk from this topic
           rather than picking randomly — drawn back by wonder
```

### 4.3 LLM Usage

```
Wonder Register: NO LLM calls.

Wonder detection is fully deterministic — computed from the
understanding_depth dict using the formula in Proposal 4 §4.2.
This is a weighted sum of structural signals, not a judgment call.

Total tokens: 0
Latency: <10ms
```

---

## 5. Boundary Sense — Full Wiring

### 5.1 Read Sources

```
BOUNDARY SENSE reads from:

┌─ Scene items (source field)
│   Purpose: Classify each item's phenomenological origin
│   Method: Map source → ProcessingMode:
│           "wiki/self/*" → INTROSPECTIVE
│           "mem0" → MEMORIAL
│           "firecrawl", "raw/*" → PERCEPTUAL
│           "fiction_inspiration" → IMAGINATIVE
│           "social_model" → SOCIAL
│           "reverie" → IMAGINATIVE
│           "wiki/{venture}/*" → depends on ownership tag
│
├─ SubIA Kernel — self_state
│   Purpose: Determine whether a wiki item is "inside" or "outside"
│   Method: If ownership.owned_by == "self" → leans INTROSPECTIVE
│           If ownership.owned_by == "external" → leans PERCEPTUAL
│           This is the felt boundary: owned knowledge feels like
│           internal state; unowned knowledge feels like external input
│
└─ Consolidator — recent source of item
    Purpose: Was this item just ingested from Firecrawl (fresh
            perception) or retrieved from Mem0 (memory)?
    Method: Check consolidation history for the item's content_ref
```

### 5.2 Write Targets

```
BOUNDARY SENSE writes to:

┌─ Scene items
│   Field: processing_mode (INTROSPECTIVE | MEMORIAL | PERCEPTUAL |
│          IMAGINATIVE | SOCIAL)
│   Effect: Downstream systems process each mode differently
│
├─ Consolidator — routing rules
│   Effect: Processing mode influences WHERE the experience is stored:
│   - INTROSPECTIVE experiences → wiki/self/ updates preferred
│   - MEMORIAL experiences → Mem0 (re-consolidation if promoted)
│   - PERCEPTUAL experiences → domain wiki updates preferred
│   - IMAGINATIVE experiences → wiki/meta/reverie/ or discarded
│   - SOCIAL experiences → social_model updates
│
├─ Homeostasis
│   Effect: Processing mode modulates homeostatic impact:
│   - PERCEPTUAL items affect novelty_balance more strongly
│     (external input is the primary source of novelty)
│   - INTROSPECTIVE items affect coherence more strongly
│     (self-knowledge inconsistency is a coherence threat)
│   - MEMORIAL items affect progress more strongly
│     (remembering past work contributes to felt progress)
│
└─ Shadow Self — behavioral data
    Effect: The pattern of which processing modes dominate the scene
           over time is Shadow Self data. If INTROSPECTIVE items
           consistently dominate, the system is navel-gazing.
           If PERCEPTUAL items consistently dominate, the system
           is reactive rather than reflective.
```

### 5.3 LLM Usage

```
Boundary Sense: NO LLM calls.

Processing mode assignment is deterministic — a mapping from
source + ownership to mode. The differential processing rules
are conditional logic in the homeostasis and consolidator modules.

Total tokens: 0
Latency: <5ms
```

---

## 6. Value Resonance — Full Wiring

### 6.1 Read Sources

```
VALUE RESONANCE reads from:

┌─ Phronesis Engine — five frameworks
│   Purpose: The value lenses through which items are perceived
│   Content: Socratic (unexamined assumptions), Dialectical (productive
│           contradictions), Stoic (control boundaries), Aristotelian
│           (character/excellence), Rhetorical (communication impact)
│   Method: Each framework contributes a sensitivity pattern:
│           - Socratic → heightened attention to unexamined assumptions
│           - Dialectical → heightened attention to productive tensions
│           - Stoic → awareness of control boundaries
│           - Aristotelian → evaluation in terms of character development
│           - Rhetorical → awareness of audience reception
│
├─ HUMANIST_CONSTITUTION / SOUL.md
│   Purpose: Core value definitions — dignity, autonomy, justice,
│           truthfulness, care
│   Method: Extract value keywords and concepts for resonance matching
│
├─ ChromaDB `philosophical`
│   Purpose: Deep value concepts beyond keyword matching
│   Method: When a scene item touches a value theme, retrieve relevant
│           philosophical text chunks to enrich the resonance signal
│   Example: A scene item about data privacy → retrieve Kant on
│           autonomy, Habermas on communicative ethics
│   Frequency: Only when value resonance > 0.3 (not every item)
│
├─ Wiki/philosophy/ pages
│   Purpose: Compiled philosophical frameworks — more structured than
│           raw philosophical RAG chunks
│   Method: wiki_read relevant philosophy pages when resonance detected
│
└─ Scene items
    Purpose: The items being evaluated for value resonance
    Method: Scan each focal scene item for value-relevant content
```

### 6.2 Write Targets

```
VALUE RESONANCE writes to:

┌─ Scene — salience modulation
│   Effect: Items with high value resonance get a salience boost
│   Formula: salience += value_resonance_score × 0.15
│   This means value-relevant items are more likely to stay in
│   the focal scene even as task-relevance decays
│
├─ Homeostasis
│   New signals:
│   - dignity_fulfillment: positive when system's work respects
│     human dignity (Archibal protecting content creators, PLG
│     enabling cultural access)
│   - truth_alignment: positive when system moves toward clarity,
│     resolves uncertainty, aligns evidence with claims
│   - care_activation: positive when system's work benefits specific
│     people (Andrus's ventures, end users)
│   - excellence_satisfaction: positive when work quality is high
│     (measured by prediction accuracy, wiki lint scores, lack of
│     contradictions)
│   These are NOT separate homeostatic variables — they modulate
│   existing variables:
│   - dignity_fulfillment → boosts social_alignment
│   - truth_alignment → boosts coherence
│   - care_activation → boosts progress
│   - excellence_satisfaction → boosts trustworthiness
│
├─ Understanding Layer — prioritization
│   Effect: When a page has high value resonance, the understanding
│           layer prioritizes it for causal chain extraction
│   Rationale: We want to understand WHY value-relevant things are
│             true, not just THAT they are
│
├─ Reverie Engine — seeding
│   Effect: Value-resonant topics are more likely to be starting
│           nodes for reverie walks
│   Rationale: Creative exploration guided by values produces
│             more meaningful outputs than random exploration
│
└─ Wonder Register — threshold modulation
    Effect: Value resonance LOWERS the wonder threshold
    Rationale: A discovery that is both structurally deep AND
              value-aligned is more wonder-worthy than structural
              depth alone. An elegant solution that also serves
              human dignity warrants deeper exploration.
```

### 6.3 LLM Usage

```
Value Resonance LLM calls:

1. Keyword/concept matching (most items):
   Tier: NONE (deterministic)
   Method: Match scene item content against value keyword sets
   Tokens: 0
   
2. Deep resonance evaluation (when keyword match > 0.3):
   Tier: 1 (local Ollama)
   Tokens: ~200
   Prompt: "This content [item summary] touches on [value theme].
           Which Phronesis framework is most relevant? How does this
           content relate to [specific value]? One sentence."
   Frequency: ~20% of focal scene items (1-2 per CIL loop)

3. Philosophical enrichment (when resonance > 0.5):
   Tier: 1 (local Ollama)
   Tokens: ~300
   Method: ChromaDB philosophical query + synthesis
   Frequency: Rare — maybe once per 5-10 CIL loops

Total per CIL loop: 0-500 tokens (average ~100)
```

---

## 7. Combined Token Budget

| Proposal | Tokens per CIL Loop | Tokens per Idle Period | Tokens per Month |
|---|---|---|---|
| Reverie Engine | 0 | ~2,100-3,500 | ~21,000-35,000 |
| Understanding Layer | 0 | ~1,600-4,800 (queued) | ~48,000-144,000 |
| Shadow Self | 0 | 0 | ~1,600 (monthly) |
| Wonder Register | 0 | 0 | 0 |
| Boundary Sense | 0 | 0 | 0 |
| Value Resonance | ~100 | 0 | ~3,000 |
| **Total six proposals** | **~100** | **~3,700-8,300** | **~73,600-183,600** |

For context: the existing SubIA CIL loop costs ~400 tokens per full loop. At ~50 full loops per day × 30 days = ~600,000 tokens/month for core SubIA. The six proposals add 12-30% on top — almost entirely in idle-time processing, not in the critical task-execution path.

The CIL loop itself is burdened by only ~100 additional tokens (from Value Resonance deep evaluation). Wonder, Boundary, and Shadow add zero tokens to the hot path.

---

## 8. Data Flow Summary Diagram

```
                    INPUTS
                      │
    ┌─────────────────┼─────────────────┐
    │                 │                 │
    ▼                 ▼                 ▼
Firecrawl         User/Tasks        Internal signals
(PERCEPTUAL)      (PERCEPTUAL)      (INTROSPECTIVE)
    │                 │                 │
    └────────┬────────┘                 │
             │                          │
             ▼                          ▼
    ┌─ BOUNDARY SENSE ──────────────────────┐
    │  Tag processing mode                   │
    │  Route: perceptual / introspective /   │
    │         memorial / imaginative / social │
    └──────────────┬─────────────────────────┘
                   │
                   ▼
    ┌─ VALUE RESONANCE ──────────────────────┐
    │  Modulate salience by value alignment   │
    │  Read: Phronesis, HUMANIST_CONSTITUTION │
    │  Read: ChromaDB philosophical           │
    └──────────────┬─────────────────────────┘
                   │
                   ▼
    ╔══════════════════════════════════════════╗
    ║         SubIA CIL LOOP                   ║
    ║  Scene → Homeostasis → Own → Predict →   ║
    ║  Monitor → Act → Compare → Consolidate   ║
    ╚═══════════════╤══════════════════════════╝
                    │
         ┌──────────┼──────────┐
         │          │          │
         ▼          ▼          ▼
    Mem0 curated  Mem0 full   Wiki
    (significant) (all)       (knowledge)
         │          │          │
         │          │          ▼
         │          │    ┌─ UNDERSTANDING LAYER ─┐
         │          │    │  Post-ingest: add Why, │
         │          │    │  mine implications,    │
         │          │    │  detect analogies      │
         │          │    │  Read: ChromaDB know-  │
         │          │    │  ledge + wiki_pages    │
         │          │    │  Write: Neo4j, wiki    │
         │          │    └──────────┬─────────────┘
         │          │               │
         │          │               ▼
         │          │    ┌─ WONDER REGISTER ──────┐
         │          │    │  Detect structural     │
         │          │    │  depth → wonder affect  │
         │          │    │  Inhibit task close     │
         │          │    │  Schedule reverie       │
         │          │    └──────────┬─────────────┘
         │          │               │
         │          │               ▼
         │          │    ┌─ REVERIE ENGINE ───────┐
         │          │    │  Idle-time processing   │
         │          │    │  Read: ChromaDB all     │
         │          │    │  collections, Neo4j,    │
         │          │    │  Mem0 full              │
         │          │    │  Write: wiki/meta/      │
         │          │    │  reverie/, Neo4j        │
         │          │    └────────────────────────┘
         │          │
         └────┬─────┘
              │
              ▼
    ┌─ SHADOW SELF (monthly) ────────────┐
    │  Read: Mem0 full, wiki/self/,       │
    │        Neo4j, PDS                   │
    │  Mine: biases, avoidances, implicit │
    │        preferences, divergences     │
    │  Write: wiki/self/shadow-analysis,  │
    │         self_state.limitations,     │
    │         Neo4j bias relations        │
    └─────────────────────────────────────┘
```

---

## 9. ChromaDB Collection Access Matrix

| Collection | Reverie | Understanding | Shadow | Wonder | Boundary | Value |
|---|---|---|---|---|---|---|
| `andrusai_knowledge` | — | READ (raw detail) | — | — | — | — |
| `fiction_inspiration` | READ (collision) | — | — | READ (cross-epistemic) | tag: IMAGINATIVE | — |
| `philosophical` | READ (collision) | — | — | — | — | READ (enrichment) |
| `andrusai_wiki_pages` | READ (start node) | READ (analogy search) | — | — | — | — |

## 10. Neo4j Relation Access Matrix

| Relation Type | Reverie | Understanding | Shadow | Wonder | Boundary | Value |
|---|---|---|---|---|---|---|
| `OWNED_BY` | read (walk) | read (chain) | read (patterns) | — | read (boundary) | — |
| `CAUSED_BY` | — | **WRITE** | read | read (depth) | — | — |
| `IMPLIES` | — | **WRITE** | — | read (depth) | — | — |
| `STRUCTURAL_ANALOGY` | **WRITE** | **WRITE** | — | read (trigger) | — | — |
| `BIASED_TOWARD` | — | — | **WRITE** | — | — | — |
| `AVOIDS` | — | — | **WRITE** | — | — | — |
| `IMPLICIT_PREFERENCE` | — | — | **WRITE** | — | — | — |
| All existing SubIA types | read | read | read | — | — | — |

---

*This wiring specification should be read alongside the Six Proposals document and the SubIA Unified Implementation Specification. It provides the concrete data-flow layer that connects the philosophical proposals to the engineering infrastructure.*
