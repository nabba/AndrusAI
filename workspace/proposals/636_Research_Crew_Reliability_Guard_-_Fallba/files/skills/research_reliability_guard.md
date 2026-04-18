# Research Reliability Guard

## Problem
Research crew frequently returns empty/failed output on simple queries. Over 60% of low-difficulty tasks fail.

## Root Causes
1. Simple factual questions get routed to web search unnecessarily
2. No retry logic when web search returns empty results
3. No fallback to LLM knowledge for trivial facts

## Solution: Three-Layer Reliability Pattern

### Layer 1: Query Classification
Before searching, classify the query:
- **Trivial factual** (capitals, dates, definitions): Answer from LLM knowledge first, verify with search only if uncertain
- **Current events/data**: Must use web search
- **Opinion/analysis**: Use LLM reasoning with optional search for supporting evidence

### Layer 2: Retry with Reformulation
If web search returns empty:
1. Retry with simplified/rephrased query
2. Try alternative search terms (synonyms, broader terms)
3. Maximum 2 retries before fallback

### Layer 3: Fallback Response
If all search attempts fail:
1. Provide LLM-knowledge answer with confidence caveat
2. Log the failure for later analysis
3. Never return empty output — always provide best-effort response

## Implementation Checklist
- [ ] Add output length check (< 10 chars = likely failure)
- [ ] Add query complexity classifier
- [ ] Implement retry loop with query reformulation
- [ ] Add fallback to direct LLM response
- [ ] Log all failures with query text for pattern analysis

## Expected Impact
Reduce research crew failure rate from ~60% to <10% on simple queries.