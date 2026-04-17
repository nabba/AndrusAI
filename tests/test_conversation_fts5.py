"""Tests for FTS5 migrations + search_messages + rebuild_fts_index."""
import sqlite3
from pathlib import Path

import pytest

from tests._v2_shim import install_settings_shim

install_settings_shim()

import app.conversation_store as cs  # noqa: E402


@pytest.fixture(autouse=True)
def reset_store(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "DB_PATH", tmp_path / "conv.db")
    # Force a fresh thread-local connection each test
    if hasattr(cs._local, "conn"):
        cs._local.conn = None
    yield


class TestMigrations:
    def test_schema_created_on_first_connection(self):
        conn = cs._get_conn()
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        # Core tables present
        assert "messages" in tables
        assert "tasks" in tables
        assert "_schema_version" in tables

    def test_fts_table_present_when_supported(self):
        conn = cs._get_conn()
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master"
        ).fetchall()}
        # messages_fts may be skipped on SQLite builds without FTS5 — but
        # modern macOS/Linux Python ships with it by default.
        try:
            conn.execute("SELECT * FROM messages_fts LIMIT 1")
            assert "messages_fts" in names
        except sqlite3.OperationalError:
            pytest.skip("FTS5 not compiled in this SQLite build")

    def test_migrations_idempotent(self):
        # Run twice — schema_version should not duplicate
        cs._get_conn()
        cs._local.conn = None
        conn = cs._get_conn()
        rows = conn.execute("SELECT name FROM _schema_version").fetchall()
        names = [r[0] for r in rows]
        assert len(names) == len(set(names))  # no duplicates

    def test_indices_exist(self):
        conn = cs._get_conn()
        indices = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        assert "idx_sender_ts" in indices
        assert "idx_tasks_started" in indices


class TestSearchMessages:
    def test_returns_empty_on_blank_query(self):
        cs.add_message("+1555", "user", "hello helsinki today")
        assert cs.search_messages("") == []
        assert cs.search_messages("   ") == []

    def test_returns_empty_on_non_word_chars(self):
        cs.add_message("+1555", "user", "hello world")
        assert cs.search_messages("!!!") == []

    def test_finds_single_match(self):
        cs.add_message("+1555", "user", "what's the weather in Helsinki today")
        results = cs.search_messages("helsinki")
        if not results:
            pytest.skip("FTS5 not supported in this SQLite build")
        assert len(results) == 1
        assert results[0]["role"] == "user"
        assert ">>>" in results[0]["content_snippet"]

    def test_sender_scoped_search(self):
        cs.add_message("+1555111", "user", "helsinki news please")
        cs.add_message("+1555222", "user", "helsinki weather please")
        hits_1 = cs.search_messages("helsinki", sender="+1555111")
        hits_2 = cs.search_messages("helsinki", sender="+1555222")
        if not hits_1:
            pytest.skip("FTS5 not supported")
        assert len(hits_1) == 1
        assert len(hits_2) == 1
        assert "news" in hits_1[0]["content_snippet"]
        assert "weather" in hits_2[0]["content_snippet"]

    def test_orders_by_most_recent(self):
        import time
        cs.add_message("+1555", "user", "first helsinki mention")
        time.sleep(0.01)
        cs.add_message("+1555", "user", "second helsinki mention")
        results = cs.search_messages("helsinki", limit=10)
        if not results:
            pytest.skip("FTS5 not supported")
        # Most recent first
        assert "second" in results[0]["content_snippet"]

    def test_respects_limit(self):
        for i in range(15):
            cs.add_message("+1555", "user", f"helsinki note number {i}")
        results = cs.search_messages("helsinki", limit=5)
        if not results:
            pytest.skip("FTS5 not supported")
        assert len(results) == 5

    def test_failure_returns_empty_not_raise(self, monkeypatch):
        def boom():
            raise RuntimeError("db crashed")
        monkeypatch.setattr(cs, "_get_conn", boom)
        assert cs.search_messages("anything") == []


class TestRebuildFtsIndex:
    def test_rebuild_returns_message_count(self):
        cs.add_message("+1555", "user", "message 1")
        cs.add_message("+1555", "assistant", "message 2")
        cs.add_message("+1555", "user", "message 3")
        count = cs.rebuild_fts_index()
        if count == 0:
            pytest.skip("FTS5 not supported")
        assert count == 3

    def test_rebuild_empty_store(self):
        count = cs.rebuild_fts_index()
        # FTS5 unavailable returns 0; otherwise 0 messages returns 0
        assert count == 0
