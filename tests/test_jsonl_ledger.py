"""Tests for the JsonlLedger primitive (Phase E.1, 2026-05-22).

Covers the public surface:

  * ``append`` round-trips a record
  * ``iter_all`` / ``load_all`` yield records in append order
  * Malformed rows are skipped, not raised
  * ``stats`` returns ``{rows, bytes, last_ts}``
  * ``reset_for_tests`` overrides the path
  * Custom ``serialise`` and ``rehydrate`` callbacks honored
  * Custom ``ts_field`` works
  * Thread-safety (concurrent appends produce N rows)
"""
from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock

import pytest


_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())


from app.utils.jsonl_ledger import JsonlLedger  # noqa: E402


# ── Test record types ───────────────────────────────────────────────


@dataclass
class _Row:
    """Simple dataclass record."""

    id: str
    ts: str
    payload: str = ""


@dataclass
class _RowWithCreatedAt:
    """Same shape but with a non-default ts field name."""

    id: str
    created_at: str
    payload: str = ""


# ── Helpers ─────────────────────────────────────────────────────────


def _make_ledger(path: Path, **overrides) -> JsonlLedger[_Row]:
    """Factory for a standard ledger pointing at ``path``."""
    defaults = dict(
        name="test",
        default_path=lambda: path,
        rehydrate=lambda d: _Row(**d),
    )
    defaults.update(overrides)
    return JsonlLedger[_Row](**defaults)


# ── Basic round-trip ────────────────────────────────────────────────


class TestRoundTrip:
    def test_append_and_iter_one_row(self, tmp_path):
        ledger = _make_ledger(tmp_path / "test.jsonl")
        ledger.append(_Row(id="a", ts="2026-05-22T10:00:00Z"))
        rows = list(ledger.iter_all())
        assert len(rows) == 1
        assert rows[0].id == "a"

    def test_load_all_matches_iter_all(self, tmp_path):
        ledger = _make_ledger(tmp_path / "test.jsonl")
        for i in range(5):
            ledger.append(_Row(id=str(i), ts=f"2026-05-22T10:0{i}:00Z"))
        from_iter = list(ledger.iter_all())
        from_load = ledger.load_all()
        assert len(from_iter) == 5
        assert from_iter == from_load

    def test_preserves_append_order(self, tmp_path):
        ledger = _make_ledger(tmp_path / "test.jsonl")
        ids = ["alpha", "bravo", "charlie", "delta", "echo"]
        for i, id_ in enumerate(ids):
            ledger.append(_Row(id=id_, ts=f"2026-05-22T10:0{i}:00Z"))
        loaded = ledger.load_all()
        assert [r.id for r in loaded] == ids

    def test_missing_file_yields_nothing(self, tmp_path):
        # No file ever written
        ledger = _make_ledger(tmp_path / "absent.jsonl")
        assert ledger.load_all() == []
        assert list(ledger.iter_all()) == []


# ── Tolerance to malformed input ────────────────────────────────────


class TestMalformedRows:
    def test_malformed_json_skipped(self, tmp_path):
        path = tmp_path / "test.jsonl"
        ledger = _make_ledger(path)
        ledger.append(_Row(id="ok", ts="2026-05-22T10:00:00Z"))
        # Hand-corrupt the file
        with path.open("a", encoding="utf-8") as fp:
            fp.write("not json {\n")
            fp.write("\n")  # empty
            fp.write('{"id": "good", "ts": "2026-05-22T10:01:00Z"}\n')
        rows = ledger.load_all()
        assert len(rows) == 2
        assert {r.id for r in rows} == {"ok", "good"}

    def test_wrong_shape_skipped(self, tmp_path):
        path = tmp_path / "test.jsonl"
        ledger = _make_ledger(path)
        with path.open("w", encoding="utf-8") as fp:
            fp.write('{"id": "ok", "ts": "now"}\n')
            fp.write('{"nope": "missing required fields"}\n')
            fp.write('"a string at top level"\n')
            fp.write('[1,2,3]\n')
            fp.write('{"id": "ok2", "ts": "now2"}\n')
        rows = ledger.load_all()
        assert len(rows) == 2

    def test_rehydrate_raises_skipped(self, tmp_path):
        def _strict_rehydrate(d):
            if d.get("id") == "boom":
                raise ValueError("nope")
            return _Row(**d)

        path = tmp_path / "test.jsonl"
        ledger = _make_ledger(path, rehydrate=_strict_rehydrate)
        with path.open("w", encoding="utf-8") as fp:
            fp.write('{"id": "ok", "ts": "1"}\n')
            fp.write('{"id": "boom", "ts": "2"}\n')
            fp.write('{"id": "ok2", "ts": "3"}\n')
        rows = ledger.load_all()
        assert {r.id for r in rows} == {"ok", "ok2"}


# ── Stats ───────────────────────────────────────────────────────────


class TestStats:
    def test_empty_stats(self, tmp_path):
        ledger = _make_ledger(tmp_path / "missing.jsonl")
        s = ledger.stats()
        assert s == {"rows": 0, "bytes": 0, "last_ts": ""}

    def test_populated_stats(self, tmp_path):
        ledger = _make_ledger(tmp_path / "test.jsonl")
        for i in range(3):
            ledger.append(_Row(id=str(i), ts=f"2026-05-22T10:0{i}:00Z"))
        s = ledger.stats()
        assert s["rows"] == 3
        assert s["bytes"] > 0
        assert s["last_ts"] == "2026-05-22T10:02:00Z"

    def test_stats_ignore_malformed_for_last_ts(self, tmp_path):
        path = tmp_path / "test.jsonl"
        ledger = _make_ledger(path)
        with path.open("w", encoding="utf-8") as fp:
            fp.write('{"id": "ok", "ts": "2026-05-22T10:00:00Z"}\n')
            fp.write("garbage\n")
        # last_ts is the last well-formed row's ts
        s = ledger.stats()
        assert s["last_ts"] == "2026-05-22T10:00:00Z"
        assert s["rows"] == 2  # count includes the garbage line too

    def test_custom_ts_field(self, tmp_path):
        ledger = JsonlLedger[_RowWithCreatedAt](
            name="test",
            default_path=lambda: tmp_path / "test.jsonl",
            rehydrate=lambda d: _RowWithCreatedAt(**d),
            ts_field="created_at",
        )
        ledger.append(
            _RowWithCreatedAt(id="a", created_at="2026-05-22"),
        )
        s = ledger.stats()
        assert s["last_ts"] == "2026-05-22"


# ── Path resolution / test reset ────────────────────────────────────


class TestPathResolution:
    def test_default_path_called_each_time(self, tmp_path):
        # The resolver runs on every operation — late-bound paths win
        active = {"path": tmp_path / "v1.jsonl"}

        ledger = JsonlLedger[_Row](
            name="test",
            default_path=lambda: active["path"],
            rehydrate=lambda d: _Row(**d),
        )
        ledger.append(_Row(id="a", ts="1"))
        # Now swap the active path
        active["path"] = tmp_path / "v2.jsonl"
        ledger.append(_Row(id="b", ts="2"))
        # Each file has its own row
        assert ledger.path() == tmp_path / "v2.jsonl"
        assert (tmp_path / "v1.jsonl").exists()
        assert (tmp_path / "v2.jsonl").exists()

    def test_reset_for_tests_override(self, tmp_path):
        prod_path = tmp_path / "prod.jsonl"
        test_path = tmp_path / "test.jsonl"
        ledger = _make_ledger(prod_path)
        ledger.reset_for_tests(test_path)
        ledger.append(_Row(id="a", ts="1"))
        # Lands in test_path, not prod_path
        assert test_path.exists()
        assert not prod_path.exists()

    def test_reset_for_tests_clear(self, tmp_path):
        prod_path = tmp_path / "prod.jsonl"
        test_path = tmp_path / "test.jsonl"
        ledger = _make_ledger(prod_path)
        ledger.reset_for_tests(test_path)
        ledger.reset_for_tests(None)
        ledger.append(_Row(id="a", ts="1"))
        # Lands back in prod_path
        assert prod_path.exists()
        assert not test_path.exists()


# ── Serialisation ───────────────────────────────────────────────────


class TestSerialisation:
    def test_dataclass_autoserialised(self, tmp_path):
        ledger = _make_ledger(tmp_path / "test.jsonl")
        ledger.append(_Row(id="a", ts="2026-05-22T10:00:00Z", payload="hi"))
        # File should contain a JSON object with all 3 fields
        with (tmp_path / "test.jsonl").open() as fp:
            d = json.loads(fp.readline())
        assert d == {
            "id": "a", "ts": "2026-05-22T10:00:00Z", "payload": "hi",
        }

    def test_custom_serialise_callback(self, tmp_path):
        # Convert payload to uppercase before persisting
        def _to_dict(row: _Row) -> dict:
            return {"id": row.id, "ts": row.ts, "payload": row.payload.upper()}

        ledger = _make_ledger(
            tmp_path / "test.jsonl",
            serialise=_to_dict,
        )
        ledger.append(_Row(id="a", ts="1", payload="hello"))
        loaded = ledger.load_all()
        assert loaded[0].payload == "HELLO"

    def test_non_serialisable_skipped_with_warning(self, tmp_path, caplog):
        def _broken(row):
            raise RuntimeError("can't serialise")

        ledger = _make_ledger(
            tmp_path / "test.jsonl",
            serialise=_broken,
        )
        ledger.append(_Row(id="a", ts="1"))
        # No row written (broken serialise skipped); file may not even exist
        assert ledger.load_all() == []


# ── Thread safety ───────────────────────────────────────────────────


class TestThreadSafety:
    def test_concurrent_appends_all_succeed(self, tmp_path):
        ledger = _make_ledger(tmp_path / "test.jsonl")
        n = 50

        def _worker(i: int):
            ledger.append(_Row(id=str(i), ts=f"ts{i}"))

        threads = [
            threading.Thread(target=_worker, args=(i,))
            for i in range(n)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        rows = ledger.load_all()
        assert len(rows) == n
        assert {r.id for r in rows} == {str(i) for i in range(n)}


# ── Integration: benchmarks store still works ───────────────────────


class TestBenchmarksStoreParity:
    """Pin that the existing benchmarks public API survived the
    Phase E.1 migration."""

    def test_append_iter_stats_unchanged(self, tmp_path):
        from app.benchmarks import store as bs
        from app.benchmarks.models import BenchmarkRun
        bs.reset_for_tests(tmp_path / "bench")
        try:
            r = BenchmarkRun(
                task_id="t", model="m",
                ts="2026-05-22T10:00:00+00:00",
                score=1.0, latency_ms=100,
                tokens_in=0, tokens_out=0, cost_usd=0.0,
                output_preview="",
            )
            bs.append_run(r)
            stored = bs.load_all()
            assert len(stored) == 1
            assert stored[0] == r
            s = bs.stats()
            assert s["rows"] == 1
            assert s["last_ts"] == "2026-05-22T10:00:00+00:00"
        finally:
            bs.reset_for_tests(None)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
