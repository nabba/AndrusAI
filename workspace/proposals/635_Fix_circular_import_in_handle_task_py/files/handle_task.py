# Fix for circular import in handle_task.py
# The core issue: module-level imports create a cycle where A imports B and B imports A.
# Solution: defer imports until needed (lazy loading) and use TYPE_CHECKING for annotations.

from typing import TYPE_CHECKING

# Type hints only - these imports are not executed at runtime
if TYPE_CHECKING:
    from agents import AgentWrapper  # or whatever the actual types are
    from crews import CrewOrchestrator

# NOTE: ALL other agent/crew imports must be moved INSIDE the functions
# that use them. For example:
#
# BEFORE (causes circular import):
# from agents.base import Agent
# from crews.orchestrator import CrewOrchestrator
#
# AFTER (lazy import inside function):
# def execute_task(task):
#     from agents.base import Agent  # Imported only when function runs
#     from crews.orchestrator import CrewOrchestrator
#     ...
#
# This file needs to be refactored to apply this pattern consistently.
# Instructions:
# 1. Identify all imports from agents/ or crews/ modules at the top
# 2. Move each import into the function/method where it's first used
# 3. For type hints, move those under 'if TYPE_CHECKING:'
# 4. Verify no symbols from those modules are used at module level

# Example refactoring snippet for reference:
def handle_task(task_spec):
    # Lazy import to avoid circular dependency
    from crew_system.orchestrator import CrewOrchestrator
    orchestrator = CrewOrchestrator()
    return orchestrator.dispatch(task_spec)

# If there are module-level constants/utilities that need these types,
# move them into a separate helpers module that doesn't import back.