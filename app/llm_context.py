"""
llm_context.py — Per-call metadata propagation for the LLM telemetry pipeline.

The telemetry recorder in `rate_throttle.py` hooks into litellm at a layer
that has no access to orchestrator-level concepts like "which crew is this
call part of" or "what task type". This module closes that gap with a
`contextvars.ContextVar` scope that the orchestrator sets before entering
a crew and that the recorder reads on each completion.

ContextVars propagate naturally through `asyncio`. For ThreadPoolExecutor
dispatch, use `contextvars.copy_context().run(...)` — the `run_in_context`
helper here does this for you.

Usage:
    from app.llm_context import scope, current, run_in_context
    from app.llm_catalog import canonical_task_type

    with scope(crew_name="coding", role="coding",
               task_type=canonical_task_type(role="coding")):
        result = CodingCrew().run(task)

    # Inside the recorder:
    ctx = current()
    task_type = ctx.task_type if ctx else "general"
"""

from __future__ import annotations

import contextvars
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator, TypeVar


@dataclass(frozen=True, slots=True)
class CallContext:
    """Immutable per-crew-run metadata visible to the telemetry recorder."""
    crew_name: str
    role: str
    task_type: str
    started_at: float  # time.monotonic() at scope entry — coarse crew-level timing


_ctx: contextvars.ContextVar[CallContext | None] = contextvars.ContextVar(
    "llm_call_ctx", default=None,
)


@contextmanager
def scope(crew_name: str, role: str, task_type: str) -> Iterator[CallContext]:
    """Set the per-call metadata for the duration of the block.

    Nested scopes stack correctly — the outer value is restored on exit.
    """
    ctx = CallContext(
        crew_name=crew_name or "",
        role=role or "",
        task_type=task_type or "general",
        started_at=time.monotonic(),
    )
    token = _ctx.set(ctx)
    try:
        yield ctx
    finally:
        _ctx.reset(token)


def current() -> CallContext | None:
    """Return the active CallContext, or None if no scope is active."""
    return _ctx.get()


def current_task_type(default: str = "general") -> str:
    """Convenience: return the active task_type or a default."""
    ctx = _ctx.get()
    return ctx.task_type if ctx else default


T = TypeVar("T")


def run_in_context(
    fn: Callable[..., T], /, *args, **kwargs,
) -> Callable[[], T]:
    """Bind the caller's current ContextVar state to ``fn``.

    Returns a zero-argument callable that, when invoked from any
    thread, executes ``fn(*args, **kwargs)`` with the context snapshot
    taken at *bind* time. Use this when dispatching to a
    ``ThreadPoolExecutor``; plain ``submit`` drops ContextVars because
    worker threads start with an empty context.

    Usage:
        executor.submit(run_in_context(worker_fn, arg1, arg2))
    """
    ctx = contextvars.copy_context()

    def _bound() -> T:
        return ctx.run(fn, *args, **kwargs)

    _bound.__name__ = getattr(fn, "__name__", "ctx_bound")
    return _bound
