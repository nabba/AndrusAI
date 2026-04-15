# Proposal #385: Add LLM Response Validation and Retry Logic

**Type:** code
**Created:** 2026-03-31T00:01:54.866007+00:00

## Description

Implement code changes to validate LLM responses and retry on specific errors. Modify the LLM call handler to check for None/empty responses and Openrouter exceptions. Add retry logic with exponential backoff for these specific cases. This should reduce the ValueError and BadRequestError occurrences.

## Files

- `llm_handler.py`
