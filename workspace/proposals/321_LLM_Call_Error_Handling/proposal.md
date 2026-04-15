# Proposal #321: LLM Call Error Handling

**Type:** code
**Created:** 2026-03-25T12:00:19.713347+00:00

## Description

Add a wrapper function around LLM calls that: 1) Validates responses aren't None/empty, 2) Retries on BadRequestError (with exponential backoff), 3) Has a maximum retry limit (3 attempts), 4) Returns a clear error message if all retries fail. This should handle the recurring ValueError and BadRequestError patterns we're seeing.

## Files

- `llm_wrapper.py`
