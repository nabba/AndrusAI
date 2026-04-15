# Proposal #554: Fix: coding:BadRequestError

**Type:** code
**Created:** 2026-04-03T06:30:03.953990+00:00

## Description

The error occurs because the model 'registry.ollama.ai/library/codestral:22b' does not support tools functionality. The fix is to either use a different model that supports tools or remove the tools parameter from the request.

## Files

None
