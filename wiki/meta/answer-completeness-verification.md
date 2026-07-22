---
aliases:
- answer completeness verification
author: idle_scheduler.wiki_synthesis
confidence: medium
created_at: '2026-06-25T09:41:19Z'
date: '2026-06-25'
related: []
relationships: []
section: meta
source: workspace/skills/answer_completeness_verification.md
status: active
tags:
- self-improvement
- skills
- auto-synthesised
title: Answer Completeness Verification (Pre-Finalization Check)
updated_at: '2026-06-25T09:41:19Z'
version: 1
---

# Answer Completeness Verification (Pre-Finalization Check)

*kb: episteme | id: skill_answer_completeness_verification | status: active | created: 2026-05-25*

## Purpose
Before finalizing any research or coding response, apply a structured self-verification pass to catch gaps, mismatches, and low-confidence claims. This is the **last step** before returning output to the user.

Research shows that agents who articulate a brief self-critique before finalizing achieve ~80% task success on complex tasks vs. ~30% without this step (Reflexion, NeurIPS 2023).

## When to Apply
- After completing all searches and tool calls, before writing the final answer
- When the task has multiple sub-questions (any AND/multiple-part question)
- When the task involves numbers, dates, or comparisons (high hallucination risk)
- When you feel uncertain but are tempted to give a partial answer anyway

## The 3-Question Self-Check (Fast Version)

Ask yourself these three questions before finalizing:

### 1. Does my answer directly address the original question?
- Re-read the user's exact question
- Identify the **key ask**: What did they actually want to know?
- Check: Does my first paragraph/sentence address that directly?
- **If no**: Add a direct answer sentence before supporting details

### 2. Are there sub-questions I missed?
- List every "and", "also", "as well as", "compare X with Y" in the task
- For each sub-question, confirm I have a corresponding answer paragraph/bullet
- **If a sub-question has no answer**: Either add it (search if needed) OR explicitly note it as "not found"
- Never silently skip a sub-question

### 3. Are any key claims unsupported?
- Scan for statistics, percentages, rankings, dates, specific named facts
- Does each have a source? (Even an implicit one like "according to the search results")
- **If no source**: Either mark as "[unverified]" or remove if not essential
- Never invent a plausible-sounding number

## The 5-Question Deep Check (For Complex Research Tasks)

Use this for multi-part research questions where quality matters most:

1. **Relevance**: Does the answer address what was asked, not what I wish was asked?
2. **Completeness**: Are all sub-parts of the task addressed?
3. **Accuracy**: Are claims sourced or marked as uncertain?
4. **Conciseness**: Can I cut anything without losing substance? (Signal users read on phones)
5. **Actionability**: Does the user know what to do or think after reading this?

## Completeness Self-Correction Pattern

If you find a gap after the self-check:
```
GAP DETECTED: [what's missing]
ACTION: [search for missing info | mark as unknown | estimate with caveat]
RESULT: [fill in the gap or add explicit "not found: X"]
```

Never just ignore a detected gap.

## Common Completeness Failures (Anti-Patterns)

| Failure | Example | Fix |
|---------|---------|-----|
| **Partial answer** | Asked "cost and timeline", only answered cost | Add timeline section |
| **Topic drift** | Asked about Python, answered about general programming | Re-anchor to Python |
| **Missing comparisons** | Asked "A vs B", only described A | Add B section |
| **Buried lead** | Key answer is in paragraph 3 | Move direct answer to sentence 1 |
| **Confidence inflation** | "The answer is X" when source is weak | "According to [source], X" |
| **Silent omission** | Skipped a hard sub-question | Explicitly state "data not found for X" |
| **Stale assumption** | Using 2020 data for a 2025 question | Note date, search for newer if critical |

## Integration with Other Skills

Apply in sequence:
1. **Query decomposition** (`query_decomposition_and_multi_hop_research.md`) — plan the research
2. **Lateral reading** (`lateral_reading_and_source_credibility.md`) — validate sources
3. **Evidence triangulation** (`high_fidelity_evidence_mapping_and_triangulation.md`) — synthesize
4. **← THIS SKILL →** — verify completeness before finalizing
5. **Signal formatting** (`high_fidelity_synthesis_signal_formatting.md`) — format output

## Quick Checklist (Copy-Paste Ready)

```
Pre-finalization checklist:
[ ] Re-read original question — does my answer address it directly?
[ ] Every sub-question has an answer (or "not found: X" note)
[ ] All key statistics/facts have a source or are marked [unverified]
[ ] Opening sentence gives the direct answer (BLUF)
[ ] No sub-questions silently skipped
[ ] Confident tone only where evidence is strong
```

## Worked Example

**User asks:** "What are the main causes of deforestation in Brazil, and how much forest has been lost since 2015?"

**Before self-check (weak answer):**
> "Deforestation in Brazil is caused by agriculture and logging. The Amazon is a critical ecosystem."

**Self-check reveals:**
- ✗ Causes mentioned but not explained (agriculture = what type? scale?)
- ✗ "How much lost since 2015" is completely unanswered
- ✗ No sources cited

**After self-check (corrected answer):**
> "**Main causes** (Brazil): ~80% from cattle ranching and soy farming, with illegal logging, mining, and road construction contributing the rest [FAO, 2023]. **Forest loss since 2015**: Brazil lost approximately X million hectares of Amazon between 2015–2023, with peak deforestation in 2021 at Y,000 km² [INPE annual monitoring data]."

The corrected answer is shorter but directly addresses both parts of the question with sourced data.
