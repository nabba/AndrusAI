# SKILL PROPOSAL: Finding and Understanding handle_task.py Circular Import

This skill guides the team on how to locate the actual `handle_task.py` file and diagnose the circular import error.

## Why This Is Needed

The error journal shows `handle_task:ImportError: 7x` but the file doesn't exist at workspace root. The handle_task module is:
1. Likely part of the crew framework
2. Located in a different path (e.g., subdirectory, installed package)
3. Must be found to apply the circular import fix

## Steps to Locate handle_task.py

### Option 1: Use find command in Docker container
```bash
find /app -name "handle_task.py" 2>/dev/null
```

### Option 2: Search workspace recursively
```bash
find . -name "*.py" | xargs grep -l "handle_task" 2>/dev/null | head -20
```

### Option 3: Check MCP filesystem tools
If MCP filesystem server is available, use it to search workspace.

## Common Locations
- `/app/crew/` or `/app/agents/`
- `/app/tasks/`
- `/app/handlers/`
- Inside installed package: `/usr/local/lib/python3.11/site-packages/crew/`

## What to Look For Once Found

Open `handle_task.py` and check:
```python
# At top of file:
from some_module import something  # ← This is likely the cycle
```

Check `some_module` for:
```python
# In that module:
from handle_task import something_else  # ← This closes the cycle
```

## Diagnostic Pattern
The skill `circular_import_resolution_patterns` already contains proven solutions:
- Pattern 1: Lazy Import (move import inside function)
- Pattern 2: Dependency Inversion (extract to new module)

Apply whichever pattern fits the actual import structure.

## Proceed After Finding
Once handle_task.py is located, create a CODE proposal (not a skill) that modifies that specific file to break the circular dependency.