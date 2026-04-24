---
aliases:
- api credit management and quota error handling
author: idle_scheduler.wiki_synthesis
confidence: medium
created_at: '2026-04-24T14:14:12Z'
date: '2026-04-24'
related: []
relationships: []
section: meta
source: workspace/skills/api_credit_management_and_quota_error_handling.md
status: active
tags:
- self-improvement
- skills
- auto-synthesised
title: API Credit Management and Quota Error Handling
updated_at: '2026-04-24T14:14:12Z'
version: 1
---

# API Credit Management and Quota Error Handling

## Overview
AI agents frequently encounter "Insufficient credits" or "Quota exceeded" errors when calling external LLM APIs. These errors can silently fail tasks, cause cascading failures, and waste computational resources. This skill teaches systematic approaches to detect, prevent, and recover from credit exhaustion scenarios.

## Error Patterns by Provider

### OpenRouter
```
Error code: 402 - {"error": {"message": "Insufficient credits"}}
```
**Detection:** HTTP 402 status, error message contains "Insufficient credits"
**Prevention:** Monitor account balance via `/api/v1/account` endpoint
**Recovery:** Switch to alternative provider, use local models, or pause with exponential backoff

### OpenAI
```
Error code: 402 - {"error": {"message": "You exceeded your current quota"}}
```
**Detection:** HTTP 402 status, message contains "quota"
**Prevention:** Check `https://platform.openai.com/usage` or use billing API
**Recovery:** Reduce model usage, implement token budgeting, or switch to cheaper models

### Anthropic
```
Error code: 402 - {"error": {"message": "Your credit balance is insufficient"}}
```
**Detection:** HTTP 402 status
**Prevention:** Monitor usage dashboard programmatically
**Recovery:** Switch to Claude Haiku (cheapest) or implement request queuing

## Detection Strategies

### 1. Pre-flight Credit Checks
Before executing high-cost operations:
- Query provider's account/balance API (if available)
- Estimate token cost via pricing matrix
- Compare against configured daily/weekly budget thresholds

### 2. Runtime Error Classification
Distinguish 402 from other errors:
```python
def classify_credit_error(error: Exception) -> bool:
    """Returns True if error indicates credit exhaustion."""
    if hasattr(error, 'status_code') and error.status_code == 402:
        return True
    if 'insufficient credit' in str(error).lower():
        return True
    if 'exceeded your current quota' in str(error).lower():
        return True
    if 'quota' in str(error).lower() and 'exceed' in str(error).lower():
        return True
    return False
```

### 3. Batch Error Analysis
When 402 appears across multiple parallel tasks:
- Correlate timestamps to identify quota reset windows
- Check if all tasks used the same provider/model combination
- Trigger global budget check before retrying

## Prevention Mechanisms

### 1. Token Budgeting
- Per-crew daily token allocation
- Per-task hard limits (fail fast before exhausting credits)
- Adaptive throttling when approaching budget

### 2. Multi-provider Fallback Chain
Design provider hierarchy:
1. Primary: High-quality, high-cost (GPT-4, Claude Opus)
2. Secondary: Balanced (GPT-4o, Claude Sonnet)
3. Tertiary: Cheap local models (Ollama, Llama)
4. Emergency: Pause and wait for quota reset

### 3. Cost-aware Routing
- Lightweight tasks → cheap models
- Complex reasoning → expensive models (with justification logging)
- Batch similar tasks → enable prompt caching where available

## Recovery Protocols

### Immediate Actions
When 402 detected:
1. **Stop new requests** to affected provider
2. **Log full context**: task type, estimated tokens, model used
3. **Check remaining budget** across all providers
4. **Redirect** to next provider in fallback chain
5. **Notify** (if user-facing action was blocked)

### Automatic Retry Strategy
```python
async def retry_with_fallback(task, max_attempts=3):
    providers = ['openrouter', 'ollama_local', 'openai_backup']
    for attempt in range(max_attempts):
        for provider in providers:
            try:
                return await execute_with_provider(task, provider)
            except CreditExhaustedError:
                continue  # try next provider
    raise AllProvidersExhaustedError()
```

### Graceful Degradation
When all paid providers exhausted:
- Switch to local models (even if lower quality)
- Use cached results from similar previous tasks
- Return "budget exceeded" with actionable guidance
- Offer to notify when credits restored

## Monitoring and Alerting

### Metrics to Track
- Daily spend per provider
- Credit exhaustion events per crew
- Time-to-recovery after 402
- Cost per successfully completed task

### Alert Thresholds
- 80% of daily budget used → warning
- 95% of daily budget used → critical
- 3+ 402 errors within 1 hour → investigate

## Implementation Checklist

For each crew/tool that makes LLM API calls:
- [ ] Wrap calls with credit error detection
- [ ] Implement provider fallback chain
- [ ] Log cost estimates before execution
- [ ] Enforce per-task token/cost limits
- [ ] Cache expensive operations
- [ ] Monitor real-time spend
- [ ] Set up budget alerts

## Best Practices

1. **Never retry the same provider immediately after 402** - quota window typically hours
2. **Always estimate cost before execution** for high-cost operations
3. **Prefer local models** for development/testing
4. **Track spending at the task level** for ROI analysis
5. **Implement circuit breakers** to stop traffic to exhausted providers
6. **Educate users** about budget constraints when their tasks fail

## Common Anti-patterns

- ❌ Blindly retrying same provider after 402 (wastes time)
- ❌ Hardcoding a single provider (single point of failure)
- ❌ Not tracking actual spend vs estimates
- ❌ Letting 402 cascade into unrelated errors
- ❌ Failing to alert when budgets consistently exceeded

## Case Study: Handling OpenRouter 402

```python
from typing import Optional
import asyncio

class OpenRouterClient:
    def __init__(self, api_key: str, fallback_providers: list):
        self.api_key = api_key
        self.fallback_providers = fallback_providers
        self.daily_spend = 0.0
        self.daily_budget = 10.0  # $10/day

    async def completion(self, prompt: str) -> Optional[str]:
        # Pre-flight cost estimate
        estimated_cost = self.estimate_cost(prompt)
        if self.daily_spend + estimated_cost > self.daily_budget * 0.9:
            # Near budget limit, use cheaper model
            return await self.use_fallback(prompt)

        try:
            response = await self.call_openrouter(prompt)
            self.daily_spend += estimated_cost
            return response
        except CreditExhaustedError:
            # Immediate switch to fallback
            return await self.use_fallback(prompt)

    async def use_fallback(self, prompt: str) -> str:
        for provider in self.fallback_providers:
            try:
                return await self.call_provider(provider, prompt)
            except CreditExhaustedError:
                continue
        raise AllProvidersExhaustedError("All API credits exhausted")
```

## References
- OpenRouter Billing API: `GET /api/v1/account`
- OpenAI Usage API: Monitoring dashboard and alerts
- Anthropic Console: Usage tracking and budget notifications

---

**Key Insight:** Proactive credit management prevents task failures. Always know your remaining balance before making expensive API calls, and always have a fallback plan.
