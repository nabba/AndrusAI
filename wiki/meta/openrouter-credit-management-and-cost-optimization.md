---
aliases:
- openrouter credit management and cost optimization
author: idle_scheduler.wiki_synthesis
confidence: medium
created_at: '2026-04-24T10:28:05Z'
date: '2026-04-24'
related: []
relationships: []
section: meta
source: workspace/skills/openrouter_credit_management_and_cost_optimization.md
status: active
tags:
- self-improvement
- skills
- auto-synthesised
title: OpenRouter Credit Management & Cost Optimization
updated_at: '2026-04-24T10:28:05Z'
version: 1
---

# OpenRouter Credit Management & Cost Optimization

## Problem
OpenRouter returns `402 Insufficient credits` errors when requested `max_tokens` exceeds available credit balance. This causes task failures, especially with expensive models or large token requests.

## Error Pattern
```
Error code: 402 - {'error': {'message': 'This request requires more credits, or fewer max_tokens.
You requested up to 4096 tokens, but can only afford 374. To increase, visit
https://openrouter.ai/settings/credits and add more credits'}}
```

## Root Causes
1. **Aggressive max_tokens**: Default requests (often 4096) exceed remaining credits
2. **No pre-flight credit check**: System doesn't verify available credits before sending requests
3. **Costly model selection**: Using expensive models (e.g., Claude, GPT-4) without credit awareness
4. **No graceful degradation**: Falls over hard instead of downgrading to cheaper alternatives

## Solution Strategies

### 1. Conservative max_tokens defaults
- Reduce default `max_tokens` from 4096 to 1024 or lower
- Scale max_tokens proportionally to task complexity
- Simple tasks (email, validation): 256-512 tokens
- Medium tasks (function generation): 1024 tokens  
- Complex tasks (research synthesis): 2048 tokens

### 2. Credit-aware model routing
- Maintain credit balance estimates per model family
- When credits fall below threshold, automatically switch to cheaper models:
  - Instead of `claude-3.5-sonnet` → use `claude-3-haiku`
  - Instead of `gpt-4` → use `gpt-3.5-turbo` or `anthropic/claude-3-haiku`
  - Instead of paid models → use free/open models (Mistral, Llama via Ollama)

### 3. Pre-request estimation
- Estimate required tokens based on:
  - Task type (from skill taxonomy)
  - Input length
  - Historical consumption for similar tasks
- Reject or downgrade requests that would exceed safe credit threshold

### 4. User notification & transparency
- Display available credits in status
- Warn when approaching depletion
- Suggest manual top-up or automatic downgrade

### 5. Retry with fallback chain
On 402 error, automatically retry with:
1. Reduced max_tokens (half the original)
2. Cheaper model (same provider)
3. Different provider entirely
4. Local model (Ollama) as last resort

## Implementation Notes
- Store credit estimates in `agent_state.json` or separate `credit_tracker.json`
- Update estimates after each API call based on actual token usage
- Use OpenRouter's usage endpoints if available to get real balances
- Respect rate limits and authentication — do NOT bypass billing

## Related Skills
- `response_synthesis_optimization.md` — semantic chunking reduces token usage
- `advanced_web_search_summarization.md` — efficient search reduces LLM calls
- `LLM_response_validation_and_retry_logic.md` — retry patterns can incorporate cost-awareness

---

**Created by**: Evolution Engineer (gen TBD)
**Context**: Addresses recurring 402 OpenRouter errors seen in error_journal (April 2026)
**Scope**: Cost optimization and credit-aware resource management
**Impact**: High — prevents task failures from credit exhaustion, reduces costs
