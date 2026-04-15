# Proposal #389: Retry Mechanism for LLM Responses

**Type:** code
**Created:** 2026-03-31T20:52:48.077390+00:00

## Description

Propose adding a validation wrapper around LLM responses to check for None/empty responses and handle OpenrouterException by retrying the LLM call up to 3 times. This will improve error handling and reduce the frequency of Invalid response from LLM call - None or empty and BadRequestError related to OpenrouterException.

## Files

- `llm_retry.py`
