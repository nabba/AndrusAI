"""Agent-callable tools for the autonomous executor.

Verified Implementation Plan §1 closure (2026-05-22). The plan
called for ``tools/delegate_tool.py`` so internal agents — not just
the operator via Signal — can file delegate runs programmatically.

Currently shipped:
  * :mod:`delegate_tool` — wraps the executor's ``create_run`` REST
    endpoint as a CrewAI ``@tool`` function. Failure-isolated.
"""
from app.autonomous_executor.tools.delegate_tool import (  # noqa: F401
    delegate_goal,
)

__all__ = ["delegate_goal"]
