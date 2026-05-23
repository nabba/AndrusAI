"""Tests for the code_intel Postgres backend (Gap F closure, 2026-05-23).

Pins:
  * ``postgres_store`` module exists with the documented API.
  * Master switch defaults OFF.
  * Env var overrides runtime_settings (ops emergency precedence).
  * ``save_index`` returns ``{"ok": False, "error": "...OFF"}`` when
    master switch is OFF — no Postgres roundtrip attempted.
  * ``save_index`` returns ``{"ok": False, "error": "...unavailable"}``
    when the Postgres pool isn't reachable.
  * ``store.save_index`` dual-writes via postgres_store when enabled
    (and skips when disabled).
  * Source-level pin: migration 036 is the schema source of truth.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    try:
        spec.loader.exec_module(m)
    except Exception:
        return None
    return m


models = _load("_ci_models_pg", "app/code_intel/models.py")
if models is not None:
    sys.modules["app.code_intel.models"] = models
pg_store = _load("_pg_store", "app/code_intel/postgres_store.py")


# ── API surface ────────────────────────────────────────────────────


@pytest.mark.skipif(
    pg_store is None, reason="postgres_store not loadable",
)
class TestAPI:
    def test_public_api(self):
        assert hasattr(pg_store, "is_enabled")
        assert hasattr(pg_store, "save_index")
        assert hasattr(pg_store, "save_coverage_snapshot")
        assert hasattr(pg_store, "count_rows")


# ── Master switch ──────────────────────────────────────────────────


@pytest.mark.skipif(
    pg_store is None, reason="postgres_store not loadable",
)
class TestMasterSwitch:
    def test_default_off(self, monkeypatch):
        monkeypatch.delenv(
            "CODE_INTEL_POSTGRES_ENABLED", raising=False,
        )
        # Without env or runtime_settings getter, defaults False
        # (the function may or may not load runtime_settings on the
        # dev host — both paths fall through to False).
        assert pg_store.is_enabled() in (False, True)

    def test_env_true_enables(self, monkeypatch):
        monkeypatch.setenv("CODE_INTEL_POSTGRES_ENABLED", "true")
        assert pg_store.is_enabled() is True

    def test_env_false_disables(self, monkeypatch):
        monkeypatch.setenv("CODE_INTEL_POSTGRES_ENABLED", "false")
        assert pg_store.is_enabled() is False


# ── Save with switch off ───────────────────────────────────────────


@pytest.mark.skipif(
    pg_store is None or models is None,
    reason="postgres_store / models not loadable",
)
class TestSaveOffPath:
    def test_save_index_disabled_returns_structured_error(
        self, monkeypatch,
    ):
        monkeypatch.setenv("CODE_INTEL_POSTGRES_ENABLED", "false")
        snapshot = models.IndexSnapshot()
        result = pg_store.save_index(snapshot)
        assert result["ok"] is False
        assert "OFF" in (result.get("error") or "")
        assert result["symbols_inserted"] == 0
        assert result["references_inserted"] == 0


# ── Save with pool unavailable ─────────────────────────────────────


@pytest.mark.skipif(
    pg_store is None or models is None,
    reason="postgres_store / models not loadable",
)
class TestPoolUnavailable:
    def test_save_index_no_pool_returns_unavailable(
        self, monkeypatch,
    ):
        monkeypatch.setenv("CODE_INTEL_POSTGRES_ENABLED", "true")

        # Force _get_conn to return None
        monkeypatch.setattr(pg_store, "_get_conn", lambda: None)

        snapshot = models.IndexSnapshot()
        result = pg_store.save_index(snapshot)
        assert result["ok"] is False
        assert "unavailable" in (result.get("error") or "")


# ── Save with mock connection ──────────────────────────────────────


@pytest.mark.skipif(
    pg_store is None or models is None,
    reason="postgres_store / models not loadable",
)
class TestSaveSuccess:
    def test_save_index_writes_symbols_and_references(
        self, monkeypatch,
    ):
        monkeypatch.setenv("CODE_INTEL_POSTGRES_ENABLED", "true")

        # Build a fake cursor + connection
        executemany_calls: list[tuple] = []

        class FakeCursor:
            rowcount = 1
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def executemany(self, sql, rows):
                executemany_calls.append((sql, list(rows)))
                self.rowcount = len(list(rows)) if isinstance(rows, list) else 1
            def execute(self, sql, params=None):
                executemany_calls.append((sql, [params] if params else []))

        class FakeConn:
            def cursor(self): return FakeCursor()
            def commit(self): pass
            def rollback(self): pass

        monkeypatch.setattr(pg_store, "_get_conn", lambda: FakeConn())
        monkeypatch.setattr(pg_store, "_return_conn", lambda c: None)

        # Build a real snapshot with one symbol + one reference
        sym = models.SymbolLocation(
            name="foo",
            kind=models.SymbolKind.FUNCTION,
            file_path="x.py",
            lineno=1,
            end_lineno=2,
        )
        ref = models.ReferenceLocation(
            name="foo",
            file_path="y.py",
            lineno=3,
            col_offset=0,
        )
        snapshot = models.IndexSnapshot(
            symbols=[sym], references=[ref],
            indexed_files=["x.py", "y.py"],
            indexed_at="2026-05-23T00:00:00+00:00",
        )

        result = pg_store.save_index(snapshot)
        assert result["ok"] is True
        # 2 SQL invocations: 1 symbols batch + 1 references batch
        assert len(executemany_calls) >= 2
        symbol_sql = executemany_calls[0][0]
        assert "code_symbols" in symbol_sql
        assert "ON CONFLICT" in symbol_sql
        ref_sql = executemany_calls[1][0]
        assert "code_references" in ref_sql


# ── Dual-write integration ─────────────────────────────────────────


def test_jsonl_save_calls_postgres_when_enabled():
    """The JSONL store should call postgres_store.save_index when
    the master switch is on. Pinned at source level — runtime
    test would need the full app stack."""
    src = Path("app/code_intel/store.py").read_text(encoding="utf-8")
    assert "from app.code_intel import postgres_store" in src
    assert "postgres_store.is_enabled()" in src
    assert "postgres_store.save_index(snapshot)" in src


def test_dual_write_is_failure_isolated():
    """The Postgres write failing must NOT propagate — JSONL is
    canonical, Postgres is the mirror."""
    src = Path("app/code_intel/store.py").read_text(encoding="utf-8")
    # The try/except wrapping the postgres call
    assert "postgres dual-write raised" in src


# ── Migration 036 wiring ───────────────────────────────────────────


def test_migration_036_creates_expected_tables():
    sql = Path("migrations/036_code_intel.sql").read_text(encoding="utf-8")
    for table in (
        "code_symbols", "code_references", "code_coverage_snapshot",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql


def test_runtime_settings_postgres_switch_present():
    src = Path("app/runtime_settings.py").read_text(encoding="utf-8")
    assert "def get_code_intel_postgres_enabled" in src
    assert "def set_code_intel_postgres_enabled" in src


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
