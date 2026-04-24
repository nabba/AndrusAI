# OpenRouter Credit Exhaustion Management

## Problem
Recurring `402 Insufficient credits` errors from the OpenRouter API cause:
- Task failures across all crews (coding, research, pim, writing)
- Cascading errors: 402 → BadRequestError / RuntimeError
- Wasted API calls and user frustration
- 16+ BadRequestError occurrences linked to this root cause

## Detection
- Monitor HTTP 402 status codes from OpenRouter responses
- Pattern: `{'error': {'message': 'Insufficient credits', 'code': 402}}`

## Response Strategy
1. **Immediate**: Stop submitting new parallel tasks to affected crew
2. **Backoff**: Exponential wait with jitter (30s → 60s → 120s → 300s)
3. **Throttle**: Reduce worker count to 1 for the affected crew
4. **Alert**: Write to team memory with timestamp and estimated recovery time
5. **Degrade**: Switch to cheapest available model for critical tasks only

## Recovery
- Credits replenished → clear team memory alert
- Gradually restore parallel workers over 2-3 successful tasks
- Log credit exhaustion events for trend analysis

## Cost
- Zero additional API calls (uses existing error responses)
- Reduces wasted spend by preventing futile retries
- Improves user experience with graceful degradation
