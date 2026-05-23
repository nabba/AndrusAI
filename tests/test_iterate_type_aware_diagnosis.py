"""Tests for the iterate type-aware diagnosis path (2026-05-22).

When `run_type_check=True` AND a `type_checker` is supplied, the
iterate loop now invokes the checker BEFORE each diagnosis call and
passes the error-severity rows to diagnosis_fn as `type_errors_hint`.
The LLM addresses both test failure AND type errors in one pass.

Covers:
  * type_errors_hint passed when type_checker returns errors
  * type_errors_hint is None when type_checker returns []
  * type_errors_hint is None when run_type_check=False (even with checker)
  * type_errors_hint is None when no type_checker supplied
  * Checker exception → hint=None, diagnosis still called
  * Only error-severity rows pass to diagnosis (warnings filtered)
  * Diagnosis_fn that doesn't accept type_errors_hint still works
    (backward-compat via _invoke_diagnosis_fn)
  * _format_type_errors_hint formatting verified
"""
from __future__ import annotations

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


from app.coding_session.iterate import (  # noqa: E402
    IterateConfig,
    _invoke_diagnosis_fn,
    iterate_until_green,
)


# ── Test helpers ──────────────────────────────────────────────────────


class _RedResult:
    ok = False
    exit_code = 1
    stderr = "AssertionError: nope"
    stdout = ""

    def to_dict(self):
        return {"ok": False, "exit_code": 1}


def _red_runner():
    return _RedResult()


def _decline_fix(**_kw):
    """Returns a declined fix so the loop terminates after one
    iteration — keeps tests fast and focused on the hint plumbing."""
    class _F:
        declined = True
        is_actionable = False
        decline_reason = "decline-for-test"
        path = ""
        new_content = ""
        confidence = 0.0
        reasoning = ""
    return _F()


# ── Hint passed when present ─────────────────────────────────────────


class TestHintPassed:
    def test_errors_passed_to_diagnosis(self):
        captured = {}

        def _capture(**kwargs):
            captured.update(kwargs)
            return _decline_fix()

        type_errors = [
            {"severity": "error", "file": "x.py", "line": 1,
             "column": 1, "rule": "r", "message": "bad"},
            {"severity": "error", "file": "x.py", "line": 5,
             "column": 1, "rule": "r2", "message": "broken"},
        ]
        iterate_until_green(
            target_file="x.py",
            test_runner=_red_runner,
            file_reader=lambda p: "src",
            file_writer=lambda p, c: None,
            config=IterateConfig(run_type_check=True),
            type_checker=lambda p: type_errors,
            diagnosis_fn=_capture,
        )
        assert captured.get("type_errors_hint") is not None
        assert len(captured["type_errors_hint"]) == 2
        assert captured["type_errors_hint"][0]["message"] == "bad"

    def test_warnings_filtered_out(self):
        captured = {}

        def _capture(**kwargs):
            captured.update(kwargs)
            return _decline_fix()

        diags = [
            {"severity": "error", "file": "x.py", "line": 1,
             "column": 1, "rule": "r", "message": "real error"},
            {"severity": "warning", "file": "x.py", "line": 2,
             "column": 1, "rule": "r", "message": "unused"},
            {"severity": "info", "file": "x.py", "line": 3,
             "column": 1, "rule": "r", "message": "hint"},
        ]
        iterate_until_green(
            target_file="x.py",
            test_runner=_red_runner,
            file_reader=lambda p: "src",
            file_writer=lambda p, c: None,
            config=IterateConfig(run_type_check=True),
            type_checker=lambda p: diags,
            diagnosis_fn=_capture,
        )
        # Only the error-severity row in the hint
        assert len(captured["type_errors_hint"]) == 1
        assert captured["type_errors_hint"][0]["severity"] == "error"


# ── Hint absent in dormant cases ─────────────────────────────────────


class TestHintAbsent:
    def test_no_checker_no_hint(self):
        captured = {}

        def _capture(**kwargs):
            captured.update(kwargs)
            return _decline_fix()

        iterate_until_green(
            target_file="x.py",
            test_runner=_red_runner,
            file_reader=lambda p: "src",
            file_writer=lambda p, c: None,
            config=IterateConfig(run_type_check=True),
            diagnosis_fn=_capture,
        )
        assert captured.get("type_errors_hint") is None

    def test_run_type_check_false_no_hint(self):
        captured = {}

        def _capture(**kwargs):
            captured.update(kwargs)
            return _decline_fix()

        # Checker provided but config flag off → checker NOT consulted
        iterate_until_green(
            target_file="x.py",
            test_runner=_red_runner,
            file_reader=lambda p: "src",
            file_writer=lambda p, c: None,
            diagnosis_fn=_capture,
            type_checker=lambda p: [
                {"severity": "error", "file": "x", "line": 1,
                 "column": 1, "rule": "r", "message": "m"},
            ],
        )
        assert captured.get("type_errors_hint") is None

    def test_empty_diagnostics_normalized_to_none(self):
        captured = {}

        def _capture(**kwargs):
            captured.update(kwargs)
            return _decline_fix()

        iterate_until_green(
            target_file="x.py",
            test_runner=_red_runner,
            file_reader=lambda p: "src",
            file_writer=lambda p, c: None,
            config=IterateConfig(run_type_check=True),
            type_checker=lambda p: [],
            diagnosis_fn=_capture,
        )
        # Empty list → hint normalized to None so diagnosis_fn knows
        # "no type info" vs "type-check ran and found nothing"
        assert captured.get("type_errors_hint") is None


# ── Failure isolation ────────────────────────────────────────────────


class TestFailureIsolation:
    def test_checker_exception_does_not_block_diagnosis(self):
        captured = {}

        def _capture(**kwargs):
            captured.update(kwargs)
            return _decline_fix()

        def _boom(p):
            raise RuntimeError("pyright sick")

        iterate_until_green(
            target_file="x.py",
            test_runner=_red_runner,
            file_reader=lambda p: "src",
            file_writer=lambda p, c: None,
            config=IterateConfig(run_type_check=True),
            type_checker=_boom,
            diagnosis_fn=_capture,
        )
        # Diagnosis still called with hint=None
        assert "type_errors_hint" in captured
        assert captured["type_errors_hint"] is None


# ── _invoke_diagnosis_fn backward-compat ─────────────────────────────


class TestInvokeBackwardCompat:
    def test_fn_accepting_hint_gets_it(self):
        received = {}

        def fn(*, error_message, type_errors_hint=None, **kw):
            received["msg"] = error_message
            received["hint"] = type_errors_hint
            return "ok"

        result = _invoke_diagnosis_fn(
            fn,
            error_message="boom",
            type_errors_hint=[{"severity": "error"}],
        )
        assert result == "ok"
        assert received["hint"] == [{"severity": "error"}]

    def test_fn_rejecting_hint_retried_without_it(self):
        """A pre-Phase-3-v2 diagnosis_fn signature doesn't have
        `type_errors_hint`. The wrapper should retry without the kwarg
        so existing callers keep working."""
        received = {}

        def old_fn(*, error_message):  # no kwargs catch-all
            received["msg"] = error_message
            return "ok"

        result = _invoke_diagnosis_fn(
            old_fn,
            error_message="boom",
            type_errors_hint=[{"severity": "error"}],
        )
        assert result == "ok"
        assert "msg" in received
        # The hint was dropped by the fallback retry, NOT passed in
        assert "hint" not in received

    def test_unrelated_TypeError_propagates(self):
        """If diagnosis_fn raises TypeError from inside its body, the
        wrapper should NOT swallow it. Phase B.1 cleanup (2026-05-22):
        the wrapper now uses inspect.signature so it never CATCHES a
        TypeError at all — body-raised errors propagate untouched."""

        def fn(*, error_message):
            raise TypeError("unrelated_kwarg unexpected")

        with pytest.raises(TypeError, match="unrelated_kwarg"):
            _invoke_diagnosis_fn(
                fn, error_message="boom", type_errors_hint=None,
            )

    def test_kwargs_catchall_passes_everything(self):
        """When the fn accepts **kwargs, ALL provided kwargs reach it.
        Phase B.1 cleanup: explicit signature inspection makes this
        deterministic — no string matching, no exception-control-flow."""
        received = {}

        def fn(**kwargs):
            received.update(kwargs)
            return "ok"

        result = _invoke_diagnosis_fn(
            fn,
            error_message="x",
            error_traceback="tb",
            file_path="x.py",
            file_content="src",
            pattern_signature="sig",
            error_class="cls",
            type_errors_hint=[{"severity": "error"}],
        )
        assert result == "ok"
        assert received["type_errors_hint"] == [{"severity": "error"}]
        # All other kwargs reached the catchall
        assert received["error_message"] == "x"
        assert received["pattern_signature"] == "sig"

    def test_signature_introspection_drops_unaccepted_kwargs(self):
        """The contract: kwargs the callee doesn't declare are
        SILENTLY DROPPED before the call (not raised, not re-caught).
        Pins the "no string matching" cleanup property."""
        received = {}

        def picky_fn(*, error_message, file_path):
            received["msg"] = error_message
            received["file"] = file_path
            return "ok"

        # type_errors_hint NOT declared by picky_fn — should be dropped
        # by the introspector, NOT cause an exception.
        result = _invoke_diagnosis_fn(
            picky_fn,
            error_message="boom",
            file_path="x.py",
            type_errors_hint=[{"severity": "error"}],
            extra_kwarg="also dropped",
        )
        assert result == "ok"
        # Both unaccepted kwargs silently filtered
        assert "type_errors_hint" not in received
        assert "extra_kwarg" not in received


# ── _format_type_errors_hint ─────────────────────────────────────────


def _import_format_hint():
    """Import _format_type_errors_hint, skipping the test on hosts
    that lack the heavy structured_diagnosis import path (pydantic_settings
    isn't installed in the test env). On the gateway this import works
    fine."""
    try:
        from app.healing.structured_diagnosis import _format_type_errors_hint
        return _format_type_errors_hint
    except Exception as exc:
        pytest.skip(f"structured_diagnosis import unavailable: {exc}")


class TestFormatHint:
    def test_empty_or_none_returns_empty_string(self):
        f = _import_format_hint()
        assert f(None) == ""
        assert f([]) == ""

    def test_formatted_block_includes_each_error(self):
        f = _import_format_hint()
        block = f([
            {"severity": "error", "file": "x.py", "line": 5,
             "column": 1, "rule": "reportGeneralTypeIssues",
             "message": "Cannot assign int to str"},
        ])
        assert "x.py:5:1" in block
        assert "reportGeneralTypeIssues" in block
        assert "Cannot assign int to str" in block
        assert "Address these in the same fix attempt" in block

    def test_capped_at_10_with_footer(self):
        f = _import_format_hint()
        diags = [
            {"severity": "error", "file": "x.py", "line": i + 1,
             "column": 1, "rule": "r", "message": f"err{i}"}
            for i in range(15)
        ]
        block = f(diags)
        assert "and 5 more" in block

    def test_skips_non_dict_entries(self):
        f = _import_format_hint()
        block = f([
            "not a dict",
            {"severity": "error", "file": "x", "line": 1,
             "column": 1, "rule": "r", "message": "good"},
            42,
        ])
        assert "good" in block


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
