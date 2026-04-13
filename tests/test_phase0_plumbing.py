"""
Tests for Phase 0 plumbing utilities.

Covers:
  - app.paths (constants, ensure_dirs, under_workspace)
  - app.json_store (load, save, update, append, retention, default)
  - app.thread_pools (get_pool idempotency, named helpers, shutdown)
  - app.lazy_imports (settings caching)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ── app.paths ────────────────────────────────────────────────────────

class TestPaths:
    def test_workspace_root_env_override(self):
        # Reimporting with WORKSPACE_ROOT set picks up the override.
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"WORKSPACE_ROOT": tmp}):
                sys.modules.pop("app.paths", None)
                import app.paths as paths
                assert str(paths.WORKSPACE_ROOT) == str(Path(tmp).resolve())

    def test_constants_are_paths(self):
        sys.modules.pop("app.paths", None)
        import app.paths as paths
        for name in ("ERROR_JOURNAL", "AUDIT_JOURNAL", "AGENT_STATE",
                     "LOGS_DIR", "SUBIA_SELF_DIR", "KERNEL_STATE"):
            assert isinstance(getattr(paths, name), Path), name

    def test_ensure_dirs_creates_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"WORKSPACE_ROOT": tmp}):
                sys.modules.pop("app.paths", None)
                import app.paths as paths
                paths.ensure_dirs()
                assert paths.LOGS_DIR.is_dir()
                assert paths.SUBIA_SELF_DIR.is_dir()
                assert paths.SUBIA_WORKSPACE_DIR.is_dir()

    def test_under_workspace_accepts_inside(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"WORKSPACE_ROOT": tmp}):
                sys.modules.pop("app.paths", None)
                import app.paths as paths
                assert paths.under_workspace(paths.WORKSPACE_ROOT / "x.txt")
                assert paths.under_workspace(paths.LOGS_DIR / "y.log")

    def test_under_workspace_rejects_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"WORKSPACE_ROOT": tmp}):
                sys.modules.pop("app.paths", None)
                import app.paths as paths
                assert not paths.under_workspace("/etc/passwd")
                assert not paths.under_workspace(paths.WORKSPACE_ROOT / ".." / ".." / "etc")


# ── app.json_store ───────────────────────────────────────────────────

class TestJsonStore:
    def test_load_returns_default_when_missing(self, tmp_path):
        from app.json_store import JsonStore
        store = JsonStore(tmp_path / "missing.json", default={"k": 1})
        loaded = store.load()
        assert loaded == {"k": 1}
        # Default is deep-copied: mutating returned object must not affect store
        loaded["k"] = 99
        assert store.load() == {"k": 1}

    def test_save_and_load_roundtrip(self, tmp_path):
        from app.json_store import JsonStore
        store = JsonStore(tmp_path / "s.json", default={})
        store.save({"hello": "world", "n": 42})
        assert store.load() == {"hello": "world", "n": 42}

    def test_retention_limit_caps_list(self, tmp_path):
        from app.json_store import JsonStore
        store = JsonStore(tmp_path / "list.json", retention_limit=3, default=[])
        store.save([1, 2, 3, 4, 5])
        assert store.load() == [3, 4, 5]

    def test_retention_limit_ignored_for_dict(self, tmp_path):
        from app.json_store import JsonStore
        store = JsonStore(tmp_path / "d.json", retention_limit=3, default={})
        store.save({"a": 1, "b": 2, "c": 3, "d": 4})
        assert store.load() == {"a": 1, "b": 2, "c": 3, "d": 4}

    def test_update_callback_transforms(self, tmp_path):
        from app.json_store import JsonStore
        store = JsonStore(tmp_path / "u.json", default=[])
        store.update(lambda xs: xs + [1])
        store.update(lambda xs: xs + [2])
        assert store.load() == [1, 2]

    def test_update_none_return_saves_mutations(self, tmp_path):
        from app.json_store import JsonStore
        store = JsonStore(tmp_path / "m.json", default={})

        def mutate(data):
            data["added"] = True
            return None  # In-place — None means "save as-is".

        store.update(mutate)
        assert store.load() == {"added": True}

    def test_append_helper(self, tmp_path):
        from app.json_store import JsonStore
        store = JsonStore(tmp_path / "a.json", default=[])
        store.append({"x": 1})
        store.append({"x": 2})
        assert store.load() == [{"x": 1}, {"x": 2}]

    def test_load_recovers_from_corrupt(self, tmp_path):
        from app.json_store import JsonStore
        p = tmp_path / "bad.json"
        p.write_text("{ not json")
        store = JsonStore(p, default={"fallback": True})
        assert store.load() == {"fallback": True}

    def test_save_is_atomic(self, tmp_path):
        """Verify no partial file is left after a successful save."""
        from app.json_store import JsonStore
        store = JsonStore(tmp_path / "atomic.json", default={})
        store.save({"complete": True})
        # Only the target should exist — no .tmp leftovers.
        leftover = [p for p in tmp_path.iterdir()
                    if p.name != "atomic.json"]
        assert leftover == [], f"leftover temp files: {leftover}"

    def test_clear_resets_to_default(self, tmp_path):
        from app.json_store import JsonStore
        store = JsonStore(tmp_path / "c.json", default=[])
        store.save([1, 2, 3])
        store.clear()
        assert store.load() == []


# ── app.thread_pools ─────────────────────────────────────────────────

class TestThreadPools:
    def setup_method(self):
        # Reset pool registry so each test starts fresh.
        from app.thread_pools import _pools
        _pools.clear()

    def teardown_method(self):
        from app.thread_pools import shutdown_all
        shutdown_all(wait=False)

    def test_get_pool_is_idempotent(self):
        from app.thread_pools import get_pool
        p1 = get_pool("test", max_workers=2)
        p2 = get_pool("test", max_workers=99)  # Second call's sizing ignored.
        assert p1 is p2

    def test_named_helpers_return_consistent_instances(self):
        from app.thread_pools import commander_pool, ctx_pool, firebase_pool
        assert commander_pool() is commander_pool()
        assert ctx_pool() is ctx_pool()
        assert firebase_pool() is firebase_pool()
        assert commander_pool() is not ctx_pool()

    def test_pool_executes_work(self):
        from app.thread_pools import get_pool
        pool = get_pool("test-exec", max_workers=2)
        fut = pool.submit(lambda x: x * 2, 21)
        assert fut.result(timeout=2) == 42

    def test_shutdown_clears_registry(self):
        from app.thread_pools import get_pool, _pools, shutdown_all
        get_pool("a")
        get_pool("b")
        assert len(_pools) == 2
        shutdown_all(wait=False)
        assert len(_pools) == 0


# ── app.lazy_imports ────────────────────────────────────────────────

class TestLazyImports:
    def test_settings_caches(self):
        from app import lazy_imports
        lazy_imports.settings.cache_clear()
        with patch("app.config.get_settings") as mock_gs:
            mock_gs.return_value = {"ok": True}
            r1 = lazy_imports.settings()
            r2 = lazy_imports.settings()
            assert r1 is r2
            # Called exactly once thanks to lru_cache
            assert mock_gs.call_count == 1
