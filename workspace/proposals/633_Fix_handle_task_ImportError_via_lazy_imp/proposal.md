# Proposal #633: Fix handle_task ImportError via lazy import

**Type:** code
**Created:** 2026-04-18T19:37:21.404048+00:00

## Description

The error journal shows 7 occurrences of `handle_task:ImportError` caused by circular imports between `handle_task.py` and `agents/coding_agent.py`. At module load time, `handle_task.py` attempts to import from `coding_agent.py`, which in turn imports back from `handle_task.py`, creating a cycle that fails initialization. This is a known Python anti-pattern. The fix removes top-level imports of `CodingAgent` and `BaseAgent` from `handle_task.py` and replaces them with lazy imports inside the `_execute_coding` helper function. This breaks the cycle: `handle_task` no longer imports `coding_agent` at module load, so `coding_agent` can safely import whatever it needs from `handle_task` without triggering partial initialization errors. The change is minimal (2 lines removed, 2 moved inside function), no new dependencies, and preserves all existing behavior.

## Files

- `handle_task.py`
