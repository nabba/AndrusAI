# Proposal #566: Switch to a Model that Supports Tools

**Type:** code
**Created:** 2026-04-05T00:01:48.164935+00:00

## Description

Diagnosis: The model 'registry.ollama.ai/library/codestral:22b' does not support the use of tools, which is causing the BadRequestError.

Fix: Replace the current model with one that supports tool usage, such as OpenAI's GPT-4 Turbo, to avoid the BadRequestError. This involves updating the code to specify a compatible model in the API request.

## Files

None
