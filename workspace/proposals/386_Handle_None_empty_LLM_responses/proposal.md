# Proposal #386: Handle None/empty LLM responses

**Type:** code
**Created:** 2026-03-31T00:07:19.199093+00:00

## Description

Propose adding explicit validation checks in the handle_task function to detect None or empty LLM responses and implement a retry mechanism. The change would wrap the LLM call in a try-except block, validate the response isn't None or empty, and retry up to 3 times if invalid. This should reduce the frequency of ValueError exceptions.

## Files

- `handle_task.py`
