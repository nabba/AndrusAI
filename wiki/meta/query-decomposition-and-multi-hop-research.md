---
aliases:
- query decomposition and multi hop research
author: idle_scheduler.wiki_synthesis
confidence: medium
created_at: '2026-06-25T09:43:55Z'
date: '2026-06-25'
related: []
relationships: []
section: meta
source: workspace/skills/query_decomposition_and_multi_hop_research.md
status: active
tags:
- self-improvement
- skills
- auto-synthesised
title: Query Decomposition and Multi-Hop Research Strategy
updated_at: '2026-06-25T09:43:55Z'
version: 1
---

# Query Decomposition and Multi-Hop Research Strategy

*kb: episteme | id: skill_query_decomposition_multi_hop | status: active | created: 2026-05-23*

## Purpose
Transform a single complex user question into a set of targeted sub-queries that can be independently searched and recombined into a high-quality, comprehensive answer. This directly improves output quality by ensuring no key facets are missed.

## When to Apply
Use query decomposition whenever a task:
- Involves **comparison** ("Which is better, X or Y?")
- Requires **multiple data types** (e.g., financial + technical + historical)
- Spans **multiple entities** (two companies, two countries, two products)
- Has **causal depth** ("Why did X happen and what are the effects?")
- Involves **temporal reasoning** ("How has X changed over time?")

## The Decomposition Method

### Step 1: Identify the Query Type
| Type | Example | Strategy |
|------|---------|----------|
| Comparison | "Is Go faster than Python?" | Decompose by entity, then compare |
| Causal chain | "Why did SVB collapse?" | Decompose into cause, mechanism, effect |
| Multi-aspect | "Analyze Tesla's competitive position" | Decompose into product, finance, market, competition |
| Temporal | "How has deforestation changed since 2010?" | Decompose by time slice or metric |
| Factual lookup | "What is the capital of France?" | No decomposition needed |

### Step 2: Generate Sub-Queries (3–5 max)
For each complex query, generate independent sub-questions that together cover the full answer space.

**Example — Complex Query:** "Should I use PostgreSQL or MongoDB for my e-commerce platform?"

Sub-queries:
1. "What are PostgreSQL's strengths and weaknesses for e-commerce workloads?"
2. "What are MongoDB's strengths and weaknesses for e-commerce workloads?"
3. "What data consistency guarantees does each database provide for transactions?"
4. "What is the performance profile of each database at scale?"
5. "Which database is preferred in production e-commerce companies in 2025?"

### Step 3: Execute Searches Independently
Search each sub-query separately. This prevents one dominant source from overshadowing specific facts.

**Anti-pattern:** Searching "PostgreSQL vs MongoDB e-commerce" (returns opinion articles)
**Better:** Search each sub-query individually (returns specific technical documentation and benchmarks)

### Step 4: Synthesize with Claim-Source Binding
After collecting answers to each sub-query, bind every factual claim to its source:
- Format: **[Claim]** → [Source/Evidence]
- Conflicting claims → flag explicitly, do NOT silently pick one

### Step 5: Reconstruct the Answer
Combine sub-answers into a unified response using the BLUF format:
1. **Direct answer** (addresses the top-level question)
2. **Supporting sub-findings** (one bullet per sub-query conclusion)
3. **Confidence / caveats** (note any gaps or conflicts found)

## Worked Example

**User asks:** "What is the economic impact of deforestation in Brazil since 2015?"

**Naive approach:** Single search "economic impact deforestation Brazil" → gets generalist articles

**Decomposed approach:**
| Sub-query | What it yields |
|-----------|---------------|
| "Brazil deforestation rate hectares lost 2015–2024 data" | Quantitative loss data |
| "Economic value of Brazilian Amazon timber and agriculture sector" | Revenue side |
| "Carbon credit market value Amazon deforestation Brazil" | Climate finance angle |
| "GDP impact deforestation Brazil economic studies" | Macro-economic studies |
| "Brazil government fines penalties illegal deforestation 2015–2024" | Regulatory/cost side |

**Result:** 5 targeted searches that together build a complete economic picture rather than a single high-level article.

## Search Query Construction Rules
- **Specificity wins**: Include year ranges, country names, specific metrics
- **Avoid compound questions in a single search**: one concept per query
- **Prefer exact terminology**: Use domain-specific terms, not layperson synonyms
- **Anchor with data type**: Prefix with "statistics", "study", "report", "benchmark" when seeking factual data

## Multi-Hop Research Chain
When an answer from one sub-query opens new questions, follow the chain:
```
Initial query → Sub-query A → Finding A → Deeper query A1 (if needed)
                            → Finding A2 (contradicts B) → Tie-breaker query
             → Sub-query B → Finding B
             → Sub-query C → Finding C
                           ↓
                   Synthesize A + B + C → Final answer
```
Stop when: all original sub-queries are answered OR diminishing returns (3+ searches yielding no new facts).

## Quality Checklist Before Final Output
- [ ] Every claim in the response has a source
- [ ] Comparison questions address ALL sides (not just one)
- [ ] Temporal questions include at least start + end data points
- [ ] Contradictions are flagged, not hidden
- [ ] No hallucinated statistics (if no source found, say "data not found")
