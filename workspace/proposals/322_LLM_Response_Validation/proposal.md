# Proposal #322: LLM Response Validation

**Type:** code
**Created:** 2026-03-25T12:00:42.789809+00:00

## Description

Propose adding a simple wrapper function that checks for None/empty responses from LLM calls and implements a single retry with exponential backoff. This should handle the recurring ValueError cases while maintaining simplicity.

## Files

- `llm_wrapper.py`
