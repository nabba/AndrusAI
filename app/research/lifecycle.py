"""Lifecycle bridge for direct research operations.

The synchronous research spine calls source adapters as ordinary Python
functions instead of through CrewAI's tool executor.  Without this bridge those
operations bypass PRE_TOOL_USE / POST_TOOL_USE, including SubIA observations,
the immutable dangerous-action hook, and tool-result memory.  This module gives
direct operations the same hook contract without modifying the infrastructure
registry itself.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


class ResearchToolBlocked(RuntimeError):
    """Raised when an immutable PRE_TOOL_USE hook blocks an operation."""


def _task_id() -> str:
    """Return the active research-run id without making it a hard dependency."""
    try:
        from app.research.run import _active_research_run_id

        return _active_research_run_id.get() or "research-direct"
    except Exception:
        return "research-direct"


def invoke_research_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    operation: Callable[[dict[str, Any]], Any],
    *,
    task_description: str = "",
    agent_id: str = "research",
) -> Any:
    """Execute a direct research operation through lifecycle hooks.

    Registry/import failures preserve the existing failure-isolated research
    behaviour.  An explicit hook abort is different: it is enforced by raising
    :class:`ResearchToolBlocked`, which source adapters already convert into an
    empty result.  Hook-provided input/result modifications are honoured.
    """
    effective_input = dict(tool_input)
    registry = None
    HookContext = HookPoint = None
    task_id = _task_id()

    try:
        from app.lifecycle_hooks import get_registry, HookContext, HookPoint

        registry = get_registry()
        pre = HookContext(
            hook_point=HookPoint.PRE_TOOL_USE,
            agent_id=agent_id,
            task_description=(task_description or tool_name)[:1000],
            data={
                "tool_name": tool_name,
                "tool_input": dict(effective_input),
                "action": str(effective_input),
            },
            metadata={
                "task_id": task_id,
                "operation_type": "research_tool",
            },
        )
        pre = registry.execute(HookPoint.PRE_TOOL_USE, pre)
        if pre.abort:
            raise ResearchToolBlocked(
                pre.abort_reason or f"{tool_name} blocked by lifecycle hook"
            )
        modified = pre.get("tool_input")
        if isinstance(modified, dict):
            effective_input = dict(modified)
    except ResearchToolBlocked:
        raise
    except Exception:
        registry = None
        logger.debug(
            "research lifecycle: PRE_TOOL_USE unavailable for %s",
            tool_name,
            exc_info=True,
        )

    try:
        result = operation(effective_input)
    except Exception as exc:
        if registry is not None and HookContext is not None and HookPoint is not None:
            try:
                registry.execute(
                    HookPoint.ON_ERROR,
                    HookContext(
                        hook_point=HookPoint.ON_ERROR,
                        agent_id=agent_id,
                        task_description=(task_description or tool_name)[:1000],
                        data={"error": str(exc)[:500], "tool_name": tool_name},
                        metadata={
                            "task_id": task_id,
                            "operation_type": "research_tool",
                        },
                    ),
                )
            except Exception:
                logger.debug(
                    "research lifecycle: ON_ERROR unavailable for %s",
                    tool_name,
                    exc_info=True,
                )
        raise

    if registry is None or HookContext is None or HookPoint is None:
        return result
    try:
        post = registry.execute(
            HookPoint.POST_TOOL_USE,
            HookContext(
                hook_point=HookPoint.POST_TOOL_USE,
                agent_id=agent_id,
                task_description=(task_description or tool_name)[:1000],
                data={
                    "tool_name": tool_name,
                    "tool_input": dict(effective_input),
                    "tool_result": result,
                    "result": result,
                    "success": True,
                },
                metadata={
                    "task_id": task_id,
                    "operation_type": "research_tool",
                },
            ),
        )
        return post.get("tool_result", result)
    except Exception:
        logger.debug(
            "research lifecycle: POST_TOOL_USE unavailable for %s",
            tool_name,
            exc_info=True,
        )
        return result


__all__ = ["ResearchToolBlocked", "invoke_research_tool"]
