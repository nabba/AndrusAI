# Proposal #285: Fix circular import

**Type:** code
**Created:** 2026-03-24T17:40:35.766044+00:00

## Description

Diagnosis: Circular import between `app.knowledge_base.__init__` and `app.knowledge_base.vectorstore` due to `config` import.

Fix: Move `config` import from `vectorstore.py` to a separate configuration module or use lazy imports to break the circular dependency.

## Files

None
