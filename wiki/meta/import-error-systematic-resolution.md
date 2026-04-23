---
aliases:
- import error systematic resolution
author: idle_scheduler.wiki_synthesis
confidence: medium
created_at: '2026-04-23T11:49:47Z'
date: '2026-04-23'
related: []
relationships: []
section: meta
source: workspace/skills/import_error_systematic_resolution.md
status: active
tags:
- self-improvement
- skills
- auto-synthesised
title: Systematic ImportError Resolution — Domain Skill
updated_at: '2026-04-23T11:49:47Z'
version: 1
---

# Systematic ImportError Resolution — Domain Skill

## Purpose
Provides systematic, actionable steps to resolve Python ImportError and ModuleNotFoundError issues, especially recurring `handle_task:ImportError` patterns in autonomous agent systems.

## Quick Diagnosis Guide

### Step 1: Error Type Identification
- **ImportError (generic)**: Module exists but specific item fails to import
- **ModuleNotFoundError**: Module cannot be found at all (subclass of ImportError)
- **Circular Import Error**: "most likely due to a circular import" in error message

**Error Message Pattern Recognition:**
```
ImportError: cannot import name 'X' from partially initialized module 'Y'
(most likely due to a circular import)
```
→ This indicates a circular dependency, not a missing module.

```
ModuleNotFoundError: No module named 'module_name'
```
→ Module not installed or PYTHONPATH issue.

```
ImportError: cannot import name 'X' from 'Y'
```
→ Typo, wrong version, or API change in module Y.

---

## Step-by-Step Resolution Workflow

### For Circular Import Detection
1. **Check import chain**: Examine the traceback to see which modules are loading in a cycle
2. **Visualize dependency graph**: Use `pycycle` or manual inspection
3. **Apply appropriate fix based on dependency direction**

### Reduction Patterns (from most to least invasive)

#### Pattern 1: Lazy Import (Local Scope)
Move imports inside functions to break top-level cycle:

```python
# BEFORE (causes circular import at module load)
from task_handler import process_task

def run():
    return process_task(data)

# AFTER (lazy import breaks cycle)
def run():
    from task_handler import process_task  # Imported only when run() is called
    return process_task(data)
```

**Pattern 2: Dependency Inversion (Third Module)**
Extract shared dependencies into a separate module:

```python
# shared_types.py
from dataclasses import dataclass

@dataclass
class TaskData:
    id: str
    payload: dict

# module_a.py
from shared_types import TaskData

# module_b.py
from shared_types import TaskData
```

**Pattern 3: Type-Only Imports (TYPE_CHECKING)**
For type hints only, use conditional import:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from task_handler import TaskResult  # Only for type checkers, not at runtime

def process() -> "TaskResult":
    # ... implementation
```

**Pattern 4: Import Module Instead of Names**
Import the module itself, then access attributes:

```python
# Instead of: from task_handler import TaskHandler
import task_handler

class Worker:
    def handle(self):
        handler = task_handler.TaskHandler()
```

---

## Debugging Checklist

- [ ] Examine full traceback — identify which modules are part of the cycle
- [ ] Check `__init__.py` files for unexpected re-exports
- [ ] Verify there are no import statements in global scope that trigger chain
- [ ] Search for "from X import Y" patterns where both X and Y import each other
- [ ] Confirm module names don't shadow standard library packages
- [ ] Check `sys.path` includes project root directory

---

## Prevention Guidelines

1. **Prefer dependency injection** over global imports
2. **Keep top-level imports minimal** — move non-essential imports into functions
3. **Use interface segregation** — extract shared contracts to separate modules
4. **Avoid circular type hints** — use string annotations or TYPE_CHECKING
5. **Establish module ownership** — one module should own each type/class
6. **Document import assumptions** in module docstrings

---

## Common Pitfalls to Avoid

| Pitfall | Why It's Wrong | Fix |
|---------|---------------|-----|
| Importing entire crew modules in task files | Creates coupling between task definitions and execution layer | Use abstract interfaces or data-only contracts |
| Re-exporting via `__init__.py` without need | Hides actual dependency graph | Keep `__init__.py` minimal or empty |
| Conditional imports based on runtime flags | Makes dependency graph unpredictable | Separate optional functionality into sub-modules |

---

## Tool Support

- **pycycle**: `pip install pycycle` → `pycycle your_project/` to visualize cycles
- **pydeps**: `pip install pydeps` → generates dependency graphs
- **mypy**: Use `--warn-unused-ignores` to detect TYPE_CHECKING misuse

---

## Expertise Level Required
**Level 2 Intermediate**: Requires understanding of Python's import system, module loading sequence, and basic software design principles. The patterns above cover 90% of circular import cases in autonomous agent codebases.
