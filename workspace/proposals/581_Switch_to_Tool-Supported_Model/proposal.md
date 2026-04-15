# Proposal #581: Switch to Tool-Supported Model

**Type:** code
**Created:** 2026-04-08T20:58:35.382264+00:00

## Description

Diagnosis: The AI model `registry.ollama.ai/library/codestral:22b` does not support the use of tools, leading to a BadRequestError.

Fix: Replace the current model `registry.ollama.ai/library/codestral:22b` with a tool-supported model in the codebase. This change is necessary because the current model does not support the tools feature, which is required for the task execution.

## Files

None
