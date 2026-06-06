"""
Tests for Phase 0 plumbing utilities.

Covers:
  - app.paths (constants, ensure_dirs, under_workspace)
  - app.json_store (load, save, update, append, retention, default)
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

# TestThreadPools removed 2026-06-06 — app/thread_pools.py was an unadopted
# "Phase 0 plumbing" module (0 production importers; only this test exercised
# it) and was deleted in the same change. app.paths + app.json_store above
# remain live and tested.
