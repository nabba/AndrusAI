"""Tests for pyright sidecar's project-config discovery (2026-05-22).

When a file lives inside a project that has pyrightconfig.json or
pyproject.toml [tool.pyright], pyright should run from that root so
it picks up the operator's rules. The discovery walks up from the
file's path looking for either marker.

Covers:
  * No config above the file → discover returns None
  * pyrightconfig.json directly in file's dir → discover returns dir
  * pyrightconfig.json one level up → discover returns parent
  * pyrightconfig.json deep nested → walk stops at first match
  * pyproject.toml WITH [tool.pyright] → discover returns dir
  * pyproject.toml WITHOUT [tool.pyright] → falls through
  * pyproject.toml with [tool.pyright.execution_environments] (subtable)
    → still detected
  * Walk respects _MAX_CONFIG_WALKUP (no infinite recursion)
  * Walk handles file-not-dir start_path
  * config_root populated on PyrightReport when discovery succeeds
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

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


from app.code_intel import pyright_sidecar as ps  # noqa: E402
from app.code_intel.pyright_sidecar import (  # noqa: E402
    _discover_project_config,
    check_paths,
    PyrightReport,
)


# ── Discovery helper ─────────────────────────────────────────────────


class TestDiscoverProjectConfig:
    def test_no_config_returns_none(self, tmp_path):
        # tmp_path doesn't have a pyright config; system root won't either
        # in a sane filesystem. Up-walk caps stop the search.
        assert _discover_project_config(tmp_path) is None

    def test_pyrightconfig_in_file_dir(self, tmp_path):
        (tmp_path / "pyrightconfig.json").write_text("{}", encoding="utf-8")
        f = tmp_path / "x.py"
        f.write_text("x = 1", encoding="utf-8")
        assert _discover_project_config(f) == tmp_path.resolve()

    def test_pyrightconfig_one_level_up(self, tmp_path):
        (tmp_path / "pyrightconfig.json").write_text("{}", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        f = sub / "x.py"
        f.write_text("x = 1", encoding="utf-8")
        assert _discover_project_config(f) == tmp_path.resolve()

    def test_pyrightconfig_deep_nested(self, tmp_path):
        (tmp_path / "pyrightconfig.json").write_text("{}", encoding="utf-8")
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        f = deep / "x.py"
        f.write_text("x = 1", encoding="utf-8")
        # Walks up until pyrightconfig.json found at tmp_path
        assert _discover_project_config(f) == tmp_path.resolve()

    def test_pyproject_with_tool_pyright(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[tool.pyright]\nstrict = true\n",
            encoding="utf-8",
        )
        f = tmp_path / "x.py"
        f.write_text("x = 1", encoding="utf-8")
        assert _discover_project_config(f) == tmp_path.resolve()

    def test_pyproject_without_tool_pyright_skipped(self, tmp_path):
        # Only [tool.poetry] — not a pyright config
        (tmp_path / "pyproject.toml").write_text(
            "[tool.poetry]\nname = \"x\"\n",
            encoding="utf-8",
        )
        f = tmp_path / "x.py"
        f.write_text("x = 1", encoding="utf-8")
        assert _discover_project_config(f) is None

    def test_pyproject_with_tool_pyright_subtable(self, tmp_path):
        # Detect [tool.pyright.execution_environments] without top-level
        (tmp_path / "pyproject.toml").write_text(
            "[tool.pyright.execution_environments]\n"
            "root = \".\"\n",
            encoding="utf-8",
        )
        f = tmp_path / "x.py"
        f.write_text("x = 1", encoding="utf-8")
        # Detected via the "[tool.pyright." prefix
        assert _discover_project_config(f) == tmp_path.resolve()

    def test_pyrightconfig_wins_over_pyproject(self, tmp_path):
        # Both present at the same level — first match in walk wins.
        # The implementation checks pyrightconfig.json first per level.
        (tmp_path / "pyrightconfig.json").write_text("{}", encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(
            "[tool.pyright]\nstrict = true\n",
            encoding="utf-8",
        )
        f = tmp_path / "x.py"
        f.write_text("x = 1", encoding="utf-8")
        # Both at tmp_path → tmp_path is returned regardless
        assert _discover_project_config(f) == tmp_path.resolve()

    def test_walk_max_depth_respected(self, tmp_path):
        # Build deep nesting beyond _MAX_CONFIG_WALKUP
        deep = tmp_path
        for i in range(15):
            deep = deep / f"d{i}"
            deep.mkdir()
        f = deep / "x.py"
        f.write_text("x = 1", encoding="utf-8")
        # No config anywhere on the path → walk caps, returns None
        # (would loop forever without the cap)
        assert _discover_project_config(f) is None

    def test_handles_directory_start_path(self, tmp_path):
        (tmp_path / "pyrightconfig.json").write_text("{}", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        # Pass the directory itself, not a file in it
        assert _discover_project_config(sub) == tmp_path.resolve()

    def test_handles_corrupted_pyproject(self, tmp_path):
        # Non-UTF-8 pyproject shouldn't crash the discovery
        (tmp_path / "pyproject.toml").write_bytes(b"\x80\x81\x82")
        f = tmp_path / "x.py"
        f.write_text("x = 1", encoding="utf-8")
        # Reading fails silently; falls through (returns None when no
        # other config is found)
        assert _discover_project_config(f) is None


# ── check_paths wires discovery into config_root ─────────────────────


class TestCheckPathsConfigRoot:
    def _fake_proc(self, stdout="{}"):
        m = MagicMock()
        m.stdout = stdout
        m.stderr = ""
        m.returncode = 0
        return m

    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.setattr(ps, "_master_switch_on", lambda: True)
        monkeypatch.setattr(ps, "is_available", lambda: True)

    def test_config_root_populated_when_discovered(self, tmp_path):
        (tmp_path / "pyrightconfig.json").write_text("{}", encoding="utf-8")
        f = tmp_path / "x.py"
        f.write_text("x = 1", encoding="utf-8")
        with patch.object(
            ps.subprocess, "run", return_value=self._fake_proc(),
        ):
            r = check_paths([f])
        assert r.config_root == str(tmp_path.resolve())

    def test_config_root_empty_when_no_config(self, tmp_path):
        f = tmp_path / "x.py"
        f.write_text("x = 1", encoding="utf-8")
        with patch.object(
            ps.subprocess, "run", return_value=self._fake_proc(),
        ):
            r = check_paths([f])
        assert r.config_root == ""

    def test_cwd_pinned_by_caller_still_records_config_root(self, tmp_path):
        """When caller explicitly pins cwd, we still surface the
        config_root (it's debugging info, not a behavior change)."""
        (tmp_path / "pyrightconfig.json").write_text("{}", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        f = sub / "x.py"
        f.write_text("x = 1", encoding="utf-8")
        with patch.object(
            ps.subprocess, "run", return_value=self._fake_proc(),
        ):
            # Caller pins cwd=sub; config_root should still find tmp_path
            r = check_paths([f], cwd=sub)
        # Either the discovered root OR empty — both are valid; the
        # implementation now surfaces it as debugging context.
        assert r.config_root == str(tmp_path.resolve())

    def test_subprocess_cwd_is_discovered_root(self, tmp_path):
        """Pyright is invoked with cwd=<discovered root> — pin via the
        subprocess.run mock so we can assert the cwd argument."""
        (tmp_path / "pyrightconfig.json").write_text("{}", encoding="utf-8")
        deep = tmp_path / "a" / "b"
        deep.mkdir(parents=True)
        f = deep / "x.py"
        f.write_text("x = 1", encoding="utf-8")

        captured: dict = {}

        def _capture(*args, **kwargs):
            captured["cwd"] = kwargs.get("cwd")
            return self._fake_proc()

        with patch.object(ps.subprocess, "run", side_effect=_capture):
            check_paths([f])

        # cwd was set to the project root, not the file's parent dir
        assert captured["cwd"] == str(tmp_path.resolve())


# ── PyrightReport.to_dict includes config_root ───────────────────────


class TestReportSerialization:
    def test_to_dict_includes_config_root(self):
        r = PyrightReport(config_root="/work/myproject")
        d = r.to_dict()
        assert d["config_root"] == "/work/myproject"

    def test_default_empty_string(self):
        r = PyrightReport()
        d = r.to_dict()
        assert d["config_root"] == ""


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
