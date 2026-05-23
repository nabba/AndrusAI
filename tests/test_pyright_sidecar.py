"""Tests for the pyright sidecar (Phase 3 v2, 2026-05-22).

Covers the failure modes that matter most for shipping a subprocess
wrapper into the gateway:

  * Master switch OFF → no subprocess spawn, disabled=True
  * pyright binary absent → available=False, no spawn
  * Empty paths → no spawn, empty report
  * Subprocess timeout → timed_out=True, never hangs the caller
  * Non-JSON stdout → error populated, no diagnostics
  * Well-formed JSON parse → diagnostics extracted with 1-based line/col
  * Severity bucket mapping (error/warning/information/hint → 3-bucket)
  * Diagnostic dataclass round-trip (to_dict)
  * Report.has_errors + .errors + .warnings accessors
  * to_dict adds the derived counters
  * Master-switch round-trip via runtime_settings (skipped on host
    without pydantic_settings)
"""
from __future__ import annotations

import json
import subprocess
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
    PyrightDiagnostic,
    PyrightReport,
    check_paths,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def switch_on(monkeypatch):
    monkeypatch.setattr(ps, "_master_switch_on", lambda: True)


@pytest.fixture
def switch_off(monkeypatch):
    monkeypatch.setattr(ps, "_master_switch_on", lambda: False)


@pytest.fixture
def binary_present(monkeypatch):
    monkeypatch.setattr(ps, "is_available", lambda: True)


@pytest.fixture
def binary_absent(monkeypatch):
    monkeypatch.setattr(ps, "is_available", lambda: False)


# ── Master-switch + availability gating ───────────────────────────────


class TestGating:
    def test_disabled_short_circuits(self, tmp_path, switch_off, binary_present):
        # Even with pyright "available" the switch off path returns
        # without spawning. We assert by patching subprocess.run to
        # blow up — it must not be called.
        with patch.object(ps.subprocess, "run", side_effect=AssertionError):
            r = check_paths([tmp_path / "x.py"])
        assert r.disabled is True
        assert r.diagnostics == []

    def test_binary_absent_short_circuits(
        self, tmp_path, switch_on, binary_absent,
    ):
        with patch.object(ps.subprocess, "run", side_effect=AssertionError):
            r = check_paths([tmp_path / "x.py"])
        assert r.available is False
        assert r.disabled is False
        assert r.diagnostics == []

    def test_empty_paths_no_spawn(self, switch_on, binary_present):
        with patch.object(ps.subprocess, "run", side_effect=AssertionError):
            r = check_paths([])
        assert r.diagnostics == []
        assert r.available is True


# ── Subprocess timeout + crashes ──────────────────────────────────────


class TestSubprocessFailures:
    def test_timeout(self, tmp_path, switch_on, binary_present):
        def _boom(*a, **kw):
            raise subprocess.TimeoutExpired(cmd=a[0], timeout=1.0)
        with patch.object(ps.subprocess, "run", side_effect=_boom):
            r = check_paths([tmp_path / "x.py"], timeout_s=1.0)
        assert r.timed_out is True
        assert r.diagnostics == []
        assert r.available is True

    def test_file_not_found_at_spawn(
        self, tmp_path, switch_on, binary_present,
    ):
        # is_available reported True but the spawn itself raises
        # FileNotFoundError (PATH race). Should fall back to
        # available=False rather than crash.
        with patch.object(ps.subprocess, "run", side_effect=FileNotFoundError):
            r = check_paths([tmp_path / "x.py"])
        assert r.available is False
        assert r.diagnostics == []

    def test_arbitrary_subprocess_exception_recorded(
        self, tmp_path, switch_on, binary_present,
    ):
        with patch.object(
            ps.subprocess, "run", side_effect=OSError("disk full"),
        ):
            r = check_paths([tmp_path / "x.py"])
        assert r.available is True
        assert "disk full" in r.error
        assert r.diagnostics == []


# ── JSON parse ────────────────────────────────────────────────────────


class TestJsonParse:
    def _fake_proc(self, stdout: str, stderr: str = ""):
        m = MagicMock()
        m.stdout = stdout
        m.stderr = stderr
        m.returncode = 0
        return m

    def test_well_formed_json(self, tmp_path, switch_on, binary_present):
        payload = {
            "generalDiagnostics": [
                {
                    "file": "/work/a.py",
                    "severity": "error",
                    "rule": "reportGeneralTypeIssues",
                    "message": "Cannot assign int to str",
                    "range": {
                        "start": {"line": 10, "character": 4},
                        "end": {"line": 10, "character": 12},
                    },
                },
                {
                    "file": "/work/a.py",
                    "severity": "warning",
                    "rule": "reportUnusedImport",
                    "message": "Unused import",
                    "range": {
                        "start": {"line": 2, "character": 0},
                    },
                },
            ],
        }
        with patch.object(
            ps.subprocess, "run",
            return_value=self._fake_proc(json.dumps(payload)),
        ):
            r = check_paths([tmp_path / "a.py"])
        assert len(r.diagnostics) == 2
        # 0-based pyright → 1-based ours
        assert r.diagnostics[0].line == 11
        assert r.diagnostics[0].column == 5
        assert r.diagnostics[0].severity == "error"
        assert r.diagnostics[0].rule == "reportGeneralTypeIssues"
        assert r.diagnostics[1].severity == "warning"
        assert r.has_errors is True

    def test_information_and_hint_bucket_to_info(
        self, tmp_path, switch_on, binary_present,
    ):
        payload = {
            "generalDiagnostics": [
                {"file": "/a.py", "severity": "information", "rule": "x",
                 "message": "i", "range": {"start": {"line": 0, "character": 0}}},
                {"file": "/a.py", "severity": "hint", "rule": "y",
                 "message": "h", "range": {"start": {"line": 0, "character": 0}}},
            ],
        }
        with patch.object(
            ps.subprocess, "run",
            return_value=self._fake_proc(json.dumps(payload)),
        ):
            r = check_paths([tmp_path / "a.py"])
        assert [d.severity for d in r.diagnostics] == ["info", "info"]
        assert r.has_errors is False
        assert r.errors == []
        assert r.warnings == []

    def test_non_json_stdout(self, tmp_path, switch_on, binary_present):
        with patch.object(
            ps.subprocess, "run",
            return_value=self._fake_proc("not json", stderr="crashed at line 5"),
        ):
            r = check_paths([tmp_path / "a.py"])
        assert r.diagnostics == []
        assert "parse" in r.error
        assert "crashed at line 5" in r.error  # stderr surfaced

    def test_missing_diagnostics_key(self, tmp_path, switch_on, binary_present):
        with patch.object(
            ps.subprocess, "run",
            return_value=self._fake_proc(json.dumps({"summary": {}})),
        ):
            r = check_paths([tmp_path / "a.py"])
        # No generalDiagnostics → empty diagnostics, no error
        assert r.diagnostics == []
        assert r.error == ""

    def test_malformed_diagnostic_item_skipped(
        self, tmp_path, switch_on, binary_present,
    ):
        payload = {
            "generalDiagnostics": [
                "not a dict",
                {"file": "/a.py", "severity": "error", "rule": "x",
                 "message": "m", "range": {"start": {"line": 0, "character": 0}}},
            ],
        }
        with patch.object(
            ps.subprocess, "run",
            return_value=self._fake_proc(json.dumps(payload)),
        ):
            r = check_paths([tmp_path / "a.py"])
        assert len(r.diagnostics) == 1


# ── PyrightReport API ─────────────────────────────────────────────────


class TestReportAPI:
    def test_diagnostic_to_dict(self):
        d = PyrightDiagnostic(
            file="/a.py", line=1, column=1,
            severity="error", rule="r", message="m",
        )
        assert d.to_dict() == {
            "file": "/a.py", "line": 1, "column": 1,
            "severity": "error", "rule": "r", "message": "m",
        }

    def test_report_accessors(self):
        r = PyrightReport(
            diagnostics=[
                PyrightDiagnostic("/a", 1, 1, "error", "r1", "m"),
                PyrightDiagnostic("/a", 2, 1, "warning", "r2", "m"),
                PyrightDiagnostic("/a", 3, 1, "info", "r3", "m"),
            ],
        )
        assert len(r.errors) == 1
        assert len(r.warnings) == 1
        assert r.has_errors is True

    def test_to_dict_includes_counters(self):
        r = PyrightReport(
            diagnostics=[
                PyrightDiagnostic("/a", 1, 1, "error", "r", "m"),
                PyrightDiagnostic("/a", 2, 1, "error", "r", "m"),
                PyrightDiagnostic("/a", 3, 1, "warning", "r", "m"),
            ],
        )
        d = r.to_dict()
        assert d["error_count"] == 2
        assert d["warning_count"] == 1
        assert d["has_errors"] is True


# ── Master-switch round-trip ──────────────────────────────────────────


class TestMasterSwitch:
    def _import_rs(self):
        try:
            import app.runtime_settings as rs
            return rs
        except Exception as exc:
            pytest.skip(f"app.runtime_settings unavailable: {exc}")

    def test_default_is_off(self, monkeypatch, tmp_path):
        rs = self._import_rs()
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(rs, "_cache", None)
        monkeypatch.setattr(rs, "_STATE_PATH", tmp_path / "runtime_settings.json")
        assert rs.get_pyright_sidecar_enabled() is False

    def test_setter_flips(self, monkeypatch, tmp_path):
        rs = self._import_rs()
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(rs, "_cache", None)
        monkeypatch.setattr(rs, "_STATE_PATH", tmp_path / "runtime_settings.json")
        rs.set_pyright_sidecar_enabled(True)
        assert rs.get_pyright_sidecar_enabled() is True
        rs.set_pyright_sidecar_enabled(False)
        assert rs.get_pyright_sidecar_enabled() is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
