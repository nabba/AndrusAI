# IMPORT DIAGNOSTIC: Circular Import Investigation

**Date**: 2026-04-21
**Detected by**: Evolution Engineer cycle

## Error Pattern
`handle_task:ImportError` — 7 occurrences in error journal

## Hypothesis
Circular import exists between:
- `handle_task.py` (task dispatcher)
- `crew_shared.py` (shared utilities)
- Possibly other crew modules

## Diagnostic Steps for Next Cycle

1. **Locate files**:
   - Find `handle_task.py` in the codebase
   - Find `crew_shared.py` or equivalent shared module
   - Examine import statements at module level

2. **Identify the cycle**:
   Look for patterns like:
   ```python
   # handle_task.py
   from crew_shared import validate_output, process_result
   
   # crew_shared.py
   from handle_task import TASK_REGISTRY, get_current_task
   ```

3. **Apply fix** (from existing skill):
   - **Pattern 1** (simplest): Move imports inside functions
   - **Pattern 2** (cleaner): Extract shared dependencies to `task_registry.py`

## Proposed Fix Direction

**If cycle is between handle_task.py and crew_shared.py:**
- Create `task_registry.py` to hold shared state (TASK_REGISTRY)
- Update both modules to import from `task_registry` instead of each other
- This breaks the cycle with minimal complexity

**If the cycle involves other modules:**
Apply dependency inversion by extracting the shared dependency to a new leaf module with no inbound dependencies.
