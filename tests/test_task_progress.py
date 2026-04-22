"""Tests for app.observability.task_progress — output-progress heartbeat."""
from __future__ import annotations

import threading
import time

import pytest

from app.observability.task_progress import (
    current_task_id,
    output_progress_count,
    record_output_progress,
    reset_task,
    seconds_since_last_output_progress,
    snapshot_all,
)


def test_unknown_task_id_returns_none():
    assert seconds_since_last_output_progress("never-seen") is None


def test_empty_task_id_returns_none():
    # Defensive: explicit empty string must not conjure a fake entry
    assert seconds_since_last_output_progress("") is None


def test_record_then_read():
    tid = "test-tid-record-then-read"
    reset_task(tid)  # defensive in case a prior test ran the same id
    record_output_progress(tid, note="row:first")
    age = seconds_since_last_output_progress(tid)
    assert age is not None
    assert 0.0 <= age <= 0.5, f"age should be near zero, got {age}"
    assert output_progress_count(tid) == 1


def test_count_increments():
    tid = "test-tid-count"
    reset_task(tid)
    record_output_progress(tid)
    record_output_progress(tid)
    record_output_progress(tid)
    assert output_progress_count(tid) == 3


def test_context_var_fallback():
    tid = "test-tid-ctx-var"
    reset_task(tid)
    token = current_task_id.set(tid)
    try:
        # No explicit task_id — should pick up from context-var
        record_output_progress()
        record_output_progress(note="another")
    finally:
        current_task_id.reset(token)
    assert output_progress_count(tid) == 2


def test_record_with_no_context_var_is_noop():
    # Outside a request context, record_output_progress() is a silent no-op
    before = snapshot_all()
    record_output_progress()  # no task_id, no context
    after = snapshot_all()
    # No new entries should appear
    assert set(after.keys()) == set(before.keys())


def test_thread_safety():
    tid = "test-tid-threads"
    reset_task(tid)
    n_threads = 10
    n_per_thread = 50

    def worker():
        for _ in range(n_per_thread):
            record_output_progress(tid)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # No lost updates — 500 total increments
    assert output_progress_count(tid) == n_threads * n_per_thread


def test_reset_drops_entry():
    tid = "test-tid-reset"
    record_output_progress(tid)
    assert seconds_since_last_output_progress(tid) is not None
    reset_task(tid)
    assert seconds_since_last_output_progress(tid) is None
    assert output_progress_count(tid) == 0


def test_age_grows_over_time():
    tid = "test-tid-age"
    reset_task(tid)
    record_output_progress(tid)
    time.sleep(0.1)
    age = seconds_since_last_output_progress(tid)
    assert age is not None
    assert age >= 0.1


def test_snapshot_returns_live_state():
    tid = "test-tid-snapshot"
    reset_task(tid)
    record_output_progress(tid)
    snap = snapshot_all()
    assert tid in snap
    assert snap[tid]["count"] == 1
    assert snap[tid]["seconds_since_last"] >= 0
