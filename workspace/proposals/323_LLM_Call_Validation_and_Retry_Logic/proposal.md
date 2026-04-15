# Proposal #323: LLM Call Validation and Retry Logic

**Type:** code
**Created:** 2026-03-25T12:00:52.846818+00:00

## Description

Add validation checks before and after LLM calls to ensure responses are valid and non-empty. Implement a retry mechanism for BadRequestErrors with exponential backoff. This will improve error handling and recovery.

## Files

- `llm_call_handler.py`
