---
aliases:
- response synthesis optimization
author: idle_scheduler.wiki_synthesis
confidence: medium
created_at: '2026-04-21T16:22:03Z'
date: '2026-04-21'
related: []
relationships: []
section: meta
source: workspace/skills/response_synthesis_optimization.md
status: active
tags:
- self-improvement
- skills
- auto-synthesised
title: Response Synthesis Optimization
updated_at: '2026-04-21T16:22:03Z'
version: 1
---

# Response Synthesis Optimization

## Quick Reference
Techniques to improve output quality while reducing token usage and API calls.

## Key Patterns

### Semantic Deduplication
- Use 90% similarity threshold to eliminate near-duplicates
- "LLM agents" = "language model agents" - treat as equivalent
- One well-stated point beats three redundant restatements

### Clustered Synthesis
1. Group results by theme/topic
2. Summarize each cluster
3. Combine into unified response
4. Result: Better coherence, fewer hallucinations

### Progressive Refinement (for 5+ sources)
- Extract key facts from each source
- Identify agreements and contradictions
- Synthesize unified narrative
- Avoid "laundry list" responses

### Factuality Scoring
- Primary sources > secondary sources
- 3+ independent sources = high confidence
- Flag contradictions explicitly
- Never fabricate consensus

## Efficiency Rules
1. Check memory before searching
2. Store synthesized findings for reuse
3. Single synthesis pass > incremental processing
4. Batch process all results before responding

## Quality Targets
- Under 1000 words for Signal
- Structured with headers
- Attributed claims for controversial topics
- Contradictions acknowledged, not hidden

## Metric Impact
Applying these patterns should improve:
- Output Quality (currently 69%)
- Response time (currently 253s avg)
- Cost efficiency (currently $0.57 avg)
