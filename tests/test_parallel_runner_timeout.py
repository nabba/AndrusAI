"""Tests for app/crews/parallel_runner.py's timeout/cancellation handling.

``concurrent.futures.Future.cancel()`` only succeeds for a future that
hasn't started running yet — once a worker thread has actually begun
executing the callable, ``cancel()`` is a documented no-op and the thread
keeps running to its natural completion. The pre-fix code called
``f.cancel()`` on every still-pending future at timeout and logged
"cancelling N pending crew(s)" regardless of whether cancellation actually
took effect, which is misleading: a slow/hung crew silently keeps running
in the background, holding a worker-pool slot and an Ollama-semaphore slot
indefinitely, with no trace in the logs once ``run_parallel`` returns.

These tests pin: (1) a future that genuinely hasn't started is reported as
cancelled, (2) a future that IS running past the timeout is reported as
orphaned rather than falsely "cancelled", and (3) an orphaned future's
eventual outcome is still logged via its done-callback instead of vanishing
silently.
"""
from __future__ import annotations

import logging
import threading
import time

import pytest

from app.crews import parallel_runner
from app.crews.parallel_runner import ParallelResult, run_parallel


def test_all_tasks_complete_normally():
    tasks = [
        ("a", lambda: "result-a"),
        ("b", lambda: "result-b"),
    ]
    results = run_parallel(tasks, timeout_seconds=5)

    assert [r.label for r in results] == ["a", "b"]
    assert all(r.success for r in results)
    assert results[0].result == "result-a"
    assert results[1].result == "result-b"


def test_one_task_raises_does_not_kill_the_other():
    def _boom():
        raise ValueError("kaboom")

    tasks = [
        ("ok", lambda: "fine"),
        ("bad", _boom),
    ]
    results = run_parallel(tasks, timeout_seconds=5)
    by_label = {r.label: r for r in results}

    assert by_label["ok"].success is True
    assert by_label["ok"].result == "fine"
    assert by_label["bad"].success is False
    assert "kaboom" in by_label["bad"].error


def test_empty_task_list_returns_empty():
    assert run_parallel([]) == []


def test_orphaned_running_task_is_not_falsely_reported_as_cancelled(caplog):
    """A task that has already started when the timeout fires cannot be
    cancelled — the log must say so, not claim it was cancelled."""
    started = threading.Event()
    release = threading.Event()

    def _slow():
        started.set()
        release.wait(timeout=5)  # released explicitly at the end of the test
        return "finished-late"

    def _fast():
        return "finished-fast"

    tasks = [("slow", _slow), ("fast", _fast)]

    with caplog.at_level(logging.WARNING, logger="app.crews.parallel_runner"):
        # Wait for the slow task to actually start before the timeout
        # fires, so we deterministically land in the "already running,
        # cannot cancel" branch rather than the "still queued" one.
        started_waiter = threading.Thread(target=started.wait, daemon=True)
        started_waiter.start()
        results = run_parallel(tasks, timeout_seconds=0.3)

    assert started.is_set(), "slow task should have started before the 0.3s timeout"

    by_label = {r.label: r for r in results}
    assert by_label["fast"].success is True
    assert by_label["slow"].success is False
    assert by_label["slow"].error == "Timed out"

    warning_text = "\n".join(
        rec.message for rec in caplog.records if rec.levelno == logging.WARNING
    )
    assert "cannot be interrupted" in warning_text
    assert "slow" in warning_text
    # Must NOT claim the running task was cleanly cancelled.
    assert "were cancelled cleanly (slow)" not in warning_text

    release.set()  # let the orphaned thread finish so it doesn't leak


def test_orphaned_task_late_completion_is_logged(caplog):
    """Once an orphaned task eventually finishes (after run_parallel has
    already returned), that outcome must still be logged — it shouldn't
    vanish into a silent black hole."""
    release = threading.Event()
    started = threading.Event()

    def _slow():
        started.set()
        release.wait(timeout=5)
        return "late-result"

    with caplog.at_level(logging.WARNING, logger="app.crews.parallel_runner"):
        results = run_parallel([("slow", _slow)], timeout_seconds=0.2)
        assert results[0].error == "Timed out"

        # Let the orphaned thread actually finish, then give its
        # add_done_callback a moment to fire and log.
        release.set()
        for _ in range(50):
            if any("finished AFTER" in r.message for r in caplog.records):
                break
            time.sleep(0.05)

    late_log = "\n".join(
        rec.message for rec in caplog.records if "finished AFTER" in rec.message
    )
    assert "slow" in late_log


def test_still_queued_task_is_genuinely_cancelled(caplog, monkeypatch):
    """When the pool is saturated, a task that never got a worker slot
    should be reported as cleanly cancelled (the happy path Future.cancel()
    actually supports)."""
    # Shrink the pool to 1 worker for this test so the second task is
    # guaranteed to still be queued (not yet started) when the first
    # blocks past the timeout.
    monkeypatch.setattr(parallel_runner, "_pool", parallel_runner.ThreadPoolExecutor(max_workers=1))
    # Avoid the Ollama semaphore serializing unrelated concurrent test runs.
    monkeypatch.setattr(parallel_runner, "_ollama_semaphore", threading.Semaphore(1))

    release = threading.Event()

    def _blocks_the_only_worker():
        release.wait(timeout=5)
        return "done"

    def _never_gets_a_slot():
        return "should not run before timeout"

    with caplog.at_level(logging.WARNING, logger="app.crews.parallel_runner"):
        results = run_parallel(
            [("blocker", _blocks_the_only_worker), ("queued", _never_gets_a_slot)],
            timeout_seconds=0.3,
        )

    by_label = {r.label: r for r in results}
    assert by_label["queued"].error == "Timed out"

    warning_text = "\n".join(
        rec.message for rec in caplog.records if rec.levelno == logging.WARNING
    )
    assert "cancelled cleanly" in warning_text
    assert "queued" in warning_text

    release.set()


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
