# Proposal #577: Unsupported Model Tool Interaction

**Type:** code
**Created:** 2026-04-07T00:38:42.289768+00:00

## Description

Diagnosis: The model 'registry.ollama.ai/library/codestral:22b' does not support tools, causing a BadRequestError.

Fix: Switch to a model that supports tools or modify the code to avoid using tools with 'registry.ollama.ai/library/codestral:22b'. This model lacks the capability to handle tool-based interactions, leading to the error.

## Files

None
