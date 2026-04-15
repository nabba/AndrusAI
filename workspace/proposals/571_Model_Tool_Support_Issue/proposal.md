# Proposal #571: Model Tool Support Issue

**Type:** code
**Created:** 2026-04-06T00:09:54.050584+00:00

## Description

Diagnosis: The model 'registry.ollama.ai/library/codestral:22b' does not support tools, which is causing the BadRequestError.

Fix: Replace 'registry.ollama.ai/library/codestral:22b' with a model that supports tools or modify the code to avoid using tools with this model. This will resolve the BadRequestError as the current model lacks the necessary tool support.

## Files

None
