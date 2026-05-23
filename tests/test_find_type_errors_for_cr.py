"""Tests for find_type_errors_for_cr cross-lookup helper (2026-05-22).

Pins the helper that bridges the CR id (visible in /cp/changes) back
to the SubmitResult.type_errors recorded by submit_session when
with_type_check=True was set.

Covers:
  * Empty cr_id → None
  * No matching session → None
  * Session without submit_results → skipped, no crash
  * Session with submit_results matching cr_id → payload returned
  * Multiple sessions same cr_id → newest submitted_at wins
  * Match returned even when type_errors is empty (clean check)
  * Failure-isolated: store.list_all raises → None (no crash)
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
from app.coding_session.submit import find_type_errors_for_cr  # noqa: E402


def _make_session(*, sid: str, submitted_at: str, results: list[SubmitResult]):
    """Lightweight session-shape stub matching the duck-typed lookups
    used in find_type_errors_for_cr."""
    class _Session:
        pass
    s = _Session()
    s.id = sid
    s.submitted_at = submitted_at
    s.submit_results = results
    return s


# ── Empty / missing cases ────────────────────────────────────────────


class TestEmptyCases:
    def test_empty_cr_id_returns_none(self):
        assert find_type_errors_for_cr("") is None
        assert find_type_errors_for_cr(None) is None  # type: ignore

    def test_no_sessions_returns_none(self):
        with patch(
            "app.coding_session.store.list_all", return_value=[],
        ):
            assert find_type_errors_for_cr("cr-1") is None

    def test_no_matching_session(self):
        sessions = [
            _make_session(
                sid="s1", submitted_at="2026-05-22T10:00:00+00:00",
                results=[SubmitResult(
                    path="x.py", change_request_id="cr-OTHER",
                    status="pending",
                )],
            ),
        ]
        with patch(
            "app.coding_session.store.list_all", return_value=sessions,
        ):
            assert find_type_errors_for_cr("cr-1") is None


# ── Hit cases ────────────────────────────────────────────────────────


class TestHitCases:
    def test_simple_hit(self):
        sessions = [
            _make_session(
                sid="s1", submitted_at="2026-05-22T10:00:00+00:00",
                results=[SubmitResult(
                    path="app/x.py", change_request_id="cr-1",
                    status="pending",
                    type_errors=[
                        {"severity": "error", "file": "x", "line": 1,
                         "column": 1, "rule": "r", "message": "m"},
                    ],
                )],
            ),
        ]
        with patch(
            "app.coding_session.store.list_all", return_value=sessions,
        ):
            payload = find_type_errors_for_cr("cr-1")
        assert payload is not None
        assert payload["session_id"] == "s1"
        assert payload["path"] == "app/x.py"
        assert payload["submitted_at"] == "2026-05-22T10:00:00+00:00"
        assert len(payload["type_errors"]) == 1

    def test_clean_check_still_returns_payload(self):
        """When a session opted into with_type_check and the file
        was clean, type_errors is []. The payload should STILL be
        returned so operators see "check ran clean" rather than
        "no check ran" (which is a 404)."""
        sessions = [
            _make_session(
                sid="s1", submitted_at="2026-05-22T10:00:00+00:00",
                results=[SubmitResult(
                    path="app/x.py", change_request_id="cr-1",
                    status="pending",
                    type_errors=[],
                )],
            ),
        ]
        with patch(
            "app.coding_session.store.list_all", return_value=sessions,
        ):
            payload = find_type_errors_for_cr("cr-1")
        assert payload is not None
        assert payload["type_errors"] == []

    def test_newest_session_wins_when_multiple_match(self):
        sessions = [
            _make_session(
                sid="s_old", submitted_at="2026-05-20T10:00:00+00:00",
                results=[SubmitResult(
                    path="x.py", change_request_id="cr-1",
                    status="pending",
                    type_errors=[
                        {"severity": "error", "file": "x", "line": 1,
                         "column": 1, "rule": "r", "message": "old err"},
                    ],
                )],
            ),
            _make_session(
                sid="s_new", submitted_at="2026-05-22T10:00:00+00:00",
                results=[SubmitResult(
                    path="x.py", change_request_id="cr-1",
                    status="pending",
                    type_errors=[
                        {"severity": "error", "file": "x", "line": 2,
                         "column": 1, "rule": "r", "message": "new err"},
                    ],
                )],
            ),
        ]
        with patch(
            "app.coding_session.store.list_all", return_value=sessions,
        ):
            payload = find_type_errors_for_cr("cr-1")
        assert payload["session_id"] == "s_new"
        assert payload["type_errors"][0]["message"] == "new err"


# ── Skip / robustness cases ──────────────────────────────────────────


class TestRobustness:
    def test_session_without_submit_results_skipped(self):
        sessions = [
            _make_session(
                sid="s_empty", submitted_at="2026-05-22T10:00:00+00:00",
                results=[],
            ),
            _make_session(
                sid="s_hit", submitted_at="2026-05-22T09:00:00+00:00",
                results=[SubmitResult(
                    path="x.py", change_request_id="cr-1",
                    status="pending",
                )],
            ),
        ]
        with patch(
            "app.coding_session.store.list_all", return_value=sessions,
        ):
            payload = find_type_errors_for_cr("cr-1")
        assert payload is not None
        assert payload["session_id"] == "s_hit"

    def test_list_all_raises_returns_none(self):
        with patch(
            "app.coding_session.store.list_all",
            side_effect=RuntimeError("store sick"),
        ):
            assert find_type_errors_for_cr("cr-1") is None

    def test_session_missing_submitted_at_still_processes(self):
        """No submitted_at attr → sort key defaults to ''; still
        finds the match."""
        sessions = [
            _make_session(
                sid="s1", submitted_at="",
                results=[SubmitResult(
                    path="x.py", change_request_id="cr-1",
                    status="pending",
                )],
            ),
        ]
        with patch(
            "app.coding_session.store.list_all", return_value=sessions,
        ):
            payload = find_type_errors_for_cr("cr-1")
        assert payload is not None
        assert payload["session_id"] == "s1"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
