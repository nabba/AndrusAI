---
aliases:
- handle task circular import immediate fix
author: idle_scheduler.wiki_synthesis
confidence: medium
created_at: '2026-04-22T14:16:55Z'
date: '2026-04-22'
related: []
relationships: []
section: meta
source: workspace/skills/handle_task_circular_import_immediate_fix.md
status: active
tags:
- self-improvement
- skills
- auto-synthesised
title: Handling handle_task.py Circular Import Error
updated_at: '2026-04-22T14:16:55Z'
version: 1
---

# Handling handle_task.py Circular Import Error

*kb: episteme | id: skill_episteme_handle_task_circular_import_fix | status: active | usage: 0 | created: 2026-04-20T00:00:00+00:00*

# Problem Statement

The error pattern `handle_task:ImportError` appears 7 times in the error journal. This indicates a circular import in the `handle_task.py` module, likely involving:

- `handle_task.py` importing from `crew_shared.py` or similar shared modules
- Those shared modules importing back from `handle_task.py`
- The cycle causes `ImportError` or `AttributeError` at module load time

# Immediate Diagnostic Steps

1. **Examine the import structure** in these key files:
   - `handle_task.py` (primary source of errors)
   - `crew_shared.py` (likely shared utility module)
   - Any module imported by both

2. **Look for module-level imports** like:
   ```python
   # handle_task.py
   from crew_shared import validate_output, process_result  # ← potential cycle start
   
   # crew_shared.py
   from handle_task import some_function  # ← cycle closure
   ```

3. **Check the exact error message** from logs:
   ```
   ImportError: cannot import name 'X' from 'handle_task' (unknown location)
   ```
   The missing name `X` tells you what's being cyclically imported.

# Three Most Likely Scenarios

## Scenario 1: Task Handler ↔ Shared Utilities Mutual Import

**Pattern**: `handle_task.py` imports `crew_shared.py` for validation/processing helpers, and `crew_shared.py` imports back from `handle_task.py` for task-specific logic.

**Fix**: Extract the `crew_shared → handle_task` dependency into a new shared module.

```python
# Before:
# crew_shared.py
from handle_task import TASK_REGISTRY  # ← creates cycle

# handle_task.py
from crew_shared import validate_output

# After:
# task_registry.py (new file - no dependencies on either)
TASK_REGISTRY = {}

# crew_shared.py
# removed: from handle_task import TASK_REGISTRY
# use: from task_registry import TASK_REGISTRY

# handle_task.py
from crew_shared import validate_output
from task_registry import TASK_REGISTRY
```

## Scenario 2: Handler ↔ Agent/Tool Definition Mutual Import

**Pattern**: `handle_task.py` imports agent or tool definitions, and those modules import handler functions for registration.

**Fix**: Use lazy imports inside functions rather than module-level imports.

```python
# handle_task.py - BEFORE (causes cycle)
from agents.coding_agent import CodingAgent
from tools.code_tools import execute_code

def handle_coding_task(task_data):
    agent = CodingAgent()
    return execute_code(agent, task_data)

# handle_task.py - AFTER (breaks cycle)
def handle_coding_task(task_data):
    # Lazy import - only loads when function is called
    from agents.coding_agent import CodingAgent
    from tools.code_tools import execute_code
    
    agent = CodingAgent()
    return execute_code(agent, task_data)
```

## Scenario 3: Handler ↔ Model/DB Mutual Import

**Pattern**: `handle_task.py` imports database models or LLM clients, and those modules import task handlers for callbacks or signals.

**Fix**: Move callback/signal registration to a separate module using Django-style pattern.

```python
# signals.py (new file)
from django.dispatch import receiver
from handle_task import task_completed  # Import AFTER all modules loaded

@receiver(signal=task_signal)
def on_task_completed(sender, task_id, **kwargs):
    task_completed(task_id, **kwargs)

# handle_task.py - Remove all signal registration code
# (moved to signals.py)

# crew_shared.py - Import signals to ensure registration
import signals  # noqa: F401
```

# Step-by-Step Fix Procedure

## Step 1: Identify the Cycle
Run this diagnostic script in the project root:
```bash
python -c "import sys; import importlib.util; spec = importlib.util.find_spec('handle_task'); print('handle_task location:', spec.origin); print('module graph:'); import pprint; pprint.pprint(sys.modules)"
```

Or add temporary debug output to `handle_task.py`:
```python
import sys
print("MODULE LOAD: handle_task.py, sys.modules keys:", list(sys.modules.keys()))
```

## Step 2: Map the Import Chain
Trace what imports what by checking the top of each file:
- `handle_task.py`: List all `import X` and `from Y import Z` lines
- For each `Y`, check its imports recursively
- Look for a loop: `handle_task → A → B → ... → handle_task`

## Step 3: Apply the Minimal Fix
Choose based on cycle length:

- **2-module cycle** (A ↔ B): Use **lazy import** (move imports inside functions)
- **3+ module cycle**: Introduce **shared module C** with common dependencies
- **Signal/registration pattern**: Use **separate signals.py** file

## Step 4: Verify the Fix
```bash
# Test import succeeds
python -c "import handle_task; print('OK')"

# Run task execution to ensure no delayed errors
python -c "from handle_task import handle; handle({'test': 'data'})"
```

# Preference Order for Fix Approach

1. **Lazy import** (simplest, minimal code change)
   ```python
   def function():
       from module import thing  # Move here
       use(thing)
   ```

2. **Dependency inversion** (extract to third module)
   ```python
   # shared_utils.py ← new file with common code
   # Both modules import from shared_utils, not each other
   ```

3. **Type-only imports** (if only for annotations)
   ```python
   from typing import TYPE_CHECKING
   if TYPE_CHECKING:
       from other import Class  # Only for type hints
   ```

4. **Restructure code ownership** (last resort)
   - Move signal handlers to `signals.py`
   - Move registrations to `registry.py`

# Common Pitfall: Delayed Import Errors

The `ImportError` might shift from module-load time to function-call time after lazy importing. Test all code paths including error handlers and edge cases.

# Validation Checklist

- [ ] `import handle_task` succeeds at Python REPL
- [ ] All unit tests involving `handle_task` pass
- [ ] At least one task of each handler type executes successfully
- [ ] No new `ImportError` or `AttributeError` appears in logs
- [ ] The change doesn't affect other modules' public APIs

# References

- Skill: `circular_import_resolution_patterns.md` (comprehensive pattern catalog)
- Error pattern: `handle_task:ImportError` (7 occurrences in journal)
- Recent experiments: pending fixes for circular import in handle_task.py
