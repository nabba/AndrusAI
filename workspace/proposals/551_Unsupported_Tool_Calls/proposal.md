# Proposal #551: Unsupported Tool Calls

**Type:** code
**Created:** 2026-04-02T13:17:14.630181+00:00

## Description

Diagnosis: The Codestral model being used does not support tool calls, which is causing the BadRequestError.

Fix: Switch to a different model that supports tool calls or modify the code to avoid using tools with Codestral. The error occurs because the current model (codestral:22b) lacks tool-calling capabilities.

## Files

None
