# Proposal #317: Resolve circular import

**Type:** code
**Created:** 2026-03-24T20:44:11.470300+00:00

## Description

Diagnosis: Circular import between knowledge_base/__init__.py and knowledge_base/vectorstore.py where vectorstore tries to import config from knowledge_base which is still being initialized.

Fix: Move the config import in vectorstore.py to be inside the KnowledgeStore class methods where it's actually needed, or restructure the imports to avoid the circular dependency. The config values should either be passed as parameters or imported from a separate config module that doesn't participate in the circular import chain.

## Files

None
