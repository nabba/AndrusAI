# Proposal #557: Unsupported Tools in Model

**Type:** code
**Created:** 2026-04-04T08:50:41.865382+00:00

## Description

Diagnosis: The AI model 'codestral:22b' does not support tools, causing a BadRequestError when attempting to use it with tool-enabled operations.

Fix: Replace 'codestral:22b' with a model that supports tools, or modify the code to avoid using tools with this model. This resolves the BadRequestError by ensuring compatibility between the model and the operations being performed.

## Files

None
