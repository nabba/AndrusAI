# Proposal #772: OpenRouter Credit Exhaustion Handling

**Type:** code  
**Created:** 2026-04-24T07:13:09.338312+00:00  

## Why this is useful

Enhanced error handling reduces repeat failures due to credit exhaustion, allowing efficient task resumes.

## What will change

Improves error handling and reduces service latency through increased redundancy and parallel crew management.

## Potential risks to other subsystems

Reduces credit exhaustion cascades and potential backfires across multiple crews, causing performance degradation.

## Files touched

- `skills/openrouter_credit_management.md`
- `api_client.py (or handle_task.py)`

## Original description

The error journal shows multiple 402 'Insufficient credits' errors from OpenRouter API across coding, research, and pim crews, leading to cascading failures (BadRequestError, RuntimeError). Current retry logic treats these as generic failures, causing wasted API calls. This fix adds:

1. Specific detection of 402 status codes in the API client
2. Exponential backoff with jitter for credit-exhausted retries (30s → 5min)
3. Temporary task throttling when credits are low (reduce parallel crew workers)
4. Team memory alert to prevent further task submissions until credits replenished
5. Graceful degradation: shift to cheaper models or skip non-critical tasks

Changes will be made to the central LLM API caller/wrapper (likely handle_task.py or api_client.py) with minimal complexity by extending existing error handling.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 772` / `reject 772` via Signal.
