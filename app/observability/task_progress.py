"""
task_progress — Per-task "user-visible output was just produced" heartbeat.

Complements :mod:`app.rate_throttle.record_llm_activity`:

* **LLM activity**    — "something LLM-shaped returned" (cheap, cross-cutting,
                        fires whether the LLM produced useful output or an API
                        error).  Good at detecting hung threads.  **Bad** at
                        detecting LLM retry-loops that look busy but produce
                        no deliverable — every 403/429 still counts as
                        "activity" because the LLM did, in fact, cycle.

* **Output progress** — "something the user would actually see was just
                        produced".  Tools call this every time they ship a
                        partial row / chunk / finding.  Strict: a retry loop
                        does **not** advance it, a structurally-stuck task
                        does **not** advance it, only real deliverables
                        advance it.

The progress-gated timeout in :func:`app.main.handle_task` should prefer
output-progress when available and fall back to LLM activity when not
(so un-instrumented tasks don't starve).

Thread safety
-------------
All functions are thread-safe.  Tools invoked from CrewAI's worker thread
pool can record progress without coordinating with the asyncio loop.

Scoping
-------
Progress is scoped by ``task_id``.  For Signal-originating tasks, the
natural task_id is the sender phone number — it's stable for the lifetime
of the handle_task call.  The :data:`current_task_id` :class:`ContextVar`
lets tools read the active task_id without threading it through every
call signature; :func:`app.main.handle_task` sets it before dispatching
to the commander.
"""

from __future__ import annotations

import logging
import threading
import time
from contextvars import ContextVar

logger = logging.getLogger(__name__)

# ── State ────────────────────────────────────────────────────────────
_last_progress: dict[str, float] = {}   # task_id → monotonic ts
_progress_count: dict[str, int] = {}    # task_id → count
_lock = threading.Lock()

# The task id of the request currently being processed in this context.
# Set by ``handle_task`` before it dispatches to the commander so tools
# can read it lazily without per-call threading.
current_task_id: ContextVar[str] = ContextVar("current_task_id", default="")

# Drop entries older than this during GC passes to cap memory growth.
_GC_MAX_AGE_SECONDS = 3600  # 1 hour


# ── Public API ───────────────────────────────────────────────────────
def record_output_progress(task_id: str | None = None, *, note: str = "") -> None:
    """Mark that the given task just produced a user-visible partial
    result (a table row, a paragraph, a search hit, etc.).

    Safe to call from any thread.  A ``task_id`` of ``None`` resolves
    via the :data:`current_task_id` context-var — tools called from
    inside :func:`app.main.handle_task` pick up the right id
    automatically.

    Parameters
    ----------
    task_id : explicit task id; falls back to the context-var if None
              or empty.  A falsy id is a silent no-op (useful for call
              sites that might run outside a request, e.g. tests).
    note    : optional short label for the debug log (not persisted).
    """
    tid = task_id or current_task_id.get()
    if not tid:
        return
    with _lock:
        now = time.monotonic()
        _last_progress[tid] = now
        _progress_count[tid] = _progress_count.get(tid, 0) + 1
        _gc_locked(now)
    if note:
        logger.debug("task_progress: %s → %s", tid[-4:], note[:120])


def seconds_since_last_output_progress(task_id: str) -> float | None:
    """Return seconds since the task last produced output-progress, or
    ``None`` if the task has never emitted (bootstrap / not yet
    instrumented) — callers should treat ``None`` as "no signal
    available, fall back to LLM-activity".
    """
    if not task_id:
        return None
    with _lock:
        ts = _last_progress.get(task_id)
        if ts is None:
            return None
        return time.monotonic() - ts


def output_progress_count(task_id: str) -> int:
    """Total partial results emitted for this task since the tracker
    started.  Useful for "did the task produce at least N rows?" gates."""
    if not task_id:
        return 0
    with _lock:
        return _progress_count.get(task_id, 0)


def reset_task(task_id: str) -> None:
    """Drop a task's counters.  Called at the end of handle_task so
    stale entries from crashed threads don't accumulate."""
    if not task_id:
        return
    with _lock:
        _last_progress.pop(task_id, None)
        _progress_count.pop(task_id, None)


def snapshot_all() -> dict[str, dict[str, float | int]]:
    """Observability helper: return ``{task_id: {last_ts_age, count}}``
    for every currently-tracked task.  Used by tests and by future
    ``/api/cp/*`` debug endpoints."""
    with _lock:
        now = time.monotonic()
        return {
            tid: {
                "seconds_since_last": now - ts,
                "count": _progress_count.get(tid, 0),
            }
            for tid, ts in _last_progress.items()
        }


# ── Internal ─────────────────────────────────────────────────────────
def _gc_locked(now: float) -> None:
    """Drop entries older than ``_GC_MAX_AGE_SECONDS``.  Caller holds
    ``_lock``."""
    cutoff = now - _GC_MAX_AGE_SECONDS
    stale = [tid for tid, ts in _last_progress.items() if ts < cutoff]
    for tid in stale:
        _last_progress.pop(tid, None)
        _progress_count.pop(tid, None)
