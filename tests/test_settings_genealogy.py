"""Tests for app.settings_genealogy — the hash-chained ledger of runtime
settings flips."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import settings_genealogy as sg


@pytest.fixture(autouse=True)
def _tmp_workspace(monkeypatch, tmp_path: Path) -> Path:
    """Re-point the module's workspace resolution at a clean tmp dir."""
    monkeypatch.setattr(sg, "_workspace", lambda: tmp_path)
    return tmp_path


def test_record_change_appends_one_row(_tmp_workspace: Path) -> None:
    row = sg.record_change(
        "tier3_amendment_enabled",
        False,
        True,
        actor="operator",
        reason="Promoting after Q5 closure",
    )
    assert row is not None
    assert row["key"] == "tier3_amendment_enabled"
    assert row["old"] is False
    assert row["new"] is True
    assert row["actor"] == "operator"
    assert row["reason"] == "Promoting after Q5 closure"
    assert row["prev_hash"] == sg.GENESIS_HASH
    assert len(row["hash"]) == 64

    path = _tmp_workspace / "settings_genealogy.jsonl"
    assert path.exists()
    lines = path.read_text().splitlines()
    assert len(lines) == 1


def test_no_op_change_skipped() -> None:
    out = sg.record_change("foo", 5, 5, actor="x", reason="")
    assert out is None


def test_chain_continues_across_calls(_tmp_workspace: Path) -> None:
    r1 = sg.record_change("a", 0, 1, actor="op", reason="")
    r2 = sg.record_change("b", "x", "y", actor="op", reason="")
    r3 = sg.record_change("c", True, False, actor="op", reason="")
    assert r1 and r2 and r3
    assert r2["prev_hash"] == r1["hash"]
    assert r3["prev_hash"] == r2["hash"]

    result = sg.verify_chain()
    assert result == {
        "ok": True,
        "n_rows": 3,
        "first_bad_row": None,
        "reason": None,
    }


def test_record_diff_only_changed_keys(_tmp_workspace: Path) -> None:
    before = {"a": 1, "b": 2, "c": 3}
    after = {"a": 1, "b": 99, "c": 3}
    rows = sg.record_diff(before, after, actor="op", reason="bumping b")
    assert len(rows) == 1
    assert rows[0]["key"] == "b"
    assert rows[0]["old"] == 2
    assert rows[0]["new"] == 99


def test_record_diff_respects_only_keys(_tmp_workspace: Path) -> None:
    """Even if more keys differ in the snapshot, only_keys restricts
    the recorded set — preventing phantom rows when ``snapshot()``
    picks up boot-time defaults the operator never touched."""
    before = {"a": 1, "b": 2, "c": 3}
    after = {"a": 99, "b": 99, "c": 99}
    rows = sg.record_diff(before, after, only_keys=["b"], actor="op")
    assert len(rows) == 1
    assert rows[0]["key"] == "b"


def test_verify_chain_detects_tampered_value(_tmp_workspace: Path) -> None:
    sg.record_change("a", 0, 1, actor="op", reason="")
    sg.record_change("b", 0, 1, actor="op", reason="")

    path = _tmp_workspace / "settings_genealogy.jsonl"
    lines = path.read_text().splitlines()
    # Tamper with row 1's `new` field — hash should no longer match.
    row = json.loads(lines[0])
    row["new"] = 99
    lines[0] = json.dumps(row, separators=(",", ":"), sort_keys=True)
    path.write_text("\n".join(lines) + "\n")

    result = sg.verify_chain()
    assert result["ok"] is False
    assert result["first_bad_row"] == 1
    assert result["reason"] == "hash_mismatch"


def test_verify_chain_detects_broken_prev_link(_tmp_workspace: Path) -> None:
    sg.record_change("a", 0, 1, actor="op", reason="")
    sg.record_change("b", 0, 1, actor="op", reason="")

    path = _tmp_workspace / "settings_genealogy.jsonl"
    lines = path.read_text().splitlines()
    row = json.loads(lines[1])
    row["prev_hash"] = "f" * 64  # break the link
    lines[1] = json.dumps(row, separators=(",", ":"), sort_keys=True)
    path.write_text("\n".join(lines) + "\n")

    result = sg.verify_chain()
    assert result["ok"] is False
    assert result["first_bad_row"] == 2
    assert result["reason"] == "prev_hash_mismatch"


def test_recent_returns_newest_first(_tmp_workspace: Path) -> None:
    sg.record_change("a", 0, 1, actor="op", reason="")
    sg.record_change("b", 0, 1, actor="op", reason="")
    sg.record_change("c", 0, 1, actor="op", reason="")

    rows = sg.recent(limit=2)
    assert len(rows) == 2
    assert rows[0]["key"] == "c"  # newest first
    assert rows[1]["key"] == "b"


def test_last_change_for_returns_most_recent_match(_tmp_workspace: Path) -> None:
    sg.record_change("a", 0, 1, actor="op", reason="")
    sg.record_change("a", 1, 2, actor="op", reason="")
    sg.record_change("b", 0, 1, actor="op", reason="")

    last_a = sg.last_change_for("a")
    assert last_a is not None
    assert last_a["new"] == 2
    assert sg.last_change_for("nonexistent") is None


def test_index_by_key_returns_last_per_key(_tmp_workspace: Path) -> None:
    sg.record_change("a", 0, 1, actor="op", reason="")
    sg.record_change("a", 1, 2, actor="op", reason="")
    sg.record_change("b", 0, 1, actor="op", reason="")

    idx = sg.index_by_key()
    assert set(idx.keys()) == {"a", "b"}
    assert idx["a"]["new"] == 2


def test_json_safe_handles_unusual_types(_tmp_workspace: Path) -> None:
    sg.record_change("complex", None, {"nested": [1, 2, {"k": True}]}, actor="op")
    sg.record_change("setval", [1, 2], {"x", "y"}, actor="op")  # set → sorted list

    rows = sg.recent()
    assert rows[0]["new"] == ["x", "y"]
    assert rows[1]["new"] == {"nested": [1, 2, {"k": True}]}


def test_empty_ledger_verifies_ok(_tmp_workspace: Path) -> None:
    result = sg.verify_chain()
    assert result["ok"] is True
    assert result["n_rows"] == 0
