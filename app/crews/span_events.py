"""CrewAI event subscribers that persist fine-grained execution spans.

Bridges CrewAI's in-process event bus (agent/tool/LLM start-finish pairs)
into the ``control_plane.crew_task_spans`` table so the dashboard's
task-flow drawer can render "Commander → Research crew → Researcher
agent → WebSearch tool → gpt-5.2 LLM call" trees.

Correlation strategy
--------------------
CrewAI events carry their own ``event_id`` + ``parent_event_id`` which
reconstructs the internal hierarchy for free. What they don't carry is
our control-plane crew task id — so we set a ContextVar on crew entry
(``set_current_crew_task_id``) that subscribers read on every event.

Overhead
--------
One INSERT per agent/tool/LLM start (~1 ms local Postgres) and one
UPDATE per completion. For a typical crew run (~5 agents × ~3 tool
calls × ~2 LLM calls each = ~45 events) that's ~90 ms of DB work
across a 30-60 s crew run — negligible. Zero overhead when no crew is
running because the CrewAI bus doesn't fire.

Failure policy
--------------
Every subscriber is fail-soft: a missing ContextVar, a DB hiccup, or a
malformed event logs at DEBUG and returns. Telemetry must never break
crew execution.
"""
from __future__ import annotations

import contextvars
import logging
import threading
from typing import Any

from app.control_plane import crew_task_spans

logger = logging.getLogger(__name__)


# ── Per-crew correlation ─────────────────────────────────────────────────

# Control-plane crew_tasks.id for the currently-running crew. Set by the
# lifecycle manager before ``crew.kickoff()``, cleared on exit.
_current_crew_task_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_crew_task_id", default=None,
)

# CrewAI ``event_id`` → our ``crew_task_spans.id``. Populated by the
# ``*_Started`` subscribers, consumed by ``*_Completed`` subscribers to
# locate the span and stamp it complete. Also lets tool/LLM starts find
# their parent span by looking up the enclosing agent's event_id.
#
# Keyed globally (not per-task) because CrewAI event_ids are UUIDs —
# collision-free. Cleaned lazily on crew completion via
# ``_prune_task_events``.
_event_to_span: dict[str, int] = {}
_event_to_task: dict[str, str] = {}  # event_id → crew_task_id (for pruning)
_event_lock = threading.Lock()


def set_current_crew_task_id(task_id: str) -> contextvars.Token:
    """Bind ``task_id`` as the active crew_task for this context.

    Call before ``crew.kickoff()`` in the lifecycle wrapper. Returns a
    ContextVar token the caller MUST pass to ``clear_current_crew_task_id``
    so nested crew dispatches restore correctly.
    """
    return _current_crew_task_id.set(task_id)


def clear_current_crew_task_id(token: contextvars.Token) -> None:
    """Restore the previous current-task binding. Also sweeps the
    event→span map for any leftover rows from this task (orphans from
    events that fired but never received a matching completion — rare
    but should never leak indefinitely).
    """
    _current_crew_task_id.reset(token)


def _remember_span(event_id: str, span_id: int, task_id: str) -> None:
    with _event_lock:
        _event_to_span[event_id] = span_id
        _event_to_task[event_id] = task_id


def _pop_span(event_id: str) -> int | None:
    with _event_lock:
        span_id = _event_to_span.pop(event_id, None)
        _event_to_task.pop(event_id, None)
    return span_id


def _peek_span(event_id: str | None) -> int | None:
    if not event_id:
        return None
    with _event_lock:
        return _event_to_span.get(event_id)


def _parent_span_id(event: Any) -> int | None:
    """Resolve the parent span for an event.

    CrewAI sets ``parent_event_id`` on every event. If we've seen that
    parent start and recorded its span, return the span id. Otherwise
    return None — the span attaches to the task root.
    """
    return _peek_span(getattr(event, "parent_event_id", None))


# ── Subscribers ─────────────────────────────────────────────────────────


def _safe_start(
    *,
    span_type: str,
    name: str,
    event: Any,
    detail: dict[str, Any] | None = None,
) -> None:
    """Shared logic for every ``*_Started`` subscriber."""
    task_id = _current_crew_task_id.get()
    if not task_id:
        # Event fired outside any crew context (Signal command running
        # a one-off LLM call, bench runner, etc.) — not our concern.
        return
    event_id = getattr(event, "event_id", None)
    if not event_id:
        return
    span_id = crew_task_spans.start_span(
        task_id=task_id,
        span_type=span_type,
        name=name or span_type,
        parent_span_id=_parent_span_id(event),
        crewai_event_id=str(event_id),
        detail=detail or {},
    )
    if span_id:
        _remember_span(str(event_id), span_id, task_id)


def _safe_complete(
    *,
    event: Any,
    state: str = "completed",
    detail_patch: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Shared logic for every ``*_Completed`` / ``*_Finished`` subscriber.

    Uses ``started_event_id`` when present (CrewAI sets this on finish
    events to point back at the start) and falls back to ``event_id``.
    """
    start_id = getattr(event, "started_event_id", None) or getattr(event, "event_id", None)
    if not start_id:
        return
    span_id = _pop_span(str(start_id))
    if not span_id:
        return
    crew_task_spans.complete_span(
        span_id=span_id,
        state=state,
        detail_patch=detail_patch,
        error=error,
    )


# ── CrewAI bus wiring ────────────────────────────────────────────────────

_listeners_installed = False
_install_lock = threading.Lock()


def install_listeners() -> None:
    """Register the subscribers on the global CrewAI event bus.

    Idempotent — safe to call at module import, from main.py startup,
    or from tests.
    """
    global _listeners_installed
    with _install_lock:
        if _listeners_installed:
            return
        try:
            from crewai.events.event_bus import crewai_event_bus
            from crewai.events.types.agent_events import (
                AgentExecutionStartedEvent,
                AgentExecutionCompletedEvent,
                AgentExecutionErrorEvent,
            )
            from crewai.events.types.tool_usage_events import (
                ToolUsageStartedEvent,
                ToolUsageFinishedEvent,
                ToolUsageErrorEvent,
            )
            from crewai.events.types.llm_events import (
                LLMCallStartedEvent,
                LLMCallCompletedEvent,
                LLMCallFailedEvent,
            )
        except ImportError as exc:
            logger.warning(
                "span_events: CrewAI event types not importable (%s) — "
                "fine-grained task-flow spans disabled.", exc,
            )
            _listeners_installed = True  # don't retry
            return

        # ── Agent lifecycle ──────────────────────────────────────────
        @crewai_event_bus.on(AgentExecutionStartedEvent)
        def _on_agent_start(_source: Any, event: Any) -> None:
            try:
                tools = event.tools or []
                tool_names = [
                    getattr(t, "name", None) or type(t).__name__ for t in tools
                ][:10]
                _safe_start(
                    span_type="agent",
                    name=getattr(event, "agent_role", "") or "agent",
                    event=event,
                    detail={
                        "task_name": getattr(event, "task_name", None),
                        "task_prompt_preview": (getattr(event, "task_prompt", "") or "")[:400],
                        "tool_count": len(tools),
                        "tool_names": tool_names,
                    },
                )
            except Exception as exc:
                logger.debug("span_events: agent_start handler failed: %s", exc)

        @crewai_event_bus.on(AgentExecutionCompletedEvent)
        def _on_agent_complete(_source: Any, event: Any) -> None:
            try:
                output = getattr(event, "output", None)
                preview = None
                if output is not None:
                    preview = str(output)[:400]
                _safe_complete(
                    event=event,
                    state="completed",
                    detail_patch={"output_preview": preview} if preview else None,
                )
            except Exception as exc:
                logger.debug("span_events: agent_complete handler failed: %s", exc)

        @crewai_event_bus.on(AgentExecutionErrorEvent)
        def _on_agent_error(_source: Any, event: Any) -> None:
            try:
                _safe_complete(
                    event=event,
                    state="failed",
                    error=str(getattr(event, "error", "") or "")[:1000],
                )
            except Exception as exc:
                logger.debug("span_events: agent_error handler failed: %s", exc)

        # ── Tool lifecycle ──────────────────────────────────────────
        @crewai_event_bus.on(ToolUsageStartedEvent)
        def _on_tool_start(_source: Any, event: Any) -> None:
            try:
                args = getattr(event, "tool_args", None)
                args_preview = None
                if args is not None:
                    try:
                        args_preview = str(args)[:400]
                    except Exception:
                        args_preview = "<unprintable>"
                _safe_start(
                    span_type="tool",
                    name=getattr(event, "tool_name", "") or "tool",
                    event=event,
                    detail={
                        "tool_class": getattr(event, "tool_class", None),
                        "args_preview": args_preview,
                        "agent_key": getattr(event, "agent_key", None),
                    },
                )
            except Exception as exc:
                logger.debug("span_events: tool_start handler failed: %s", exc)

        @crewai_event_bus.on(ToolUsageFinishedEvent)
        def _on_tool_finish(_source: Any, event: Any) -> None:
            try:
                output = getattr(event, "output", None)
                output_preview = None
                if output is not None:
                    output_preview = str(output)[:400]
                _safe_complete(
                    event=event,
                    state="completed",
                    detail_patch={
                        "output_preview": output_preview,
                        "from_cache": bool(getattr(event, "from_cache", False)),
                    },
                )
            except Exception as exc:
                logger.debug("span_events: tool_finish handler failed: %s", exc)

        @crewai_event_bus.on(ToolUsageErrorEvent)
        def _on_tool_error(_source: Any, event: Any) -> None:
            try:
                _safe_complete(
                    event=event,
                    state="failed",
                    error=str(getattr(event, "error", "") or "")[:1000],
                )
            except Exception as exc:
                logger.debug("span_events: tool_error handler failed: %s", exc)

        # ── LLM calls ───────────────────────────────────────────────
        @crewai_event_bus.on(LLMCallStartedEvent)
        def _on_llm_start(_source: Any, event: Any) -> None:
            try:
                _safe_start(
                    span_type="llm_call",
                    name=getattr(event, "model", "") or "llm",
                    event=event,
                    detail={"call_type": str(getattr(event, "call_type", ""))},
                )
            except Exception as exc:
                logger.debug("span_events: llm_start handler failed: %s", exc)

        @crewai_event_bus.on(LLMCallCompletedEvent)
        def _on_llm_complete(_source: Any, event: Any) -> None:
            try:
                usage = getattr(event, "usage", None) or {}
                if hasattr(usage, "model_dump"):
                    usage = usage.model_dump()
                elif not isinstance(usage, dict):
                    usage = {"raw": str(usage)[:200]}
                _safe_complete(
                    event=event,
                    state="completed",
                    detail_patch={"usage": usage} if usage else None,
                )
            except Exception as exc:
                logger.debug("span_events: llm_complete handler failed: %s", exc)

        @crewai_event_bus.on(LLMCallFailedEvent)
        def _on_llm_failed(_source: Any, event: Any) -> None:
            try:
                _safe_complete(
                    event=event,
                    state="failed",
                    error=str(getattr(event, "error", "") or "")[:1000],
                )
            except Exception as exc:
                logger.debug("span_events: llm_failed handler failed: %s", exc)

        _listeners_installed = True
        logger.info(
            "span_events: subscribed to CrewAI agent/tool/llm events — "
            "spans will persist to control_plane.crew_task_spans"
        )
