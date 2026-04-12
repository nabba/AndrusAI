"""
trace.py — Lightweight request tracing via contextvars.

Generates a 12-char hex trace ID at the start of each request.
Propagates automatically through asyncio.run_in_executor() (Python 3.12+)
and can be manually set in threading.Thread contexts.

Usage:
    from app.trace import new_trace_id, get_trace_id
    trace_id = new_trace_id()  # Call once at request entry point
    # ... later, in any downstream code:
    tid = get_trace_id()       # Returns same ID (via ContextVar)
"""

import contextvars
import uuid

_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")


def new_trace_id() -> str:
    """Generate a new trace ID and set it in the current context."""
    tid = uuid.uuid4().hex[:12]
    _trace_id.set(tid)
    return tid


def get_trace_id() -> str:
    """Get the current trace ID (empty string if none set)."""
    return _trace_id.get()


def set_trace_id(tid: str) -> None:
    """Set trace ID explicitly (for propagation into background threads)."""
    _trace_id.set(tid)
