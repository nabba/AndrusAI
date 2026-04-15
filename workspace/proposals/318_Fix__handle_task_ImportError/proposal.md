# Proposal #318: Fix: handle_task:ImportError

**Type:** code
**Created:** 2026-03-24T20:44:32.187295+00:00

## Description

The root cause is a circular import where `app/knowledge_base/__init__.py` imports `KnowledgeStore` from `vectorstore.py`, and `vectorstore.py` imports `config` from `app/knowledge_base`. To fix this, move the `config` import in `vectorstore.py` to after the `KnowledgeStore` class definition or at the method level where it's needed.

## Files

None
