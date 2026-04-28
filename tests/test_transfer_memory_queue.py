"""Tests for app.transfer_memory.queue."""

import json
import threading

import pytest

from app.transfer_memory import queue as q
from app.transfer_memory.types import TransferEvent, TransferKind


@pytest.fixture
def tmp_queue_dir(tmp_path, monkeypatch):
    """Redirect queue dir to a temp path for the duration of a test."""
    monkeypatch.setenv("TRANSFER_MEMORY_DIR", str(tmp_path))
    yield tmp_path


def test_append_event_creates_jsonl_line(tmp_queue_dir):
    ok = q.append_event(
        kind=TransferKind.HEALING,
        source_id="error_xyz",
        summary="some summary",
        payload={"foo": "bar"},
    )
    assert ok
    file = tmp_queue_dir / "compile_queue.jsonl"
    assert file.exists()
    lines = file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["kind"] == "healing"
    assert obj["source_id"] == "error_xyz"
    assert obj["payload"] == {"foo": "bar"}


def test_drain_returns_events_and_removes_file(tmp_queue_dir):
    q.append_event(TransferKind.HEALING, "a", payload={})
    q.append_event(TransferKind.EVO_SUCCESS, "b", payload={"hypothesis": "x"})

    events = q.drain()
    assert len(events) == 2
    assert {e.source_id for e in events} == {"a", "b"}
    assert not (tmp_queue_dir / "compile_queue.jsonl").exists()


def test_drain_dedups_by_event_id(tmp_queue_dir):
    """Same kind + source_id → same event_id → drain emits once."""
    q.append_event(TransferKind.HEALING, "dup_id", summary="first")
    q.append_event(TransferKind.HEALING, "dup_id", summary="second")

    events = q.drain()
    assert len(events) == 1


def test_drain_empty_queue_returns_empty_list(tmp_queue_dir):
    assert q.drain() == []


def test_drain_atomic_rename_preserves_concurrent_appends(tmp_queue_dir):
    """Appends made during a drain must land in a fresh queue file
    and be discoverable by a follow-up drain."""
    q.append_event(TransferKind.HEALING, "a")
    q.append_event(TransferKind.HEALING, "b")

    drained: list[TransferEvent] = []

    def drain_thread():
        drained.extend(q.drain())

    def append_thread():
        q.append_event(TransferKind.HEALING, "c")

    t1 = threading.Thread(target=drain_thread)
    t2 = threading.Thread(target=append_thread)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # At least the original 2 events were drained.
    assert len(drained) >= 2

    # A second drain picks up "c" (whether it landed in the first or second
    # drain depends on thread scheduling — the contract is that no event
    # is lost across the rename).
    second = q.drain()
    all_ids = {e.source_id for e in drained} | {e.source_id for e in second}
    assert {"a", "b", "c"}.issubset(all_ids)


def test_push_retry_increments_attempts(tmp_queue_dir):
    evt = TransferEvent(
        event_id="evt_x",
        kind=TransferKind.HEALING,
        source_id="x",
        attempts=0,
    )
    pushed = q.push_retry([evt])
    assert pushed == 1

    retries = q.drain_retries()
    assert len(retries) == 1
    assert retries[0].attempts == 1


def test_push_retry_drops_after_max_attempts(tmp_queue_dir):
    evt = TransferEvent(
        event_id="evt_y",
        kind=TransferKind.HEALING,
        source_id="y",
        attempts=3,  # already at max — push_retry will increment to 4 → drop
    )
    pushed = q.push_retry([evt])
    assert pushed == 0


def test_append_shadow_draft(tmp_queue_dir):
    ok = q.append_shadow_draft({
        "event_id": "evt_z",
        "kind": "healing",
        "draft": {"id": "draft_xfer_aaa", "topic": "test"},
    })
    assert ok
    file = tmp_queue_dir / "shadow_drafts.jsonl"
    assert file.exists()
    line = file.read_text(encoding="utf-8").splitlines()[0]
    obj = json.loads(line)
    assert obj["draft"]["id"] == "draft_xfer_aaa"


def test_cadence_guard_io(tmp_queue_dir):
    assert q.read_last_compile_at() == 0.0
    q.write_last_compile_at(1234567890.5)
    assert q.read_last_compile_at() == 1234567890.5


def test_queue_size(tmp_queue_dir):
    assert q.queue_size() == 0
    q.append_event(TransferKind.HEALING, "a")
    q.append_event(TransferKind.HEALING, "b")
    assert q.queue_size() == 2


def test_invalid_kind_raises_in_from_dict():
    with pytest.raises(ValueError):
        TransferEvent.from_dict({
            "event_id": "x", "kind": "not_a_kind", "source_id": "y",
        })


def test_drain_skips_malformed_lines(tmp_queue_dir):
    """Drain is tolerant to bad lines — never raises, skips them."""
    (tmp_queue_dir / "compile_queue.jsonl").write_text(
        '{"event_id":"evt_a","kind":"healing","source_id":"a","payload":{}}\n'
        'not json at all\n'
        '{"event_id":"evt_b","kind":"unknown_kind","source_id":"b","payload":{}}\n'
        '{"event_id":"evt_c","kind":"healing","source_id":"c","payload":{}}\n',
        encoding="utf-8",
    )
    events = q.drain()
    assert {e.source_id for e in events} == {"a", "c"}
