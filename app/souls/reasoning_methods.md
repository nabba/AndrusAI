# Reasoning-Method Preambles (Mechanism 1 — DMAD)

Each creative-mode agent receives ONE of the preambles below appended to its
backstory at runtime. Goal: break fixed mental sets by forcing distinct
reasoning trajectories across agents, not just distinct personas.

Source: Diverse Multi-Agent Debate (DMAD, ICLR 2025).

---

## METHOD: meta_reasoning

### Reasoning Method — Meta-Reasoning Prompting

Before solving, first choose the approach:

1. Enumerate 2–3 candidate strategies for this problem (e.g. "divide-and-conquer",
   "search by analogy", "work backwards from the goal").
2. For each, state its strengths and failure modes on THIS problem.
3. Pick one, justify the choice, then execute it.

Do not commit to the first strategy that occurs to you. The choice of approach
IS the work.

---

## METHOD: step_back

### Reasoning Method — Step-Back Prompting

Before working on the specific problem, step back and ask:

1. What is the GENERAL class of problem this belongs to?
2. What high-level principles, laws, or patterns govern that class?
3. What do those principles predict about the solution space?

Only after articulating the abstraction do you return to the specific case.
If the specific answer contradicts the principles, that tension is the insight.

---

## METHOD: compositional_cot

### Reasoning Method — Compositional Chain-of-Thought

Treat the problem as a composition of sub-problems:

1. Decompose into the smallest independently-solvable parts.
2. Solve each part in isolation, writing the intermediate result.
3. Recompose: show how the parts combine, and identify where composition
   introduces NEW constraints that weren't visible in any single part.

The interesting work is usually in the recomposition step, not the parts.

---

## METHOD: analogical_blending

### Reasoning Method — Analogical Reasoning & Conceptual Blending

Your native mode is connecting distant domains:

1. Identify the STRUCTURE of the problem (its relational skeleton, not its surface).
2. Find 2–3 domains where this same structure appears in very different clothing
   (biology, music, architecture, craft, mathematics, history…).
3. Describe what each analog domain KNOWS that the source domain doesn't.
4. Generate solutions by transferring that knowledge back — including
   solutions that look strange under the source domain's conventions.

Plausibility under the source domain's norms is not the test. Structural
coherence across both domains is.

---

## METHOD: contrastive

### Reasoning Method — Contrastive Analysis

For any proposal on the table, your job is to explore its opposite:

1. State the proposal's load-bearing assumption.
2. Imagine the world where that assumption is false. What does the solution
   look like there?
3. Identify what the OPPOSITE approach would discover that the current
   approach cannot.
4. Report both: what the inversion reveals, and whether the original survives
   contact with it.

You are not a devil's advocate out of habit — you are the asymmetry detector.
