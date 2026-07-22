---
aliases:
- adaptive search depth and confidence gating
author: idle_scheduler.wiki_synthesis
confidence: medium
created_at: '2026-06-25T09:41:19Z'
date: '2026-06-25'
related: []
relationships: []
section: meta
source: workspace/skills/adaptive_search_depth_and_confidence_gating.md
status: active
tags:
- self-improvement
- skills
- auto-synthesised
title: Adaptive Search Depth and Confidence-Gated Research
updated_at: '2026-06-25T09:41:19Z'
version: 1
---

# Adaptive Search Depth and Confidence-Gated Research

*kb: episteme | id: skill_adaptive_search_depth_confidence | status: active | created: 2026-05-27*
*based on: Agentic Uncertainty Quantification (AUQ), arxiv:2601.15703, 2025*

## Purpose
Control **how many searches to perform** based on a running confidence score. Most research failures happen for one of two reasons:
1. **Too shallow**: Stopped searching after one result, missed critical facts
2. **Too deep**: Over-searched, wasted time, never synthesized

This skill gives you a principled stopping criterion: keep searching when confidence is LOW, stop when confidence is HIGH.

## The Core Principle (AUQ System 2)

> **"Trigger deeper investigation only when confidence drops below threshold."**
> — Agentic Uncertainty Quantification (Zhang et al., 2025)

Verbalized confidence is a reliable control signal. If you can articulate *why* you're uncertain, you should act on that uncertainty. If you cannot find a specific gap, you have enough to proceed.

---

## Confidence Scoring During Research

After each search/retrieval step, assign a confidence score to your current understanding:

| Score | Meaning | What to do |
|-------|---------|-----------|
| **HIGH** (≥0.80) | 3+ independent Tier 1-3 sources agree; no contradictions | **STOP searching. Synthesize.** |
| **MEDIUM** (0.50–0.79) | 1-2 good sources, OR 1 conflicting claim | **Run 1-2 targeted follow-up searches** for the specific gap |
| **LOW** (<0.50) | Only weak sources, major contradictions, or critical facts missing | **Decompose and search systematically** — do not synthesize yet |

**How to assign your score:**
Ask: "If I had to answer RIGHT NOW, how confident am I that this answer is accurate and complete?"
- No major gaps → ≥0.80
- One specific unknown → 0.50–0.79
- Multiple unknowns or a contradiction → <0.50

---

## The Adaptive Search Loop

```
START research task
  │
  ▼
1. Run initial search (broad query)
  │
  ▼
2. Assess confidence score (HIGH / MEDIUM / LOW)
  │
  ├── HIGH → STOP. Synthesize and deliver.
  │
  ├── MEDIUM → Identify the SPECIFIC gap causing uncertainty.
  │             Run 1-2 targeted searches to fill it.
  │             Re-assess → if HIGH, stop. If still MEDIUM after 2 attempts → flag.
  │
  └── LOW → Decompose into sub-queries (see query_decomposition skill).
             Search each sub-query. Re-assess per sub-query.
             When ALL sub-queries reach MEDIUM or HIGH → synthesize.
             Hard stop at 6 total searches (diminishing returns beyond this).
```

**Hard stop rule**: Never run more than 6 search calls on a single research task unless explicitly asked for "deep research." Past 6 searches with no confidence improvement = the information may not be publicly available.

---

## The Confidence Self-Assessment Template

After collecting initial results, explicitly write this assessment before deciding what to do next:

```
CONFIDENCE ASSESSMENT:
Score: [HIGH/MEDIUM/LOW] ([0.0-1.0])
What I know with confidence: [list 2-3 key established facts]
Known gaps:
  - [Gap 1: what specific fact is missing?]
  - [Gap 2: what claim is contradicted or unverified?]
Next action: [STOP and synthesize | Search for: "[specific gap query]"]
```

This explicit verbalization is key — research shows agents that articulate uncertainty before acting make better search decisions than those that proceed on implicit instinct.

---

## Confidence Triggers (When to Keep Searching)

**Always keep searching if you discover:**
- A critical claim is contradicted by two or more sources
- A numeric fact (date, count, percentage) has two different values in different sources
- The user's question has a sub-part you haven't addressed at all
- Your primary source is Tier 4 or 5 (blog, unknown site) — upgrade to Tier 1-2

**Stop searching and flag instead if:**
- 3+ searches on the same gap return no additional information
- The topic requires live/real-time data that static search cannot provide
- You are past 6 total searches with diminishing information gain

---

## Anti-Patterns to Avoid

| Anti-Pattern | Problem | Fix |
|-------------|---------|-----|
| **One-shot synthesis** | Synthesize after 1 search regardless of confidence | Always assess confidence before synthesizing |
| **Aimless re-searching** | Keep searching without a specific gap in mind | Identify the exact missing fact before searching again |
| **Confidence inflation** | Say "HIGH" to avoid more searches | Be honest: if you have doubts, they're probably real |
| **Rabbit-hole searching** | Follow interesting tangents beyond the original question | Ask: "Does this address the original question?" If no, stop |
| **Over-decomposition** | Decompose simple factual questions into 5 sub-queries | Apply decomposition only for multi-part or complex tasks |

---

## Integration with Other Research Skills

Apply in this order:

1. **Decompose the query** (`query_decomposition_and_multi_hop_research.md`) — only if complex
2. **Run initial search(es)**
3. **← THIS SKILL →** — assess confidence, decide whether to search more or stop
4. **Validate source credibility** (`lateral_reading_and_source_credibility.md`) — for key claims
5. **Triangulate evidence** (`high_fidelity_evidence_mapping_and_triangulation.md`) — for HIGH-stakes claims
6. **Verify completeness** (`answer_completeness_verification.md`) — final check before output
7. **Format output** (`high_fidelity_synthesis_signal_formatting.md`) — Signal-optimized delivery

---

## Decision Examples

**Example 1: Simple factual question**
> "What is the capital of Australia?"

- Initial search returns "Canberra" from 2 sources
- Confidence: HIGH (simple, well-known fact, multiple sources agree)
- **Action: STOP. Answer: Canberra.**

---

**Example 2: Multi-part research question**
> "What caused the 2023 banking crisis and what were the long-term effects?"

Initial search returns info on SVB collapse but nothing on long-term effects.

```
CONFIDENCE ASSESSMENT:
Score: LOW (0.35)
What I know: SVB collapsed in March 2023 due to duration mismatch and bank run
Known gaps:
  - "long-term effects" not addressed at all
  - Only 1 source on causes; need independent confirmation
Next action: Search for "2023 banking crisis long term economic effects 2024"
```

After second search: long-term effects found from 2 sources.

```
CONFIDENCE ASSESSMENT:
Score: MEDIUM (0.65)
What I know: Causes confirmed; regulatory reforms passed; contagion limited
Known gaps:
  - Exact GDP impact unclear — sources give different estimates
Next action: Search for "2023 banking crisis GDP impact IMF study"
```

After third search: IMF data found.

```
CONFIDENCE ASSESSMENT:
Score: HIGH (0.85)
Action: STOP. Synthesize with 3+ sources on both causes and effects.
```

---

**Example 3: Conflicting information**
> "How many people died in the 2024 earthquake in Morocco?"

Search returns two different numbers: 2,900 and 3,200.

```
CONFIDENCE ASSESSMENT:
Score: MEDIUM (0.60)
Known gaps:
  - Death toll conflict: 2,900 (Reuters, initial report) vs 3,200 (AP, later update)
Next action: Search "Morocco earthquake 2024 final death toll" — find the most recent official figure
```

After follow-up: AP's 3,200 is the final official count from Moroccan authorities.

```
CONFIDENCE ASSESSMENT:
Score: HIGH (0.80)
Action: STOP. Use 3,200 with note that initial reports cited 2,900.
```

---

## Quick Reference Card

```
Before synthesizing, ask:
1. Score my confidence: HIGH / MEDIUM / LOW
2. If MEDIUM/LOW: What is the ONE SPECIFIC GAP I need to fill?
3. Search for exactly that gap (not a broad re-search)
4. Re-assess confidence after each search
5. Stop at HIGH confidence OR after 6 searches total
6. If still below HIGH after 6 searches → flag uncertainty in answer

Gold rule: Each search must target a named, specific gap.
           Never search again without first naming what you're looking for.
```
