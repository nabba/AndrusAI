"""Tests for the submit_session ↔ pyright sidecar wiring (Phase 3 v2
follow-up, 2026-05-22).

The wire-in is observational by design — ``type_errors`` is attached
post-fanout but the submit itself never fails on the basis of type
errors. Tests pin that contract end-to-end.

Covers:
  * Default (with_type_check=False) — type_errors stays empty
  * with_type_check=True + .py file → error-severity diagnostics attached
  * Non-.py files skipped even when with_type_check=True
  * Refused CRs (no change_request_id) skipped (no type-check spam)
  * Sidecar exception per-file → empty type_errors, other files unaffected
  * SubmitResult.to_dict / from_dict round-trip preserves type_errors
  * SubmitResult.to_dict omits type_errors when empty (compact wire)
  * Old persisted dict without type_errors → from_dict defaults to []
  * Helper _attach_type_errors_to_results is no-op on missing worktree_path
"""
from __future__ import annotations

import sys
import types
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


from app.coding_session.models import SubmitResult  # noqa: E402
from app.coding_session.submit import (  # noqa: E402
    _attach_type_errors_to_results,
)
from app.code_intel.pyright_sidecar import (  # noqa: E402
    PyrightDiagnostic,
    PyrightReport,
)


# ── SubmitResult model ───────────────────────────────────────────────


class TestSubmitResultModel:
    def test_default_type_errors_empty(self):
        r = SubmitResult(
            path="app/x.py",
            change_request_id="cr-1",
            status="pending",
        )
        assert r.type_errors == []

    def test_to_dict_omits_empty_type_errors(self):
        r = SubmitResult(
            path="app/x.py", change_request_id="cr-1", status="pending",
        )
        d = r.to_dict()
        assert "type_errors" not in d

    def test_to_dict_includes_populated_type_errors(self):
        r = SubmitResult(
            path="app/x.py", change_request_id="cr-1", status="pending",
            type_errors=[{"severity": "error", "file": "x", "line": 1,
                          "column": 1, "rule": "r", "message": "m"}],
        )
        d = r.to_dict()
        assert "type_errors" in d
        assert len(d["type_errors"]) == 1

    def test_from_dict_round_trip(self):
        original = SubmitResult(
            path="app/x.py", change_request_id="cr-1", status="pending",
            type_errors=[
                {"severity": "error", "file": "x", "line": 1,
                 "column": 1, "rule": "r", "message": "m"},
                {"severity": "error", "file": "x", "line": 2,
                 "column": 1, "rule": "r2", "message": "m2"},
            ],
        )
        reconstructed = SubmitResult.from_dict(original.to_dict())
        assert reconstructed.type_errors == original.type_errors

    def test_from_dict_old_persisted_row_defaults_empty(self):
        # Pre-Phase-3-v2 row has no type_errors key
        old = {
            "path": "app/x.py",
            "change_request_id": "cr-1",
            "status": "applied",
        }
        r = SubmitResult.from_dict(old)
        assert r.type_errors == []


# ── _attach_type_errors_to_results helper ────────────────────────────


def _make_report(error_msgs: list[str]) -> PyrightReport:
    diags = [
        PyrightDiagnostic(
            file="x.py", line=i + 1, column=1,
            severity="error", rule="reportFoo", message=m,
        )
        for i, m in enumerate(error_msgs)
    ]
    return PyrightReport(diagnostics=diags, available=True)


class TestAttachHelper:
    def test_no_worktree_path_no_op(self, tmp_path):
        r = SubmitResult(
            path="app/x.py", change_request_id="cr-1", status="pending",
        )
        _attach_type_errors_to_results(
            results=[r], worktree_path=None,
        )
        assert r.type_errors == []

    def test_attaches_errors_to_py_file(self, tmp_path):
        r = SubmitResult(
            path="app/x.py", change_request_id="cr-1", status="pending",
        )
        with patch(
            "app.code_intel.pyright_sidecar.check_file",
            return_value=_make_report(["bad type"]),
        ):
            _attach_type_errors_to_results(
                results=[r], worktree_path=str(tmp_path),
            )
        assert len(r.type_errors) == 1
        assert r.type_errors[0]["message"] == "bad type"

    def test_skips_non_py_file(self, tmp_path):
        r = SubmitResult(
            path="docs/README.md",
            change_request_id="cr-1",
            status="pending",
        )
        with patch(
            "app.code_intel.pyright_sidecar.check_file",
            side_effect=AssertionError("should not be called"),
        ):
            _attach_type_errors_to_results(
                results=[r], worktree_path=str(tmp_path),
            )
        assert r.type_errors == []

    def test_skips_refusal_no_cr_id(self, tmp_path):
        r = SubmitResult(
            path="app/x.py",
            change_request_id=None,  # refusal
            status="tier_immutable_refused",
            refusal_reason="TIER_IMMUTABLE",
        )
        with patch(
            "app.code_intel.pyright_sidecar.check_file",
            side_effect=AssertionError("should not be called"),
        ):
            _attach_type_errors_to_results(
                results=[r], worktree_path=str(tmp_path),
            )
        assert r.type_errors == []

    def test_filters_to_error_severity_only(self, tmp_path):
        r = SubmitResult(
            path="app/x.py", change_request_id="cr-1", status="pending",
        )
        mixed_report = PyrightReport(
            diagnostics=[
                PyrightDiagnostic(
                    "x.py", 1, 1, "error", "r", "real error"),
                PyrightDiagnostic(
                    "x.py", 2, 1, "warning", "r", "unused"),
                PyrightDiagnostic(
                    "x.py", 3, 1, "info", "r", "hint"),
            ],
            available=True,
        )
        with patch(
            "app.code_intel.pyright_sidecar.check_file",
            return_value=mixed_report,
        ):
            _attach_type_errors_to_results(
                results=[r], worktree_path=str(tmp_path),
            )
        assert len(r.type_errors) == 1
        assert r.type_errors[0]["severity"] == "error"

    def test_per_file_exception_isolated(self, tmp_path):
        r1 = SubmitResult(
            path="app/a.py", change_request_id="cr-1", status="pending",
        )
        r2 = SubmitResult(
            path="app/b.py", change_request_id="cr-2", status="pending",
        )

        call_count = [0]

        def _side_effect(*a, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("sidecar boom on first file")
            return _make_report(["b is broken"])

        with patch(
            "app.code_intel.pyright_sidecar.check_file",
            side_effect=_side_effect,
        ):
            _attach_type_errors_to_results(
                results=[r1, r2], worktree_path=str(tmp_path),
            )
        # First file: exception → empty type_errors
        assert r1.type_errors == []
        # Second file: still got checked → one error attached
        assert len(r2.type_errors) == 1

    def test_empty_diagnostics_keeps_empty(self, tmp_path):
        r = SubmitResult(
            path="app/clean.py",
            change_request_id="cr-1", status="pending",
        )
        with patch(
            "app.code_intel.pyright_sidecar.check_file",
            return_value=PyrightReport(diagnostics=[], available=True),
        ):
            _attach_type_errors_to_results(
                results=[r], worktree_path=str(tmp_path),
            )
        assert r.type_errors == []

    def test_resolves_relative_paths_against_worktree(self, tmp_path):
        r = SubmitResult(
            path="sub/x.py", change_request_id="cr-1", status="pending",
        )
        captured = []
        from pathlib import Path

        def _capture(p):
            captured.append(Path(p))
            return PyrightReport(diagnostics=[], available=True)

        with patch(
            "app.code_intel.pyright_sidecar.check_file",
            side_effect=_capture,
        ):
            _attach_type_errors_to_results(
                results=[r], worktree_path=str(tmp_path),
            )
        assert captured == [tmp_path / "sub/x.py"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
