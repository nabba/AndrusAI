"""
tool_hook_bridge.py — Bridges CrewAI's native tool hooks to our HookRegistry.

CrewAI (v1.11+) has a built-in hook system in crewai/hooks/tool_hooks.py
that fires automatically during tool execution inside the agent step executor.
This module registers adapter functions in CrewAI's global hook registry that
delegate to our HookRegistry's PRE_TOOL_USE and POST_TOOL_USE points.

This activates the previously dormant hooks:
  - block_dangerous (PRE_TOOL_USE, priority=1, immutable) — blocks rm -rf, DROP TABLE, etc.
  - humanist_safety (PRE_TOOL_USE, priority=0, immutable) — constitutional violation check
  - memorize_tools (POST_TOOL_USE, priority=50) — stores tool results in Mem0

Registered once at app startup via _register_defaults() in lifecycle_hooks.py.
Zero modifications to CrewAI internals or existing tool definitions.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_registered = False


def register_tool_hook_bridge() -> None:
    """Register adapter hooks in CrewAI's global hook registry.

    Idempotent — safe to call multiple times (only registers once).
    Gracefully degrades if CrewAI version lacks native hook support.
    """
    global _registered
    if _registered:
        return

    from crewai.hooks.tool_hooks import (
        register_before_tool_call_hook,
        register_after_tool_call_hook,
    )
    from app.lifecycle_hooks import get_registry, HookPoint, HookContext

    def _before_adapter(crewai_ctx) -> bool | None:
        """Translate CrewAI before-tool context to our PRE_TOOL_USE hooks.

        Returns False to block tool execution (when any hook sets ctx.abort).
        Returns None to allow execution.
        """
        try:
            agent_id = ""
            if crewai_ctx.agent:
                agent_id = getattr(crewai_ctx.agent, "role", "") or ""

            tool_input = dict(crewai_ctx.tool_input) if crewai_ctx.tool_input else {}

            hook_ctx = HookContext(
                hook_point=HookPoint.PRE_TOOL_USE,
                agent_id=agent_id,
                task_description=(crewai_ctx.task.description[:200]
                                  if crewai_ctx.task else ""),
                data={
                    "tool_name": crewai_ctx.tool_name,
                    "tool_input": tool_input,
                    # Flat string for pattern matching (used by block_dangerous)
                    "action": str(tool_input),
                },
                metadata={
                    "crew": getattr(crewai_ctx.crew, "id", None),
                },
            )

            result_ctx = get_registry().execute(HookPoint.PRE_TOOL_USE, hook_ctx)

            if result_ctx.abort:
                logger.warning(
                    f"TOOL BLOCKED by PRE_TOOL_USE: {crewai_ctx.tool_name} "
                    f"— {result_ctx.abort_reason}"
                )
                return False

            # Apply input modifications back to CrewAI's mutable context
            modified_input = result_ctx.modified_data.get("tool_input")
            if modified_input and isinstance(modified_input, dict):
                crewai_ctx.tool_input.update(modified_input)

        except Exception as e:
            # Hook bridge failure must not break tool execution (fail-open)
            logger.debug(f"tool_hook_bridge: before adapter error: {e}")

        return None  # Allow execution

    def _after_adapter(crewai_ctx) -> str | None:
        """Translate CrewAI after-tool context to our POST_TOOL_USE hooks.

        Returns modified result string if any hook changed it.
        Returns None to keep original result.
        """
        try:
            agent_id = ""
            if crewai_ctx.agent:
                agent_id = getattr(crewai_ctx.agent, "role", "") or ""

            hook_ctx = HookContext(
                hook_point=HookPoint.POST_TOOL_USE,
                agent_id=agent_id,
                task_description=(crewai_ctx.task.description[:200]
                                  if crewai_ctx.task else ""),
                data={
                    "tool_name": crewai_ctx.tool_name,
                    "tool_input": dict(crewai_ctx.tool_input) if crewai_ctx.tool_input else {},
                    "tool_result": crewai_ctx.tool_result or "",
                },
                metadata={
                    "crew": getattr(crewai_ctx.crew, "id", None),
                },
            )

            result_ctx = get_registry().execute(HookPoint.POST_TOOL_USE, hook_ctx)

            # If hooks modified the result, return it to CrewAI
            modified_result = result_ctx.modified_data.get("tool_result")
            if modified_result and isinstance(modified_result, str):
                return modified_result

        except Exception as e:
            logger.debug(f"tool_hook_bridge: after adapter error: {e}")

        return None  # Keep original result

    register_before_tool_call_hook(_before_adapter)
    register_after_tool_call_hook(_after_adapter)
    _registered = True
    logger.info("tool_hook_bridge: registered (PRE_TOOL_USE + POST_TOOL_USE now active)")
