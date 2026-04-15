# Proposal #319: Fix Circular Import in Knowledge Base

**Type:** code
**Created:** 2026-03-24T20:47:41.500531+00:00

## Description

Diagnosis: Circular import between 'app.knowledge_base.vectorstore' and 'app.knowledge_base' due to mutual dependency on 'config'.

Fix: Move the 'config' import from 'app.knowledge_base.vectorstore' to an independent module to break the circular dependency.

## Files

None
