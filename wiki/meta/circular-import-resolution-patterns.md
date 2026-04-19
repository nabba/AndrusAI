---
aliases:
- circular import resolution patterns
author: idle_scheduler.wiki_synthesis
confidence: medium
created_at: '2026-04-19T20:37:12Z'
date: '2026-04-19'
related: []
relationships: []
section: meta
source: workspace/skills/circular_import_resolution_patterns.md
status: active
tags:
- self-improvement
- skills
- auto-synthesised
title: Circular Import Resolution Patterns
updated_at: '2026-04-19T20:37:12Z'
version: 1
---

# Circular Import Resolution Patterns

*kb: episteme | id: skill_episteme_circular_import_resolution | status: active | usage: 0 | created: 2026-04-18T17:30:00+00:00*

# Problem Diagnosis

Circular imports occur when two modules depend on each other directly or indirectly:
- Module A imports from B, and B imports from A
- Import statements at module level instead of inside functions
- Shared dependencies creating import cycles

This causes `ImportError` or `AttributeError` at runtime when modules are loaded in the wrong order.

# Solution Patterns

## Pattern 1: Lazy Import (Local Scope)

Move imports inside functions/methods that need them:

```python
# BEFORE (causes circular import at module load)
from crew_shared import validate_output
from handle_task import process_result

def execute():
    result = process_task()
    return validate_output(result)
```

```python
# AFTER (breaks cycle - imports only when function runs)
def execute():
    # Lazy import to avoid circular dependency
    from crew_shared import validate_output
    from handle_task import process_result
    
    result = process_task()
    return validate_output(result)
```

**When to use:** Simple two-module cycles; optional dependencies; when import is only needed in one function.

## Pattern 2: Dependency Inversion (Extract Shared Code)

Move common code to a third module with no dependencies on the cyclical pair:

```python
# shared_utils.py (new module - no imports from A or B)
def validate_output(data):
    return data is not None

# module_a.py
from shared_utils import validate_output
import module_b

def execute():
    result = module_b.process()
    return validate_output(result)

# module_b.py
from shared_utils import validate_output

def process():
    data = get_data()
    return validate_output(data)
```

**When to use:** Multiple modules sharing utilities; long-term maintainability; when both modules need the same functions.

## Pattern 3: Import at Function Level with Caching

For frequently-called functions, cache the imported module:

```python
def get_validator():
    """Lazy import with caching to avoid repeated imports."""
    if not hasattr(get_validator, '_validate_output'):
        from crew_shared import validate_output
        get_validator._validate_output = validate_output
    return get_validator._validate_output

def execute():
    result = process_task()
    return get_validator()(result)
```

**When to use:** Performance-critical functions called repeatedly; avoiding repeated import overhead.

## Pattern 4: Restructure Code Ownership

Move the code that creates the dependency to break the cycle:

```python
# Option A: Move signal handler from users.models to separate signals.py
# users/models.py - no longer imports signals
from django.db import models

class User(models.Model):
    name = models.CharField(max_length=100)

# users/signals.py - imports models
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import User

@receiver(post_save, sender=User)
def send_welcome_email(sender, instance, **kwargs):
    pass
```

**When to use:** Framework-specific patterns (Django signals, Flask routes); separating concerns; large codebases.

## Pattern 5: Type-Only Imports (Python 3.7+)

Use `from __future__ import annotations` and `typing.TYPE_CHECKING`:

```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from other_module import OtherClass  # Only for type hints

class MyClass:
    def method(self, other: OtherClass) -> None:  # No runtime import
        pass
```

**When to use:** Type annotation dependencies only; when you need the class name for hints but not at runtime.

# Implementation Decision Tree

1. Is the import only needed inside one function?
   → Use Pattern 1 (Lazy Import)

2. Do multiple modules need the same shared code?
   → Use Pattern 2 (Dependency Inversion)

3. Is the function called very frequently?
   → Use Pattern 3 (Cached Lazy Import)

4. Is this a framework pattern (Django/Flask)?
   → Use Pattern 4 (Restructure Code Ownership)

5. Is the import only for type annotations?
   → Use Pattern 5 (Type-Only Imports)

# Best Practices

- **Import at the lowest level possible:** Prefer function-level over class-level, class-level over module-level.
- **Prefer composition over inheritance:** Reduces coupling and import cycles.
- **Use interface/protocol segregation:** Define minimal interfaces in separate modules.
- **Avoid circular dependency in constructors:** Pass dependencies as parameters instead of importing.
- **Document import structure:** Add comments explaining why lazy imports are used.

# Common Pitfall: Delayed Import Errors

Even after breaking cycles, errors may appear later when the code path executes. Test all code paths:

```python
def process():
    # This import will fail if there's still a dependency issue
    from problematic_module import function
    return function()
```

# References

- https://datacamp.com/tutorial/python-circular-import
- https://rollbar.com/blog/how-to-fix-a-circular-import-in-python
- https://www.pythonmorsels.com/fixing-circular-imports/
- https://mend.io/blog/closing-the-loop-on-python-circular-import-issue
