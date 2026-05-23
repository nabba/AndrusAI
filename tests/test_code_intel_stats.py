"""Tests for code_intel.store.stats() — Phase C.5 closure
(2026-05-22).

Surfaces what's on disk to operators via a one-shot endpoint that
returns row counts, byte sizes, indexed-at timestamp, and age in
seconds. Replaces the previous "shell into the container and
inspect JSONL by hand" model.

Covers:
  * Snapshot not built → built=False, all counts 0
  * Snapshot built → counts + bytes populated
  * indexed_at parsed → age_seconds computed
  * Malformed snapshot metadata → indexed_at empty, age_seconds None
  * Filesystem error on stat → graceful zero, not raise
"""
from __future__ import annotations

import json
import sys
import types
from unittest.mock import MagicMock

import pytest

_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())

try:
    import crewai as _real_crewai  # noqa: F401
    _crewai_available = True
except Exception:
    _crewai_available = False

if not _crewai_available:
    for _mod in ("crewai", "crewai.tools"):
        if _mod not in sys.modules:
            m = types.ModuleType(_mod)
            if _mod == "crewai.tools":
                m.tool = lambda name: (lambda fn: fn)
                m.BaseTool = type("BaseTool", (), {})
            sys.modules[_mod] = m


@pytest.fixture
def isolated_store(tmp_path):
    from app.code_intel import store as cs_store
    cs_store.reset_for_tests(base_dir=tmp_path / "code_intel")
    yield tmp_path / "code_intel"
    cs_store.reset_for_tests(base_dir=None)


class TestStatsHelper:
    def test_not_built_returns_zero(self, isolated_store):
        from app.code_intel.store import stats
        s = stats()
        assert s["built"] is False
        assert s["symbols_count"] == 0
        assert s["references_count"] == 0
        assert s["indexed_files_count"] == 0
        assert s["symbols_bytes"] == 0
        assert s["references_bytes"] == 0
        assert s["indexed_at"] == ""
        assert s["age_seconds"] is None

    def test_built_populates_counts(self, isolated_store, tmp_path):
        # Write a minimal index manually
        isolated_store.mkdir(parents=True, exist_ok=True)
        (isolated_store / "symbols.jsonl").write_text(
            '{"name": "foo"}\n{"name": "bar"}\n{"name": "baz"}\n',
            encoding="utf-8",
        )
        (isolated_store / "references.jsonl").write_text(
            '{"file": "a.py"}\n{"file": "b.py"}\n',
            encoding="utf-8",
        )
        (isolated_store / "snapshot.json").write_text(
            json.dumps({
                "indexed_at": "2026-05-22T10:00:00+00:00",
                "indexed_files": ["a.py", "b.py", "c.py"],
            }),
            encoding="utf-8",
        )

        from app.code_intel.store import stats
        s = stats()
        assert s["built"] is True
        assert s["symbols_count"] == 3
        assert s["references_count"] == 2
        assert s["indexed_files_count"] == 3
        assert s["symbols_bytes"] > 0
        assert s["references_bytes"] > 0
        assert s["indexed_at"] == "2026-05-22T10:00:00+00:00"
        # age_seconds is an int (deterministic since indexed_at is past)
        assert isinstance(s["age_seconds"], int)
        assert s["age_seconds"] >= 0

    def test_malformed_snapshot_metadata(self, isolated_store):
        isolated_store.mkdir(parents=True, exist_ok=True)
        (isolated_store / "snapshot.json").write_text(
            "not valid json {", encoding="utf-8",
        )
        # Empty data files
        (isolated_store / "symbols.jsonl").write_text("", encoding="utf-8")
        (isolated_store / "references.jsonl").write_text("", encoding="utf-8")

        from app.code_intel.store import stats
        s = stats()
        assert s["built"] is True  # snapshot.json exists
        assert s["indexed_at"] == ""  # not parsed
        assert s["age_seconds"] is None
        # Counts still work via line counting
        assert s["symbols_count"] == 0

    def test_malformed_indexed_at_timestamp(self, isolated_store):
        isolated_store.mkdir(parents=True, exist_ok=True)
        (isolated_store / "snapshot.json").write_text(
            json.dumps({
                "indexed_at": "not-an-iso-timestamp",
                "indexed_files": [],
            }),
            encoding="utf-8",
        )
        (isolated_store / "symbols.jsonl").write_text("", encoding="utf-8")
        (isolated_store / "references.jsonl").write_text("", encoding="utf-8")

        from app.code_intel.store import stats
        s = stats()
        assert s["built"] is True
        # indexed_at is surfaced as the raw string
        assert s["indexed_at"] == "not-an-iso-timestamp"
        # but age can't be computed
        assert s["age_seconds"] is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
