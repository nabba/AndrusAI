# Proposal #777: Fix knowledge_base circular import and missing config module

**Type:** code  
**Created:** 2026-04-24T13:08:09.434056+00:00  

## Why this is useful

The error journal shows 7+ ImportError occurrences in handle_task crew caused by circular import in app.knowledge_base and missing config.py file. This fix: (1) creates app/knowledge_base/config.py with proper configuration values, (2) restructures vectorstore.py to import config at module level without circular dependency, (3) ensures __init__.py only exposes the KnowledgeStore class. This is a targeted fix for a diagnosed but unfixed error pattern.

## What will change

- Modifies `app/knowledge_base/__init__.py`
- Modifies `app/knowledge_base/config.py`
- Modifies `app/knowledge_base/vectorstore.py`

## Potential risks to other subsystems

- Knowledge base / RAG: KB search results shown to agents during tasks
- Requires `docker compose up -d --build gateway` to take effect

## Files touched

- `app/knowledge_base/__init__.py`
- `app/knowledge_base/config.py`
- `app/knowledge_base/vectorstore.py`

## Original description

The error journal shows 7+ ImportError occurrences in handle_task crew caused by circular import in app.knowledge_base and missing config.py file. This fix: (1) creates app/knowledge_base/config.py with proper configuration values, (2) restructures vectorstore.py to import config at module level without circular dependency, (3) ensures __init__.py only exposes the KnowledgeStore class. This is a targeted fix for a diagnosed but unfixed error pattern.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 777` / `reject 777` via Signal.
