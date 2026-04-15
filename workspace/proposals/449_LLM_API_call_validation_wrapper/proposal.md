# Proposal #449: LLM API call validation wrapper

**Type:** code
**Created:** 2026-04-01T00:25:58.276959+00:00

## Description

Propose adding a wrapper function around LLM API calls that includes: 1) Input validation, 2) Response validation (checking for None/empty), 3) Exponential backoff retry for BadRequestErrors, and 4) Clear error messaging. This addresses the recurring ValueError and BadRequestError patterns seen in the error journal.

## Files

- `llm_wrapper.py`
