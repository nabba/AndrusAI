"""Unit tests for the shared SignalTsBridge primitive (consolidation of the 4
copy-pasted Signal-ts → id routing maps). Pure + host-runnable."""
import json
import time

import pytest

from app.signal_ts_bridge import SignalTsBridge


def _bridge(tmp_path, **kw):
    p = tmp_path / "b.json"
    return SignalTsBridge(lambda: p, max_age_seconds=kw.pop("max_age_seconds", 3600), **kw), p


def test_put_get_roundtrip_and_stamps_ts(tmp_path):
    b, p = _bridge(tmp_path)
    b.put("100", {"request_id": "abc"})
    got = b.get("100")
    assert got["request_id"] == "abc"
    assert "created_at_epoch" in got and got["created_at_epoch"] > 0
    # int key coerced to str (callers pass int or str ts)
    assert b.get(100)["request_id"] == "abc"


def test_get_returns_a_copy(tmp_path):
    b, _ = _bridge(tmp_path)
    b.put("1", {"x": "y"})
    g = b.get("1")
    g["x"] = "mutated"
    assert b.get("1")["x"] == "y"  # store unaffected


def test_value_schema_preserved_plus_ts(tmp_path):
    b, p = _bridge(tmp_path)
    b.put("1", {"section_id": "s", "created_at_iso": "2026-01-01T00:00:00+00:00"})
    on_disk = json.loads(p.read_text())["1"]
    assert set(on_disk) == {"section_id", "created_at_iso", "created_at_epoch"}


def test_missing_key_is_none(tmp_path):
    b, _ = _bridge(tmp_path)
    assert b.get("nope") is None
    assert b.get("") is None


def test_purge_on_get_drops_expired(tmp_path):
    b, p = _bridge(tmp_path, max_age_seconds=10)
    # Write an expired entry directly (epoch well in the past).
    p.write_text(json.dumps({"old": {"request_id": "x", "created_at_epoch": time.time() - 999}}))
    assert b.get("old") is None
    # persist_on_get default True → the expired entry was rewritten out of the file
    assert json.loads(p.read_text()) == {}


def test_persist_on_get_false_leaves_file(tmp_path):
    p = tmp_path / "b.json"
    b = SignalTsBridge(lambda: p, max_age_seconds=10, persist_on_get=False)
    p.write_text(json.dumps({"old": {"task_id": "x", "created_at_epoch": time.time() - 999}}))
    assert b.get("old") is None          # purged in-memory → returns None
    assert "old" in json.loads(p.read_text())  # but NOT rewritten (no write on get)


def test_custom_ts_field(tmp_path):
    p = tmp_path / "b.json"
    b = SignalTsBridge(lambda: p, max_age_seconds=10, ts_field="registered_at")
    b.put("1", {"task_id": "t"})
    assert "registered_at" in json.loads(p.read_text())["1"]
    p.write_text(json.dumps({"o": {"task_id": "t", "registered_at": time.time() - 999}}))
    assert b.get("o") is None  # purges by the custom field


def test_max_entries_drops_oldest(tmp_path):
    p = tmp_path / "b.json"
    b = SignalTsBridge(lambda: p, max_age_seconds=3600, max_entries=3)
    for i in range(5):
        b.put(str(i), {"v": i})
        time.sleep(0.005)  # ensure distinct timestamps
    data = json.loads(p.read_text())
    assert len(data) == 3
    assert set(data) == {"2", "3", "4"}  # newest 3 kept


def test_remove_where(tmp_path):
    b, _ = _bridge(tmp_path)
    b.put("1", {"run_id": "keep"})
    b.put("2", {"run_id": "drop"})
    b.put("3", {"run_id": "drop"})
    b.remove_where(lambda v: v.get("run_id") == "drop")
    assert b.get("1") is not None
    assert b.get("2") is None and b.get("3") is None


def test_corrupt_file_is_tolerated(tmp_path):
    b, p = _bridge(tmp_path)
    p.write_text("{ not json")
    assert b.get("1") is None      # no crash
    b.put("1", {"x": "y"})         # recovers
    assert b.get("1")["x"] == "y"
