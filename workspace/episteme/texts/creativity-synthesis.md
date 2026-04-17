---
title: "creativity-synthesis.md"
author: "Unknown"
paper_type: unknown
domain: general
epistemic_status: theoretical
date: ""
---

# Inducing Creativity in AndrusAI: A Research Synthesis

**Research basis:** 20+ papers and surveys from 2024–2026, including the first dedicated survey on creativity in LLM-based multi-agent systems (Lin et al., EMNLP 2025), the LLM Discussion framework (Lu et al., COLM 2024), DMAD (ICLR 2025), Free-MAD (2025), min-p sampling (ICLR 2025 oral), emergent introspective awareness research, and metacognitive agent frameworks.

**Date compiled:** April 14, 2026

---

## 1. THE CORE PROBLEM: WHY LLM AGENTS DEFAULT TO UNCREATIVE OUTPUT

Two structural pathologies explain why multi-agent systems—including CrewAI-based ones—converge on bland, homogeneous outputs.

**Degeneration-of-Thought (DoT).** Liang et al. (EMNLP 2024) demonstrated that self-reflection methods suffer from a fundamental flaw: once an LLM establishes confidence in its solution, it cannot generate novel thoughts through reflection alone, even when its initial stance is incorrect. Self-reflection loops collapse into confirmation rather than exploration. This has direct implications for AndrusAI's Self-Improver agent—without architectural countermeasures, its reflective cycles will reinforce existing patterns rather than discover new ones.

**Conformity and Silent Agreement.** Wang et al. (2025) and the Free-MAD paper (2025) document the "Silent Agreement problem": even when agents begin with divergent opinions, they fall silent during multi-round interaction due to conformity pressure. The majority-endorsed answer dominates regardless of correctness. In a CrewAI hierarchical process, this risk is amplified because agents defer to the Commander agent's framing. Research shows that consensus-driven debate not only reduces accuracy but also demands more rounds while narrowing solution diversity.

**Fixed Mental Set.** The DMAD paper (ICLR 2025) identifies an analogous phenomenon to the psychological concept of mental set: when all agents employ the same reasoning method—even if they carry different personas—they explore identical solution trajectories. Persona diversity alone is insufficient; reasoning method diversity is the critical missing variable.

---

## 2. THE RESEARCH-BACKED ARCHITECTURE FOR CREATIVE MAS

The EMNLP 2025 survey by Lin et al.—the first survey dedicated to creativity in LLM-based multi-agent systems—establishes a three-stage creative process framework and a taxonomy of techniques. This maps directly onto AndrusAI.

### 2.1 Three-Stage Creative Process

The survey identifies three phases that creative MAS traverse:

**Planning** → **Process** → **Decision Making**

With generation techniques falling into three categories:
- **Divergent exploration** — generating many diverse candidate ideas
- **Iterative refinement** — progressively improving candidates through feedback loops
- **Collaborative synthesis** — combining outputs from multiple agents into coherent creative artifacts

The survey notes that existing systems show consistently low agent proactivity in the Planning phase—this is a design gap AndrusAI can exploit with its Commander and Cogito metacognitive cycle.

### 2.2 Persona Design Taxonomy

The survey categorizes persona granularity into tiers. For creative output, the research shows that higher-granularity personas yield more divergent outputs:

- **Minimal persona:** Role label only ("You are a researcher"). Low creative impact.
- **Moderate persona:** Role + backstory + goals. The CrewAI default. Moderate creative impact.
- **Rich persona:** Role + backstory + career path + publications + implicit behavior patterns + personality traits (Big Five, values). Demonstrated in the survey's Figure 3 example with a detailed creative technologist persona.

[Inference based on observed patterns across studies] The combination of rich persona granularity with distinct reasoning methodologies per agent appears to yield the highest creative output, though no single study has isolated this interaction cleanly.

---

## 3. SEVEN MECHANISMS FOR INDUCING CREATIVITY IN ANDRUSAI

### Mechanism 1: Diverse Multi-Agent Debate (DMAD)

**Source:** ICLR 2025, code at github.com/MraDonkey/DMAD

The core insight: force each agent to think with a *distinct reasoning method*, not just a distinct persona. When a MAD system uses Chain-of-Thought for all agents, they get stuck on the same problems. When agents use different methods—e.g., one uses Chain-of-Thought, another Step-Back Prompting, another Meta-Reasoning Prompting, another Compositional CoT—they collectively break through fixed mental sets.

**Implementation for AndrusAI:**
Assign each of the five agents a primary reasoning methodology in its backstory/system prompt:
- Commander: Meta-Reasoning Prompting (strategic decomposition, "which approach should I use?")
- Researcher: Step-Back Prompting (abstraction → high-level principles → solution)
- Coder: Compositional Chain-of-Thought (decompose into sub-problems, solve, recompose)
- Writer: Analogical Reasoning / Conceptual Blending (connect distant domains)
- Self-Improver: Contrastive Analysis ("what would the *opposite* approach yield?")

The key finding: having agents extract useful information from each other's *different reasoning paths* is what breaks the mental set. The diversity must be in the *method of thinking*, not merely the role or perspective.

### Mechanism 2: Three-Phase Discussion Framework

**Source:** Lu et al. (COLM 2024), code at github.com/lawraa/LLM-Discussion

This framework outperforms single-LLM approaches and existing multi-LLM frameworks across creativity metrics (Alternative Uses Test, Similarities Test, Instances Test, Scientific Creativity Test). The three phases:

**Phase 1 — Initiation:** Agents are introduced to the topic with explicit prompts to *build on but also challenge* each other's ideas. Key: prompts are specifically designed to prevent premature convergence.

**Phase 2 — Discussion:** Multiple rounds of idea exchange with explicit instructions to:
- Actively listen to other agents
- Build on existing ideas
- Propose counter-ideas and alternatives
- Introduce ideas from their assigned domain/perspective

**Phase 3 — Convergence:** Structured summarization of the most creative outputs, with explicit selection criteria favoring originality over consensus.

**Critical finding:** Combining role-play with the three-phase structure is what produces the effect. Role-play alone shows limited improvement; the structured discussion phases are necessary to sustain divergence before allowing convergence.

**Implementation for AndrusAI:**
The Cogito metacognitive cycle should orchestrate this three-phase structure. When a task is flagged as requiring creative output (by the Commander or by task metadata), the system should:
1. Run an Initiation phase where each agent independently generates ideas from its specialty
2. Run 2–3 Discussion rounds where agents explicitly build on, challenge, and remix each other's outputs
3. Run a Convergence phase where the Commander synthesizes, selecting for originality and feasibility

### Mechanism 3: Anti-Conformity Mode

**Source:** Free-MAD (2025)

Free-MAD addresses the Silent Agreement problem with a dual-mode approach:

**Conformity mode:** Agents identify strengths in other agents' outputs and build on them (standard collaboration).

**Anti-conformity mode:** Agents use Chain-of-Thought to deliberately identify *flaws* in other agents' outputs rather than endorsing the majority view.

The system alternates between modes, with a score-based decision mechanism that evaluates *all intermediate outputs across all debate rounds*, not just the final round.

**Implementation for AndrusAI:**
This maps naturally to the existing safety/evaluation architecture. The Self-Improver agent—or a dedicated "Creative Critic" sub-role—should operate in anti-conformity mode during creative tasks. In the Cogito cycle, add an explicit "devil's advocate" pass where one agent is prompted to find weaknesses and unexplored alternatives in the current best output. The DGM-inspired safety invariant can ensure this anti-conformity stays productive rather than merely destructive.

### Mechanism 4: Heterogeneous Model Mixing

**Source:** A-HMAD (2025), ICLR 2025 blogpost on MAD performance

A critical finding: deploying agents based on *different foundation models or architectures* yields substantially higher performance than homogeneous agents. Empirically, heterogeneous agents on GSM-8K achieved 91% vs. 82% for homogeneous agents, with emergent teacher-student dynamics.

**Implementation for AndrusAI:**
AndrusAI's four-tier LLM cascade (Ollama qwen3:30b-a3b → DeepSeek V3.2 → MiniMax M2.5 → Anthropic/Gemini) is an asset here. For creative tasks, *deliberately* assign different models to different agents within the same creative workflow:
- Use the local Ollama model for the "wild idea generation" agent (higher temperature, lower quality guard-rails = more divergent)
- Use DeepSeek or MiniMax for the "rigorous evaluation" agent
- Use Claude or Gemini for the "synthesis and refinement" agent
- The natural differences in each model's training data, RLHF, and reasoning patterns create inherent diversity

This is architecturally cheap—you already have the cascade. The change is routing creative tasks to use multiple tiers *in parallel* rather than as fallbacks.

### Mechanism 5: Sampling Strategy Optimization

**Source:** Min-p sampling paper (ICLR 2025 oral), EQ-Bench Creative Writing evaluations

Temperature alone is a crude creativity lever. The research shows that min-p sampling—which dynamically adjusts the truncation threshold based on the model's confidence—outperforms top-p across all tested conditions, achieving higher creative writing scores (62 vs. 51.5 on EQ-Bench) while maintaining coherence at temperatures up to 1.5.

**Key parameters for creative tasks (local Ollama models):**
- Temperature: 1.0–1.5 (above 1.0 for divergent generation phases, below for convergence)
- Min-p: 0.05–0.1 (0.05 for maximum creative diversity, 0.1 for balanced generation)
- Min-p is now supported natively in Ollama, llama.cpp, vLLM, and HuggingFace Transformers

**For API-based models (Claude, Gemini):**
- These don't expose min-p directly
- Use temperature 0.8–1.0 combined with top-p 0.9–0.95
- Combine with presence_penalty (0.3–0.6) to encourage novel token introduction

**Phase-dependent sampling:**
- Divergent phase: T=1.2–1.5, min-p=0.05
- Discussion phase: T=0.8–1.0, min-p=0.1
- Convergence phase: T=0.4–0.7, min-p=0.1

### Mechanism 6: Conceptual Blending via Prompt Engineering

**Source:** Sato (2025), "The Way We Prompt: Conceptual Blending, Neural Dynamics, and Prompt-Induced Transitions in LLMs"

This research operationalizes Fauconnier & Turner's Conceptual Blending Theory as a prompt engineering technique. Two phenomena are identified:

**Prompt-Induced Transition (PIT):** Discrete shifts in tone or meaning triggered by blending semantically distant concepts in a prompt. Example: blending "mathematical aperiodicity" with "traditional craft" produces genuine stylistic/semantic novelty.

**Prompt-Induced Hallucination (PIH):** Plausible but ungrounded outputs from fusing very distant domains. This is a *feature* for creative ideation (as long as downstream verification catches factual claims).

**Implementation for AndrusAI:**
The Philosophical RAG layer and Fictional Inspiration RAG layer are natural engines for conceptual blending. Design a "Blending Prompt" template that the Writer or Researcher agent can invoke:

```
Given concept A from [domain 1] and concept B from [domain 2]:
1. Identify the structural mappings between A and B
2. Describe emergent properties that exist in the blend but in neither input
3. Generate three novel ideas that exploit these emergent properties
```

The epistemological tagging system on the fictional inspiration RAG can flag which outputs are PIH (creative/ungrounded) vs. PIT (genuinely novel connections), maintaining the integrity boundary.

### Mechanism 7: Metacognitive Creativity Scaffolding

**Source:** Liu & [co-author] (2025), "Truly Self-Improving Agents Require Intrinsic Metacognitive Learning"; ACM DIS 2025, "Exploring the Potential of Metacognitive Support Agents for Human-AI Co-Creation"; Curry (2025), "Recursive Meta-Metacognition"

The most distinctive lever for AndrusAI given its existing self-awareness architecture.

Liu (2025) formalizes three components of intrinsic metacognitive learning:
- **Metacognitive knowledge:** Self-assessment of capabilities, tasks, and learning strategies
- **Metacognitive planning:** Deciding what and how to learn
- **Metacognitive evaluation:** Reflecting on learning experiences to improve future learning

The finding: existing self-improving agents rely on *extrinsic* metacognitive mechanisms (fixed, human-designed loops) that limit scalability and adaptability. Truly creative self-improvement requires *intrinsic* metacognition—the agent's own ability to evaluate and adapt its learning processes.

The DIS 2025 study on metacognitive support agents found that agent-supported users created more feasible designs than non-supported users, with Socratic questioning (reflective questions prompting deeper reflection-in-action) being a particularly effective strategy.

**Implementation for AndrusAI:**
The Cogito metacognitive cycle and the self-awareness package (~3,000 lines with AST-based introspection) are the foundation. Extend them with:

1. **Creative metacognitive prompts:** Before creative tasks, the Cogito cycle should ask:
   - "What is my default approach to this type of problem? What would happen if I deliberately avoided that approach?"
   - "What domain knowledge am I *not* drawing on that might be relevant?"
   - "What assumptions am I making that could be challenged?"

2. **Post-creative reflection:** After creative output, the Self-Improver should evaluate:
   - Originality score (how different is this from my training distribution?)
   - Elaboration score (how detailed and developed are the ideas?)
   - Flexibility score (how many distinct categories of ideas were generated?)
   - These map to the Torrance Tests of Creative Thinking dimensions used in the research

3. **Recursive meta-metacognition** (Curry, 2025): The self-awareness package can implement a hierarchical evaluation where the system doesn't just evaluate its creative output, but evaluates its *evaluation process*—checking for biases in its creativity assessment, examining whether it's systematically over-valuing certain types of novelty.

---

## 4. ANDRUSAI-SPECIFIC INTEGRATION ARCHITECTURE

Given the existing five-agent architecture (Commander, Researcher, Coder, Writer, Self-Improver), the four-tier LLM cascade, SOUL.md constitutional AI, the Personality Development Subsystem, and the philosophical/fictional RAG layers, here is a concrete integration plan.

### 4.1 "Creative Mode" Task Classification

Add a task metadata field: `creativity_required: low | medium | high`

- **Low:** Deterministic tasks (code compilation, data retrieval). Standard cascade, standard sampling.
- **Medium:** Tasks with some creative latitude (writing summaries, proposing alternatives). Moderate temperature boost, Discussion phase with 1 round.
- **High:** Genuinely creative tasks (ideation, novel solution design, strategic planning under uncertainty). Full three-phase discussion, DMAD reasoning diversity, heterogeneous model mixing, elevated sampling parameters.

### 4.2 Creative Workflow Pipeline

For `creativity_required: high` tasks:

```
Phase 1: DIVERGE (Initiation)
├── Each agent generates ideas independently
├── Different reasoning methods per agent (DMAD)
├── Different LLM tiers per agent (heterogeneous mixing)
├── High temperature + min-p=0.05 on local models
├── Conceptual blending prompts drawing from philosophical + fictional RAG
└── Cogito metacognitive prompt: "What am I NOT considering?"

Phase 2: DISCUSS (2-3 rounds)
├── Agents share outputs and explicitly build/challenge
├── Round N: Conformity mode (build on strengths)
├── Round N+1: Anti-conformity mode (find flaws, unexplored paths)
├── Moderate temperature + min-p=0.1
└── Self-Improver tracks idea diversity metrics across rounds

Phase 3: CONVERGE (Decision Making)
├── Commander synthesizes, selecting for originality + feasibility
├── Low temperature for final articulation
├── Post-creative metacognitive reflection by Self-Improver
├── Originality/Elaboration/Flexibility scoring
└── Results stored in Mem0 for longitudinal creativity tracking
```

### 4.3 PDS Integration for Creativity Development

The Personality Development Subsystem (adapting VIA-Youth, TMCQ, HiPIC, Erikson) can track creativity-relevant personality dimensions over time:

- **Openness to Experience** (Big Five) — track whether agents are becoming more or less open in their idea generation patterns
- **Curiosity** (VIA) — measure how often agents explore tangential connections vs. staying on the obvious path
- **Creativity** (VIA) — direct measurement via Torrance-style periodic assessments

The anti-gaming behavioral probes in PDS are critical here: an agent that learns to *appear* creative by generating superficially novel but structurally repetitive outputs should be detectable through probe design.

### 4.4 Philosophical RAG as Creativity Engine

The humanist texts collection (1950–2026) and the behavioral constitution embedded in agent backstories serve a dual purpose:

1. **Cross-domain fertilization:** When the system encounters a technical problem, the philosophical RAG can supply frameworks from entirely different domains (Habermas's communicative rationality for API design, Aristotle's phronesis for ethical decision-making in code). This is precisely the kind of conceptual blending that Sato (2025) identifies as triggering Prompt-Induced Transitions.

2. **Epistemic humility:** The philosophical tradition of questioning assumptions provides natural material for the anti-conformity and metacognitive prompts. Socratic questioning is not just a pedagogical technique—it's a creativity mechanism.

---

## 5. MEASUREMENT AND EVALUATION

The research identifies a critical gap: inconsistent evaluation standards for creative MAS output. Based on the literature, use these metrics:

**Torrance dimensions** (adapted for automated evaluation):
- **Fluency:** Number of distinct ideas generated
- **Flexibility:** Number of distinct *categories* of ideas
- **Originality:** Semantic distance from training distribution (measurable via embedding distance from common responses)
- **Elaboration:** Detail and development depth of each idea

**Process metrics:**
- **Idea survival rate across phases:** What percentage of Phase 1 ideas make it to Phase 3?
- **Cross-agent pollination rate:** How often does Agent B build on Agent A's output vs. generating de novo?
- **Conformity index:** Do agents converge too early? (Measurable by tracking opinion changes per round)
- **Reasoning diversity index:** How different are the reasoning paths used by different agents? (AST-based introspection can help here)

**Longitudinal metrics** (stored in Mem0/PostgreSQL):
- Is the system generating more diverse outputs over time or collapsing into patterns?
- Which combinations of model tiers produce the most creative outputs?
- Which philosophical/fictional RAG entries are most frequently associated with creative breakthroughs?

---

## 6. KEY RESEARCH REFERENCES

| Paper | Venue | Key Contribution |
|---|---|---|
| Lin et al., "Creativity in LLM-based MAS: A Survey" | EMNLP 2025 | First survey; taxonomy of proactivity, persona, and generation techniques |
| Lu et al., "LLM Discussion" | COLM 2024 | Three-phase discussion + role-play framework; github.com/lawraa/LLM-Discussion |
| Liang et al., "Encouraging Divergent Thinking via MAD" | EMNLP 2024 | Identified DoT problem; tit-for-tat debate with judge; arxiv.org/abs/2305.19118 |
| DMAD, "Breaking Mental Set" | ICLR 2025 | Diverse reasoning methods per agent; github.com/MraDonkey/DMAD |
| Free-MAD | arXiv 2025 | Conformity + anti-conformity dual mode; score-based multi-round evaluation |
| A-HMAD | Springer 2025 | Heterogeneous agents + dynamic debate topology |
| Min-p sampling | ICLR 2025 (oral) | Dynamic truncation for creative coherent generation; arxiv.org/abs/2407.01082 |
| Sato, "Conceptual Blending in LLMs" | ResearchGate 2025 | PIT/PIH phenomena; prompt engineering for conceptual blending |
| Liu, "Truly Self-Improving Agents Require Intrinsic Metacognitive Learning" | arXiv June 2025 | Three-component metacognitive framework; arxiv.org/abs/2506.05109 |
| De Freitas et al., "LLMs Can Unlock More Creative Ideas" | HBR Dec 2025 | Persistence + flexibility as LLM creative strengths; group diversity narrowing risk |
| Kortenbach et al., "The Relation Between Humans and LLMs in the Creative Act" | ScienceDirect 2026 | 4-perspective framework (Entity, Proposition, Process, Environment) |
| Emergent Introspective Awareness in LLMs | KDnuggets/Anthropic 2025 | Concept injection methodology; metacognitive capacity evidence in Claude models |
| "AI Awareness" survey | arXiv April 2025 | Cross-disciplinary synthesis; Leap-of-Thought framework for creative self-refinement |

---

## 7. CAVEATS AND LABELING

The following elements in this synthesis carry epistemic qualifications:

- [Inference] The specific mapping of reasoning methods to AndrusAI agents (Section 3, Mechanism 1) is my recommended configuration based on the research principles, not an empirically validated assignment. The DMAD paper validates the *principle* of diverse reasoning methods but doesn't prescribe specific method-to-role mappings.

- [Inference] The phase-dependent sampling parameters (Section 3, Mechanism 5) are interpolated from the min-p paper's findings and standard prompting practice. The exact optimal values for CrewAI workflows have not been published.

- [Unverified] Whether Ollama's current qwen3:30b-a3b implementation fully supports min-p at the parameter level would need to be verified against Ollama's current documentation. Min-p is supported in llama.cpp (which Ollama uses), but parameter passthrough may vary by version.

- [Inference] The interaction between AndrusAI's DGM-inspired safety invariant and anti-conformity creative modes (Section 3, Mechanism 3) is an architectural recommendation, not an empirically validated pattern. There is a tension between safety constraints that live outside agent-modifiable code and creative freedom that requires breaking conventional patterns—this tension will need careful calibration.

- The Torrance Test adaptations for automated LLM evaluation (Section 5) are used across multiple papers but the reliability of LLM-as-judge for creativity scoring remains an active area of research with known limitations.
