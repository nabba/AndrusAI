# Proposal #188: LLM Call Validation and Retry

**Type:** code
**Created:** 2026-03-24T08:53:37.698737+00:00

## Description

Propose enhancing LLM call handling by adding validation checks and retry logic. Specifically, validate the response for None/empty strings before processing, and implement exponential backoff retries for BadRequestErrors from Openrouter.

## Files

- `llm_handler.py`
