"""Tests for the iterate_until_green ↔ pyright sidecar wiring
(Phase 3 v2 follow-up, 2026-05-22).

The wire-in is observational by design: ``IterateOutcome.type_errors``
is populated post-loop, but no termination status changes regardless
of whether type errors are present. Tests pin that contract.

Covers:
  * Default behavior (no config flag, no checker) — type_errors stays empty
  * run_type_check=True but no checker callback → still empty (silent no-op)
  * run_type_check=False with a checker → still empty (config wins)
  * Happy path: checker returns mixed severities; only "error" rows attached
  * Checker exception → outcome still returned; type_errors empty; status unchanged
  * Checker returns None → handled as empty list
  * IterateOutcome.as_jsonable() includes type_errors
  * make_pyright_type_checker happy path (mocks the sidecar)
  * make_pyright_type_checker failure-isolated (sidecar raises → [])
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


from app.coding_session.iterate import (  # noqa: E402
    IterateConfig,
    iterate_until_green,
    make_pyright_type_checker,
)


# ── Test helpers ──────────────────────────────────────────────────────


class _GreenResult:
    """Minimal RunResult-shape for the test_runner."""

    ok = True
    exit_code = 0
    stderr = ""
    stdout = ""

    def to_dict(self):
        return {"ok": True, "exit_code": 0}


class _RedResult:
    ok = False
    exit_code = 1
    stderr = "AssertionError: nope"
    stdout = ""

    def to_dict(self):
        return {"ok": False, "exit_code": 1}


def _green_runner():
    return _GreenResult()


def _red_runner():
    return _RedResult()


# Stub diagnosis_fn used by green-path tests. The real lazy import
# fails on dev hosts that lack pydantic_settings, which would short-
# circuit the loop to "no_fix_available" before the green test ever
# runs. Passing a no-op stub makes the test deterministic across
# environments.
def _noop_diagnosis(**_kw):
    return None


# ── Default OFF behavior ──────────────────────────────────────────────


class TestDefaultOff:
    def test_no_type_checker_no_field(self):
        out = iterate_until_green(
            target_file="x.py",
            test_runner=_green_runner,
            file_reader=lambda p: "src",
            file_writer=lambda p, c: None,
            diagnosis_fn=_noop_diagnosis,
        )
        assert out.status == "passed"
        assert out.type_errors == []

    def test_config_off_with_checker_silent(self):
        # checker provided but run_type_check=False (default) → no call
        calls = []
        def _checker(p):
            calls.append(p)
            return [{"severity": "error", "file": "x", "line": 1,
                     "column": 1, "rule": "r", "message": "m"}]
        out = iterate_until_green(
            target_file="x.py",
            test_runner=_green_runner,
            file_reader=lambda p: "src",
            file_writer=lambda p, c: None,
            diagnosis_fn=_noop_diagnosis,
            type_checker=_checker,
        )
        assert calls == []  # checker never invoked
        assert out.type_errors == []

    def test_config_on_no_checker_silent(self):
        # config wants check but no callback → silent no-op (no crash)
        out = iterate_until_green(
            target_file="x.py",
            test_runner=_green_runner,
            file_reader=lambda p: "src",
            file_writer=lambda p, c: None,
            diagnosis_fn=_noop_diagnosis,
            config=IterateConfig(run_type_check=True),
        )
        assert out.status == "passed"
        assert out.type_errors == []


# ── ON happy path ─────────────────────────────────────────────────────


class TestOnHappyPath:
    def test_errors_only_attached(self):
        diags = [
            {"severity": "error", "file": "x.py", "line": 1,
             "column": 1, "rule": "r1", "message": "type error"},
            {"severity": "warning", "file": "x.py", "line": 2,
             "column": 1, "rule": "r2", "message": "unused import"},
            {"severity": "info", "file": "x.py", "line": 3,
             "column": 1, "rule": "r3", "message": "hint"},
            {"severity": "error", "file": "x.py", "line": 4,
             "column": 1, "rule": "r4", "message": "second error"},
        ]
        out = iterate_until_green(
            target_file="x.py",
            test_runner=_green_runner,
            file_reader=lambda p: "src",
            file_writer=lambda p, c: None,
            diagnosis_fn=_noop_diagnosis,
            config=IterateConfig(run_type_check=True),
            type_checker=lambda p: diags,
        )
        assert out.status == "passed"
        # Only the two "error"-severity rows kept
        assert len(out.type_errors) == 2
        for row in out.type_errors:
            assert row["severity"] == "error"

    def test_attached_on_max_iterations_path_too(self):
        # Loop never goes green; checker still runs at the end.
        diags = [
            {"severity": "error", "file": "x.py", "line": 1,
             "column": 1, "rule": "r1", "message": "still broken"},
        ]
        out = iterate_until_green(
            target_file="x.py",
            test_runner=_red_runner,
            file_reader=lambda p: "src",
            file_writer=lambda p, c: None,
            config=IterateConfig(
                max_iterations=2, budget_usd=0.0001,
                run_type_check=True,
            ),
            diagnosis_fn=lambda **kw: None,  # nothing actionable
            type_checker=lambda p: diags,
        )
        # Status is one of the non-passed terminals — but type errors still attached
        assert out.status in {
            "no_fix_available", "max_iterations", "budget_exhausted",
        }
        assert len(out.type_errors) == 1


# ── Failure isolation ────────────────────────────────────────────────


class TestFailureIsolation:
    def test_checker_raises_outcome_still_returned(self):
        def _boom(p):
            raise RuntimeError("pyright is sick")
        out = iterate_until_green(
            target_file="x.py",
            test_runner=_green_runner,
            file_reader=lambda p: "src",
            file_writer=lambda p, c: None,
            diagnosis_fn=_noop_diagnosis,
            config=IterateConfig(run_type_check=True),
            type_checker=_boom,
        )
        # No re-raise; outcome status preserved
        assert out.status == "passed"
        assert out.type_errors == []

    def test_checker_returns_none_handled(self):
        out = iterate_until_green(
            target_file="x.py",
            test_runner=_green_runner,
            file_reader=lambda p: "src",
            file_writer=lambda p, c: None,
            diagnosis_fn=_noop_diagnosis,
            config=IterateConfig(run_type_check=True),
            type_checker=lambda p: None,
        )
        assert out.type_errors == []

    def test_non_dict_diagnostic_skipped(self):
        # Defensive against shape drift in the checker
        out = iterate_until_green(
            target_file="x.py",
            test_runner=_green_runner,
            file_reader=lambda p: "src",
            file_writer=lambda p, c: None,
            diagnosis_fn=_noop_diagnosis,
            config=IterateConfig(run_type_check=True),
            type_checker=lambda p: [
                "not a dict",
                {"severity": "error", "file": "x", "line": 1,
                 "column": 1, "rule": "r", "message": "m"},
                42,
            ],
        )
        assert len(out.type_errors) == 1


# ── as_jsonable serialization ────────────────────────────────────────


class TestJsonable:
    def test_includes_type_errors_field(self):
        diags = [
            {"severity": "error", "file": "x.py", "line": 1,
             "column": 1, "rule": "r", "message": "m"},
        ]
        out = iterate_until_green(
            target_file="x.py",
            test_runner=_green_runner,
            file_reader=lambda p: "src",
            file_writer=lambda p, c: None,
            diagnosis_fn=_noop_diagnosis,
            config=IterateConfig(run_type_check=True),
            type_checker=lambda p: diags,
        )
        j = out.as_jsonable()
        assert "type_errors" in j
        assert len(j["type_errors"]) == 1

    def test_default_serialization_includes_empty_type_errors(self):
        out = iterate_until_green(
            target_file="x.py",
            test_runner=_green_runner,
            file_reader=lambda p: "src",
            file_writer=lambda p, c: None,
            diagnosis_fn=_noop_diagnosis,
        )
        j = out.as_jsonable()
        # Field is always present for forward-compat
        assert j["type_errors"] == []


# ── make_pyright_type_checker convenience builder ─────────────────────


class TestPyrightTypeCheckerBuilder:
    def test_happy_path(self, tmp_path):
        # Mock the sidecar's check_file to return a fake report
        fake_diag = MagicMock()
        fake_diag.to_dict.return_value = {
            "severity": "error", "file": str(tmp_path / "x.py"),
            "line": 1, "column": 1, "rule": "r", "message": "m",
        }
        fake_report = MagicMock()
        fake_report.diagnostics = [fake_diag]

        with patch(
            "app.code_intel.pyright_sidecar.check_file",
            return_value=fake_report,
        ):
            checker = make_pyright_type_checker(tmp_path)
            result = checker("x.py")

        assert len(result) == 1
        assert result[0]["severity"] == "error"

    def test_sidecar_raises_returns_empty(self, tmp_path):
        with patch(
            "app.code_intel.pyright_sidecar.check_file",
            side_effect=RuntimeError("boom"),
        ):
            checker = make_pyright_type_checker(tmp_path)
            assert checker("x.py") == []

    def test_passes_resolved_path_to_sidecar(self, tmp_path):
        captured = []

        def _capture(p):
            captured.append(Path(p))
            r = MagicMock()
            r.diagnostics = []
            return r

        with patch(
            "app.code_intel.pyright_sidecar.check_file", side_effect=_capture,
        ):
            checker = make_pyright_type_checker(tmp_path)
            checker("sub/x.py")

        assert len(captured) == 1
        assert captured[0] == tmp_path / "sub/x.py"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
