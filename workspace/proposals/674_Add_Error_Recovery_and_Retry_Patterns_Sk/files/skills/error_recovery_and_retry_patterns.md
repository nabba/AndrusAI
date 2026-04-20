# Error Recovery & Retry Patterns

## Problem
Tasks fail due to timeouts, model incompatibilities, API rate limits, and network issues. Without systematic recovery patterns, failures cascade and waste time.

## Known Failure Modes & Fixes

### 1. Model Tool-Calling Incompatibility
**Symptom**: `BadRequestError: model does not support tools`
**Fix**: Check model against known capability list before assigning tool-using tasks.
- Models WITHOUT tool support: codestral:22b, mistral-small:latest, llama2 variants
- Models WITH tool support: qwen2.5:72b, qwen2.5-coder:32b, mistral-small3.1:24b, deepseek-coder-v2
**Recovery**: Retry with a tool-capable model or reformulate task to avoid tool use.

### 2. Slow Research Tasks (>60s)
**Symptom**: Research tasks taking 98-165s+
**Fix**: 
- Set explicit depth limit (d=2 for simple lookups, d=3 for moderate, d=4 only for deep research)
- Use parallel web_search calls for independent sub-queries
- Cache intermediate results in team_memory for reuse

### 3. Future-Oriented / Speculative Queries
**Symptom**: Empty or failed outputs for predictions
**Fix**: 
- Redirect to searching for latest trends/reports/forecasts
- Explicitly state prediction limitations in output
- Add 30% time buffer for broader coverage
- Search for '[topic] trends 2026' or '[topic] forecast report' instead of speculative phrasing

### 4. Web Fetch Failures
**Symptom**: Empty content from web_fetch
**Retry Strategy**:
1. First attempt: `web_fetch(url)`
2. If empty: `browser_fetch(url, wait_selector='body')` (handles JS-rendered pages)
3. If still empty: `browser_screenshot(url)` to visually confirm content exists
4. Last resort: search for cached/alternative source of same content

### 5. General Retry Pattern
```
Attempt 1: Try primary method
Attempt 2: Try alternative method (different tool or approach)
Attempt 3: Degrade gracefully (partial result + explanation)
Final: Report failure with diagnostic info for human
```

### 6. Rate Limiting
- Space sequential API calls by 1-2 seconds
- Batch independent calls together in parallel
- If rate-limited, exponential backoff: 2s, 4s, 8s

## Prevention Checklist
- [ ] Validate model capabilities before task assignment
- [ ] Set appropriate depth/timeout for research tasks
- [ ] Check if query is speculative and adjust strategy
- [ ] Have fallback tool chain ready (web_fetch → browser_fetch → browser_screenshot)
- [ ] Store lessons learned in team_memory after novel failures
