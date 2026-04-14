"""
backup_planner.py — Adaptive replanning on tool failure.

Research shows LLM agents struggle to formulate backup plans when tools
fail (arXiv:2508.11027 "Hell or High Water"). This module tracks tool
failures per task and, after 3 failures, proposes an alternative approach
instead of just retrying.

The replanning flow:
  1. Tool fails → record_tool_failure()
  2. 3 failures for same task? → search evo_memory for past solutions
  3. No past solution? → background LLM generates alternative plan
  4. Set ctx.metadata["_replan_suggested"] for commander to re-route

Reference: arXiv:2508.11027 "Hell or High Water: Evaluating Agentic Recovery"
"""

import hashlib
import json
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

_MAX_FAILURES_PER_TASK = 3   # Failures before suggesting replan
_FAILURE_WINDOW_S = 600      # 10 min window for counting
_MAX_CONTEXT_ENTRIES = 50    # Bounded tool failure log
_CONTEXT_PATH = Path("/app/workspace/tool_failure_context.json")


@dataclass
class ToolFailureContext:
    """Accumulated context from tool failures for one task."""
    task_hash: str
    task_description: str
    failures: list[dict] = field(default_factory=list)
    first_failure_at: float = 0.0
    replan_suggested: bool = False


# Per-task failure tracking (in-memory, resets on restart)
_task_failures: dict[str, ToolFailureContext] = {}
_lock = threading.Lock()


def _task_hash(crew_name: str, task_description: str) -> str:
    """Stable hash for identifying the same task across retries."""
    key = f"{crew_name}:{task_description[:200]}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# ── Failure tracking ─────────────────────────────────────────────────────────

def record_tool_failure(
    tool_name: str,
    tool_input: str,
    error: str,
    agent_id: str,
    task_description: str,
    crew_name: str = "",
) -> ToolFailureContext:
    """Record a tool failure and return the accumulated context for this task."""
    now = time.monotonic()
    th = _task_hash(crew_name or agent_id, task_description)

    with _lock:
        if th not in _task_failures:
            _task_failures[th] = ToolFailureContext(
                task_hash=th,
                task_description=task_description[:200],
                first_failure_at=now,
            )

        ctx = _task_failures[th]

        # Prune old failures outside the window
        ctx.failures = [f for f in ctx.failures if now - f.get("at", 0) < _FAILURE_WINDOW_S]

        # Record this failure
        ctx.failures.append({
            "tool": tool_name,
            "input": str(tool_input)[:200],
            "error": str(error)[:300],
            "agent": agent_id,
            "at": now,
        })

    return ctx


def should_replan(crew_name: str, task_description: str) -> bool:
    """Check if enough failures have accumulated to suggest replanning."""
    th = _task_hash(crew_name, task_description)
    with _lock:
        ctx = _task_failures.get(th)
        if not ctx:
            return False
        return len(ctx.failures) >= _MAX_FAILURES_PER_TASK and not ctx.replan_suggested


def mark_replan_suggested(crew_name: str, task_description: str) -> None:
    """Mark that replanning has been suggested for this task (prevent re-fire)."""
    th = _task_hash(crew_name, task_description)
    with _lock:
        ctx = _task_failures.get(th)
        if ctx:
            ctx.replan_suggested = True


# ── Alternative plan generation ──────────────────────────────────────────────

def formulate_backup_plan(
    failure_ctx: ToolFailureContext,
    original_task: str,
) -> str | None:
    """Generate an alternative approach using LLM (background call).

    First searches evo_memory for past solutions to similar failures.
    Falls back to LLM only if no known solution exists.

    Returns alternative plan text, or None if generation fails.
    """
    # Step 1: Search evolutionary memory for past solutions
    try:
        from app.evo_memory import recall_similar_successes
        similar = recall_similar_successes(
            f"tool failure: {failure_ctx.failures[-1].get('error', '')}", n=3
        )
        if similar:
            best = similar[0]
            if best.get("metadata", {}).get("delta", 0) > 0:
                return (
                    f"Past successful approach: {best.get('document', '')[:300]}\n"
                    f"Files changed: {best.get('metadata', {}).get('files', 'unknown')}"
                )
    except Exception:
        pass

    # Step 2: LLM-generated alternative plan (background, non-blocking)
    try:
        from app.llm_factory import create_specialist_llm
        llm = create_specialist_llm(max_tokens=512, role="architecture")

        failed_tools = [f.get("tool", "?") for f in failure_ctx.failures]
        failed_errors = [f.get("error", "?")[:100] for f in failure_ctx.failures[-3:]]

        prompt = (
            f"A task has failed {len(failure_ctx.failures)} times due to tool errors.\n\n"
            f"Task: {original_task[:300]}\n"
            f"Failed tools: {', '.join(set(failed_tools))}\n"
            f"Errors: {'; '.join(failed_errors)}\n\n"
            f"Suggest ONE alternative approach that avoids these tools entirely.\n"
            f"Be specific and actionable in 2-3 sentences."
        )

        raw = str(llm.call(prompt)).strip()
        return raw if raw and len(raw) > 20 else None

    except Exception as e:
        logger.debug(f"backup_planner: LLM plan generation failed: {e}")
        return None


# ── Persistent logging ───────────────────────────────────────────────────────

def _log_tool_failure(failure_ctx: ToolFailureContext) -> None:
    """Append to bounded tool failure log for dashboard visibility."""
    try:
        entries = []
        if _CONTEXT_PATH.exists():
            entries = json.loads(_CONTEXT_PATH.read_text())

        entries.append({
            "task_hash": failure_ctx.task_hash,
            "task": failure_ctx.task_description[:100],
            "failure_count": len(failure_ctx.failures),
            "last_tool": failure_ctx.failures[-1].get("tool", "?") if failure_ctx.failures else "?",
            "last_error": failure_ctx.failures[-1].get("error", "?")[:100] if failure_ctx.failures else "?",
            "ts": time.time(),
            "replan_suggested": failure_ctx.replan_suggested,
        })

        # Bound to max entries
        if len(entries) > _MAX_CONTEXT_ENTRIES:
            entries = entries[-_MAX_CONTEXT_ENTRIES:]

        from app.safe_io import safe_write
        safe_write(_CONTEXT_PATH, json.dumps(entries, indent=2))

    except Exception:
        pass


# ── Lifecycle hook ───────────────────────────────────────────────────────────

def create_backup_planner_hook():
    """POST_TOOL_USE hook: track failures and suggest replanning when needed."""
    def _hook(ctx):
        try:
            # Detect tool failure from context
            tool_result = ctx.data.get("tool_result", "")
            tool_name = ctx.data.get("tool_name", "")
            tool_input = ctx.data.get("tool_input", "")

            # Heuristic: empty result, "Error:" prefix, or exception in result
            is_failure = (
                not tool_result
                or (isinstance(tool_result, str) and (
                    tool_result.startswith("Error:")
                    or "exception" in tool_result.lower()[:100]
                    or "failed" in tool_result.lower()[:50]
                ))
            )

            if not is_failure:
                return ctx

            # Record the failure
            crew_name = ctx.metadata.get("crew", ctx.agent_id or "unknown")
            failure_ctx = record_tool_failure(
                tool_name=tool_name,
                tool_input=str(tool_input)[:200],
                error=str(tool_result)[:300],
                agent_id=ctx.agent_id,
                task_description=ctx.task_description,
                crew_name=crew_name,
            )

            _log_tool_failure(failure_ctx)

            # Check if replanning threshold reached
            if should_replan(crew_name, ctx.task_description):
                mark_replan_suggested(crew_name, ctx.task_description)

                # Generate backup plan in background
                plan = formulate_backup_plan(failure_ctx, ctx.task_description)

                ctx.metadata["_replan_suggested"] = True
                ctx.metadata["_tool_failure_ctx"] = {
                    "failures": len(failure_ctx.failures),
                    "tools": list(set(f.get("tool", "?") for f in failure_ctx.failures)),
                    "backup_plan": plan[:500] if plan else None,
                }

                logger.info(
                    f"backup_planner: suggesting replan after {len(failure_ctx.failures)} failures "
                    f"(task={ctx.task_description[:60]})"
                )

        except Exception:
            pass
        return ctx
    return _hook
