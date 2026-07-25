"""
parallel_runner.py — Shared thread pool for running crews and sub-agents in parallel.

Provides run_parallel() which takes a list of callables, executes them
concurrently, and returns results with error isolation (one failure
doesn't kill the others).

Ollama concurrency control:
  Each crew/sub-agent makes multiple LLM calls (tool loops), so running
  N crews in parallel can exceed Ollama's OLLAMA_NUM_PARALLEL capacity.
  A semaphore limits how many crews hit Ollama simultaneously, queuing
  the rest at the application level rather than timing out in Ollama.
"""

import logging
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Stage 4.4 — parallel-crew head-of-line cap. Previously default 600 s let one
# slow crew block the final response for 10 min. 120 s covers even a slow hard
# task while keeping the tail bounded. Env-overridable for debugging.
_PARALLEL_DEFAULT_TIMEOUT = int(os.environ.get("PARALLEL_CREW_TIMEOUT", "120"))

# Single shared pool for the entire process — caps total concurrency
_pool = ThreadPoolExecutor(
    max_workers=settings.thread_pool_size,
    thread_name_prefix="crew-parallel",
)

# Ollama concurrency gate — limit how many crews run LLM calls at once.
# With OLLAMA_NUM_PARALLEL=4 and each crew making 3-8 LLM calls,
# allowing 2 concurrent crews keeps total in-flight requests manageable.
_ollama_concurrency = getattr(settings, "ollama_max_concurrent_crews", 4)
_ollama_semaphore = threading.Semaphore(_ollama_concurrency)

@dataclass
class ParallelResult:
    """Result from a single parallel task."""
    label: str
    success: bool
    result: str | None = None
    error: str | None = None

def run_parallel(
    tasks: list[tuple[str, Callable[[], str]]],
    timeout_seconds: int = _PARALLEL_DEFAULT_TIMEOUT,
    on_complete: Callable[["ParallelResult"], None] | None = None,
) -> list[ParallelResult]:
    """
    Run multiple callables in parallel and collect results.

    Args:
        tasks: List of (label, callable) tuples.  Each callable should
               return a string result.
        timeout_seconds: Max time to wait for all tasks (default 10 min).
        on_complete: Optional callback fired as each task finishes (for
                     streaming partial results to the user).

    Returns:
        List of ParallelResult in the same order as input tasks.
    """
    if not tasks:
        return []

    # Capture the parent thread's request cost tracker for propagation
    from app.rate_throttle import get_active_tracker, set_active_tracker
    parent_tracker = get_active_tracker()

    def _throttled(fn):
        """Wrap callable with semaphore so only N crews hit Ollama at once.
        Also propagates the request cost tracker to child threads.

        The propagated tracker is RESTORED afterwards (2026-07-25). ``_pool``
        is process-lifetime, so a ContextVar left set in a worker persists into
        whatever that worker runs next — including a task belonging to a
        different request. Since ``start_request_tracking`` is nesting-aware
        and returns an already-set tracker instead of creating a fresh one, a
        leaked tracker made the next request inherit the previous request's
        accumulated spend, silently shrinking every per-crew budget derived
        from it. See reports/GATE_DIAGNOSIS_2026-07-25.md.
        """
        if parent_tracker is None:
            with _ollama_semaphore:
                return fn()
        # Restore whatever this worker had before, rather than clearing
        # outright: nested fan-out legitimately re-enters run_parallel from a
        # worker that already carries a tracker, and that one must survive.
        prior_tracker = get_active_tracker()
        set_active_tracker(parent_tracker)
        try:
            with _ollama_semaphore:
                return fn()
        finally:
            set_active_tracker(prior_tracker)

    futures = {}
    for label, fn in tasks:
        future = _pool.submit(_throttled, fn)
        futures[future] = label

    results_map: dict[str, ParallelResult] = {}
    try:
        for future in as_completed(futures, timeout=timeout_seconds):
            label = futures[future]
            try:
                result = future.result()
                pr = ParallelResult(label=label, success=True, result=str(result))
                results_map[label] = pr
                logger.info(f"Parallel task '{label}' completed successfully")
            except Exception as exc:
                logger.error(f"Parallel task '{label}' failed: {exc}")
                pr = ParallelResult(label=label, success=False, error=str(exc)[:300])
                results_map[label] = pr

            # Fire callback for streaming / early-exit
            if on_complete is not None:
                try:
                    on_complete(pr)
                except Exception:
                    logger.debug(f"on_complete callback failed for '{label}'", exc_info=True)
    except TimeoutError:
        # Stage 4.4 — try to cancel the pending stragglers. NOTE:
        # Future.cancel() only succeeds for futures that haven't started
        # running yet (still queued behind the pool's max_workers limit);
        # once a worker thread has actually begun executing the callable,
        # cancel() is a documented no-op and the thread keeps running to
        # its natural completion — holding its _ollama_semaphore slot and
        # a _pool worker slot for as long as that takes. Distinguish the
        # two cases so the log (and any operator reading it) isn't misled
        # into thinking "cancelling" actually stopped anything running.
        pending = [f for f in futures if not f.done()]
        cancelled = [f for f in pending if f.cancel()]
        orphaned = [f for f in pending if f not in cancelled]
        logger.warning(
            "run_parallel: timeout %ds reached — %d crew(s) not yet started "
            "were cancelled cleanly (%s); %d crew(s) already running cannot "
            "be interrupted and will keep executing in the background, "
            "holding a worker/semaphore slot until they finish on their own "
            "(%s). Best-effort results returned now.",
            timeout_seconds,
            len(cancelled), ", ".join(futures[f] for f in cancelled) or "none",
            len(orphaned), ", ".join(futures[f] for f in orphaned) or "none",
        )
        # Orphaned futures are otherwise a silent black hole — nothing ever
        # looks at their eventual result once the caller has moved on. Log
        # what happens to them so a slow/hung crew is at least visible in
        # the logs after the fact, instead of vanishing without a trace.
        for f in orphaned:
            label = futures[f]

            def _log_late_result(fut: Future, _label: str = label) -> None:
                try:
                    fut.result()
                    logger.warning(
                        "run_parallel: orphaned crew '%s' finished AFTER "
                        "its caller already gave up and returned a response",
                        _label,
                    )
                except Exception as exc:
                    logger.warning(
                        "run_parallel: orphaned crew '%s' failed AFTER its "
                        "caller already gave up: %s", _label, str(exc)[:300],
                    )

            f.add_done_callback(_log_late_result)

    # Return in original order; mark missing (timed-out) tasks
    ordered = []
    for label, _ in tasks:
        if label in results_map:
            ordered.append(results_map[label])
        else:
            ordered.append(ParallelResult(
                label=label, success=False, error="Timed out",
            ))
    return ordered
