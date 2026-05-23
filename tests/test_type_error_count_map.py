"""Tests for build_type_error_count_map helper (2026-05-22).

Pins the helper that powers per-row type-error badges on the CR
list. One scan over the session store, returns a compact
``{cr_id: count}`` map.

Covers:
  * Empty store → empty map
  * Single session, single CR with errors → map has one entry
  * SubmitResult with empty type_errors → NOT in map
  * SubmitResult without change_request_id → skipped
  * Multiple sessions, same CR id → newest wins
  * Newer "clean" session overrides older "dirty" session for same CR
  * Session without submit_results attribute → skipped
  * Store import failure → empty map (no crash)
  * Store.list_all raises → empty map
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
from app.coding_session.submit import build_type_error_count_map  # noqa: E402


def _session(*, sid, submitted_at, results):
    class _S:
        pass
    s = _S()
    s.id = sid
    s.submitted_at = submitted_at
    s.submit_results = results
    return s


def _err(msg="err"):
    return {"severity": "error", "file": "x.py", "line": 1,
            "column": 1, "rule": "r", "message": msg}


# ── Empty / no-data cases ────────────────────────────────────────────


class TestEmpty:
    def test_empty_store(self):
        with patch(
            "app.coding_session.store.list_all", return_value=[],
        ):
            assert build_type_error_count_map() == {}

    def test_session_without_submit_results(self):
        s = _session(sid="s1", submitted_at="2026-05-22T00:00:00+00:00",
                     results=[])
        with patch(
            "app.coding_session.store.list_all", return_value=[s],
        ):
            assert build_type_error_count_map() == {}


# ── Population cases ─────────────────────────────────────────────────


class TestPopulation:
    def test_single_cr_with_errors(self):
        s = _session(
            sid="s1", submitted_at="2026-05-22T00:00:00+00:00",
            results=[SubmitResult(
                path="x.py", change_request_id="cr-1",
                status="pending",
                type_errors=[_err("e1"), _err("e2")],
            )],
        )
        with patch(
            "app.coding_session.store.list_all", return_value=[s],
        ):
            m = build_type_error_count_map()
        assert m == {"cr-1": 2}

    def test_empty_type_errors_not_in_map(self):
        s = _session(
            sid="s1", submitted_at="2026-05-22T00:00:00+00:00",
            results=[SubmitResult(
                path="x.py", change_request_id="cr-1",
                status="pending", type_errors=[],
            )],
        )
        with patch(
            "app.coding_session.store.list_all", return_value=[s],
        ):
            assert build_type_error_count_map() == {}

    def test_refusal_skipped(self):
        s = _session(
            sid="s1", submitted_at="2026-05-22T00:00:00+00:00",
            results=[SubmitResult(
                path="x.py", change_request_id=None,  # refused
                status="tier_immutable_refused", type_errors=[],
            )],
        )
        with patch(
            "app.coding_session.store.list_all", return_value=[s],
        ):
            assert build_type_error_count_map() == {}

    def test_multi_session_same_cr_newest_wins(self):
        s_old = _session(
            sid="s_old", submitted_at="2026-05-20T00:00:00+00:00",
            results=[SubmitResult(
                path="x.py", change_request_id="cr-1",
                status="pending", type_errors=[_err(), _err(), _err()],
            )],
        )
        s_new = _session(
            sid="s_new", submitted_at="2026-05-22T00:00:00+00:00",
            results=[SubmitResult(
                path="x.py", change_request_id="cr-1",
                status="pending", type_errors=[_err()],
            )],
        )
        with patch(
            "app.coding_session.store.list_all",
            return_value=[s_old, s_new],
        ):
            m = build_type_error_count_map()
        # Newer wins: count = 1 (not 3)
        assert m == {"cr-1": 1}

    def test_newer_clean_overrides_older_dirty(self):
        """Same CR id resubmitted after a fix — operator wants to see
        the LATEST state (clean), not the historical (dirty)."""
        s_old = _session(
            sid="s_old", submitted_at="2026-05-20T00:00:00+00:00",
            results=[SubmitResult(
                path="x.py", change_request_id="cr-1",
                status="pending", type_errors=[_err()],
            )],
        )
        s_new = _session(
            sid="s_new", submitted_at="2026-05-22T00:00:00+00:00",
            results=[SubmitResult(
                path="x.py", change_request_id="cr-1",
                status="pending", type_errors=[],  # clean now
            )],
        )
        with patch(
            "app.coding_session.store.list_all",
            return_value=[s_old, s_new],
        ):
            m = build_type_error_count_map()
        # Newer clean overrides older dirty → no entry
        assert m == {}

    def test_multiple_crs(self):
        s = _session(
            sid="s1", submitted_at="2026-05-22T00:00:00+00:00",
            results=[
                SubmitResult(path="a.py", change_request_id="cr-A",
                             status="pending",
                             type_errors=[_err(), _err()]),
                SubmitResult(path="b.py", change_request_id="cr-B",
                             status="pending",
                             type_errors=[_err()]),
                SubmitResult(path="c.py", change_request_id="cr-C",
                             status="pending",
                             type_errors=[]),  # clean — not in map
            ],
        )
        with patch(
            "app.coding_session.store.list_all", return_value=[s],
        ):
            m = build_type_error_count_map()
        assert m == {"cr-A": 2, "cr-B": 1}
        assert "cr-C" not in m


# ── Failure isolation ────────────────────────────────────────────────


class TestFailureIsolation:
    def test_list_all_raises_returns_empty(self):
        with patch(
            "app.coding_session.store.list_all",
            side_effect=RuntimeError("store sick"),
        ):
            assert build_type_error_count_map() == {}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
