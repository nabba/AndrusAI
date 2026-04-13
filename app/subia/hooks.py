"""
subia.hooks — integration surface for the CIL loop into the
crewai-amendments lifecycle hook system.

Built in Phase 4 alongside the CIL loop. Not yet wired into the live
orchestrator — that happens in a separate plug-in step once this
surface is reviewed. See PROGRAM.md Phase 4.

The hook surface is intentionally minimal:

  class SubIALifecycleHooks:
      pre_task(agent, task) -> injection_context:str
      post_task(agent, task, result) -> None

  register(hooks_registry, subia_hooks) -> None
  unregister(hooks_registry, subia_hooks) -> None

`hooks_registry` is duck-typed — any object with .register(name,
when, fn, priority=…) and .unregister(name) methods works. In
production it will be the existing `app.lifecycle_hooks` registry.
In tests it can be a dict-backed mock.

Operation classification:
  - Task descriptions containing "ingest", "new source" → "ingest"
  - Task containing "lint", "health check"               → "lint"
  - Task containing "wiki_read" or "read"                → "wiki_read"
  - Otherwise                                            → "task_execute"

All classified operations are fed to SubIALoop which then decides
full vs compressed via SUBIA_CONFIG.

Context injection: pre_task returns a structured SubIA context block
as a string that the caller appends to the agent's task context.
Format matches SubIA Part I §4.2 — a bordered block that the agent
reads but cannot modify.

Infrastructure-level. Not agent-modifiable.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from app.subia.config import SUBIA_CONFIG
from app.subia.kernel import SubjectivityKernel
from app.subia.loop import CILResult, SubIALoop

logger = logging.getLogger(__name__)


_HOOK_NAME_PRE = "subia_pre_task"
_HOOK_NAME_POST = "subia_post_task"

# Low priority so safety and budget hooks still run first.
_HOOK_PRIORITY = 20


class SubIALifecycleHooks:
    """Lifecycle hooks that run the CIL loop around every agent task.

    Args:
        loop:  A SubIALoop instance with its dependencies already wired.
               The hooks do NOT construct gates or predictors — those
               are the caller's responsibility, keeping the surface
               testable and swappable.
    """

    def __init__(self, loop: SubIALoop) -> None:
        self.loop = loop
        # Remember the pre_task result so post_task can align context.
        self._last_pre: dict[str, CILResult] = {}

    # ── pre_task ──────────────────────────────────────────────────

    def pre_task(self, agent: Any, task: Any) -> str:
        """Called before every agent task execution.

        Returns a string: the SubIA context block to prepend to the
        agent's existing context. Never raises — all failures are
        contained inside the CIL loop itself.
        """
        agent_role = getattr(agent, "role", "unknown")
        description = self._get_description(task)
        operation_type = self._classify_operation(description)

        result = self.loop.pre_task(
            agent_role=agent_role,
            task_description=description,
            operation_type=operation_type,
        )

        task_id = getattr(task, "id", None) or id(task)
        self._last_pre[str(task_id)] = result

        return self._build_injection(result)

    # ── post_task ─────────────────────────────────────────────────

    def post_task(self, agent: Any, task: Any, task_result: Any) -> None:
        """Called after every agent task execution."""
        agent_role = getattr(agent, "role", "unknown")
        description = self._get_description(task)
        operation_type = self._classify_operation(description)

        # Normalize task_result into a dict the loop expects.
        result_dict: dict
        if isinstance(task_result, dict):
            result_dict = task_result
        else:
            result_dict = {
                "summary": str(task_result)[:200],
                "success": True,
            }

        actual_content = str(task_result)[:500]

        self.loop.post_task(
            agent_role=agent_role,
            task_description=description,
            operation_type=operation_type,
            task_result=result_dict,
            actual_content=actual_content,
        )

        # Clean up pre_task cache to bound memory.
        task_id = str(getattr(task, "id", None) or id(task))
        self._last_pre.pop(task_id, None)

    # ── Helpers ───────────────────────────────────────────────────

    def _get_description(self, task: Any) -> str:
        """Extract a task description string from whatever shape
        the caller handed us.
        """
        for attr in ("description", "prompt", "input", "name"):
            value = getattr(task, attr, None)
            if isinstance(value, str):
                return value
        if isinstance(task, str):
            return task
        return str(task)[:200]

    def _classify_operation(self, description: str) -> str:
        """Heuristic classification from SubIA Part I §4.2."""
        lower = description.lower()
        if "ingest" in lower or "new source" in lower:
            return "ingest"
        if "lint" in lower or "health check" in lower:
            return "lint"
        if "wiki_read" in lower or "wiki read" in lower:
            return "wiki_read"
        if "wiki_search" in lower or "wiki search" in lower:
            return "wiki_search"
        if "routine" in lower:
            return "routine_query"
        return "task_execute"

    def _build_injection(self, result: CILResult) -> str:
        """Format the CIL pre_task result as a context-block string.

        The agent sees this as additional context, not as a tool
        call or parameter. It cannot modify the block.
        """
        ctx = result.context_for_agent
        lines = ["", "--- SubIA Context ---", f"loop: {result.loop_type}"]

        scene = ctx.get("scene_summary") or []
        if scene:
            lines.append(f"scene ({len(scene)} items):")
            for entry in scene[:5]:
                summary = entry.get("summary", "")[:60]
                salience = entry.get("salience", 0.0)
                lines.append(f"  - [{salience:.2f}] {summary}")

        if "homeostatic_deviations" in ctx:
            dev = ctx["homeostatic_deviations"]
            parts = [f"{k}={v:+.2f}" for k, v in dev.items()]
            lines.append(f"homeostatic-alerts: {', '.join(parts)}")

        pred = ctx.get("prediction")
        if pred:
            lines.append(
                f"prediction: conf={pred.get('confidence', 0.5):.2f}"
            )

        cascade = ctx.get("cascade_recommendation", "maintain")
        if cascade != "maintain":
            lines.append(f"cascade: {cascade}")

        dispatch = ctx.get("dispatch")
        if dispatch and dispatch.get("verdict") != "ALLOW":
            lines.append(
                f"dispatch: {dispatch['verdict']} — "
                f"{dispatch.get('reason', '')[:120]}"
            )

        lines.append("--- End SubIA Context ---")
        lines.append("")
        return "\n".join(lines)


# ── Registration helpers ──────────────────────────────────────────

def register(
    hooks_registry: Any,
    subia_hooks: SubIALifecycleHooks,
    priority: int = _HOOK_PRIORITY,
) -> None:
    """Register subia_hooks with a duck-typed registry.

    The registry must expose a register(name, when, fn, priority=...)
    method. Registration is idempotent: a second call with the same
    name replaces the first.
    """
    _idempotent_register(hooks_registry, _HOOK_NAME_PRE, "pre_task",
                         subia_hooks.pre_task, priority)
    _idempotent_register(hooks_registry, _HOOK_NAME_POST, "post_task",
                         subia_hooks.post_task, priority)


def unregister(hooks_registry: Any) -> None:
    """Remove the SubIA hooks from a registry, if registered."""
    for name in (_HOOK_NAME_PRE, _HOOK_NAME_POST):
        unreg = getattr(hooks_registry, "unregister", None)
        if callable(unreg):
            try:
                unreg(name)
            except Exception:
                logger.debug("hooks unregister failed for %s", name,
                             exc_info=True)


def _idempotent_register(
    registry: Any, name: str, when: str,
    fn: Callable, priority: int,
) -> None:
    unreg = getattr(registry, "unregister", None)
    if callable(unreg):
        try:
            unreg(name)
        except Exception:
            pass
    reg = getattr(registry, "register", None)
    if callable(reg):
        try:
            reg(name, when, fn, priority=priority)
        except TypeError:
            # Registry that does not accept the priority kwarg —
            # try without it.
            reg(name, when, fn)
    else:
        raise TypeError(
            f"hooks registry {type(registry).__name__} has no register(...)"
        )
