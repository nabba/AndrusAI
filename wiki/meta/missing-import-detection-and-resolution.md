---
aliases:
- missing import detection and resolution
author: idle_scheduler.wiki_synthesis
confidence: medium
created_at: '2026-04-24T12:00:58Z'
date: '2026-04-24'
related: []
relationships: []
section: meta
source: workspace/skills/missing_import_detection_and_resolution.md
status: active
tags:
- self-improvement
- skills
- auto-synthesised
title: Missing Import Detection and Resolution
updated_at: '2026-04-24T12:00:58Z'
version: 1
---

# Missing Import Detection and Resolution

## Problem Pattern
`ImportError: cannot import name 'X' from 'module.path'`

The target module exists, but the specific name (constant, function, class) cannot be found.

## Diagnostic Checklist

1. **Check for file existence and structure issues**
   - Does the target file actually exist? (`FileNotFoundError` vs `ImportError`)
   - Is there a circular import causing partially initialized modules?
   - Are there typos in the import path or name?

2. **Verify the symbol definition**
   - Search the target module for the exact name (case-sensitive)
   - Check if it was recently renamed, moved, or deleted
   - Confirm it's defined at module level (not inside a function/class)

3. **Check for conditional/existence-based definitions**
   ```python
   # BAD: Symbol only exists conditionally
   if SOME_FLAG:
       MY_CONSTANT = 100
   # Import fails when flag is False
   
   # GOOD: Always define, even as placeholder
   MY_CONSTANT = get_config_value()  # or None, or a default
   ```

4. **Inspect circular import chains**
   - Module A imports from B, B imports from A → partially initialized
   - Solution: Restructure to break the cycle (see `circular_import_resolution_patterns` skill)

5. **Verify `__init__.py` exports**
   - Does `__init__.py` re-export the symbol via `from .module import name`?
   - Or should the importer use the direct module path instead?

## Resolution Strategies

### Strategy 1: Add Missing Definition (Simplest)
If the symbol should exist but doesn't, add it to the module:
```python
# In app/agents/commander/__init__.py or the actual module
_MAX_RESPONSE_LENGTH = 4096  # or appropriate value
```

### Strategy 2: Fix Import Path
If the symbol exists but in a different location:
```python
# Wrong:
from app.agents.commander import _MAX_RESPONSE_LENGTH
# Right:
from app.agents.commander.commander_agent import _MAX_RESPONSE_LENGTH
# Or fix __init__.py to re-export it
```

### Strategy 3: Guard Import with Fallback
When symbol might be missing in some deployments:
```python
try:
    from app.agents.commander import _MAX_RESPONSE_LENGTH
except ImportError:
    _MAX_RESPONSE_LENGTH = 4096  # sensible default
```

### Strategy 4: Restructure to Eliminate Circular Import
- Move shared constants to a separate `constants.py` or `config.py`
- Use lazy imports inside functions instead of module-level
- Pass needed values as parameters rather than importing

## Real Example from Error Journal

**Error:**
```
ImportError: cannot import name '_MAX_RESPONSE_LENGTH' from 'app.agents.commander'
```

**Likely fixes:**
1. `_MAX_RESPONSE_LENGTH` was removed/renamed in `app/agents/commander/__init__.py` → restore it
2. The constant exists but import path is wrong → check actual location
3. Circular import prevents full initialization → use Strategy 4

## Prevention

- Keep all module-level constants defined unconditionally
- Avoid complex conditional logic at import time
- Use a dedicated `config.py` or `constants.py` for shared values
- Run static analysis (pylint, pyflakes) to catch undefined names
