"""The request cost tracker must not outlive its request on a pooled thread.

Bonus finding of ``reports/GATE_DIAGNOSIS_2026-07-25.md``. ``_request_cost`` is
a ContextVar and both ``main._commander_pool`` and
``parallel_runner._pool`` are process-lifetime, so a tracker left set in a
worker persists into whatever that worker runs next — including work belonging
to a different request.

That matters because ``start_request_tracking`` is deliberately nesting-aware:
it returns an *existing* tracker rather than creating one. A leaked tracker
therefore made the next request ADOPT the previous request's ledger, inheriting
its spend for every budget check derived from it. Live consequence: a creative
run reported 71,095 tokens and $0.198364 it never spent, and aborted in 0.32 s
against a budget it had already "used".

Two leak vectors, both pinned here:
  * ``parallel_runner._throttled`` set the tracker in a worker and never
    restored it.
  * ``Commander.handle`` only finalized on the happy path.
"""
from concurrent.futures import ThreadPoolExecutor

import pytest


def test_parallel_runner_restores_the_worker_tracker():
    """After run_parallel, a pool worker must not still carry the tracker."""
    pr = pytest.importorskip("app.crews.parallel_runner")
    rt = pytest.importorskip("app.rate_throttle")

    tracker = rt.RequestCostTracker("request-A")
    rt.set_active_tracker(tracker)
    try:
        seen_inside = []

        def _task():
            seen_inside.append(rt.get_active_tracker())
            return "done"

        results = pr.run_parallel([("t", _task)], timeout_seconds=30)
        assert results[0].success, results[0].error
        # Propagation must still work — that part was correct.
        assert seen_inside == [tracker]
    finally:
        rt.set_active_tracker(None)

    # Now probe the pool: whatever worker ran the task must be clean.
    leaked = []
    with_probe = [
        pr._pool.submit(lambda: leaked.append(rt.get_active_tracker()))
        for _ in range(8)
    ]
    for future in with_probe:
        future.result(timeout=30)

    assert all(t is None for t in leaked), (
        f"tracker leaked into pooled thread(s): {leaked!r} — the next request "
        "on that thread would adopt this ledger"
    )


def test_parallel_runner_preserves_a_nested_callers_tracker():
    """Nested fan-out re-enters run_parallel from a worker that has a tracker.

    Restoring must put back what was there, not blanket-clear it.
    """
    pr = pytest.importorskip("app.crews.parallel_runner")
    rt = pytest.importorskip("app.rate_throttle")

    outer = rt.RequestCostTracker("outer")
    inner = rt.RequestCostTracker("inner")
    observed = {}

    def _worker():
        rt.set_active_tracker(outer)
        try:
            fn = pr.run_parallel.__wrapped__ if hasattr(
                pr.run_parallel, "__wrapped__"
            ) else pr.run_parallel
            # Simulate the wrapper's save/restore with a different tracker.
            prior = rt.get_active_tracker()
            rt.set_active_tracker(inner)
            try:
                observed["during"] = rt.get_active_tracker()
            finally:
                rt.set_active_tracker(prior)
            observed["after"] = rt.get_active_tracker()
            return fn is not None
        finally:
            rt.set_active_tracker(None)

    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(_worker).result(timeout=30)

    assert observed["during"] is inner
    assert observed["after"] is outer, "the outer tracker must survive nesting"


def test_handle_finalizes_the_tracker_even_when_handle_locked_raises():
    """Any exception between start and finalize used to strand the tracker."""
    orch = pytest.importorskip("app.agents.commander.orchestrator")
    rt = pytest.importorskip("app.rate_throttle")

    commander = orch.Commander.__new__(orch.Commander)

    def _boom(*args, **kwargs):
        # Mimic _handle_locked: start tracking, then die before finalize.
        rt.start_request_tracking("request-that-fails")
        raise RuntimeError("routing exploded")

    rt.set_active_tracker(None)
    object.__setattr__(commander, "_handle_locked", _boom)
    try:
        with pytest.raises(RuntimeError, match="routing exploded"):
            commander.handle("a question", sender="tester")
    finally:
        leaked = rt.get_active_tracker()
        rt.set_active_tracker(None)

    assert leaked is None, (
        "handle() must clear the request tracker in a finally — otherwise the "
        "next request on this pooled thread adopts this one's ledger"
    )


def test_handle_clears_a_pending_no_answer_signal():
    """A no-answer signal must not leak into the next request either."""
    orch = pytest.importorskip("app.agents.commander.orchestrator")
    outcome = pytest.importorskip("app.crews.outcome")
    rt = pytest.importorskip("app.rate_throttle")

    commander = orch.Commander.__new__(orch.Commander)

    def _sets_signal(*args, **kwargs):
        outcome.record_no_answer("creative", "budget exhausted")
        return "some reply"

    object.__setattr__(commander, "_handle_locked", _sets_signal)
    outcome.clear_no_answer()
    try:
        assert commander.handle("a question", sender="tester") == "some reply"
    finally:
        rt.set_active_tracker(None)

    assert outcome.consume_no_answer() is None, (
        "a stale no-answer signal would suppress the quality gates for the "
        "next request served by this thread"
    )
