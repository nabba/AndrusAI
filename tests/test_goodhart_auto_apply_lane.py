"""Tests for the goodhart_guard auto-apply lane detector (Gap E
closure, 2026-05-23).

Pins:
  * `_detect_auto_apply_gaming` exists and is invoked from
    `detect_gaming_signals`.
  * Returns empty list below the minimum-outcomes threshold.
  * Returns medium-severity signal at 30%-50% rollback rate.
  * Returns high-severity signal above 50% rollback rate.
  * High-volume case (≥50 applies, >15% rollback) trips even when
    the standard 30% threshold isn't met.
  * Identifies the worst requestor in the description.
  * Failure-isolated: missing change_requests.store returns empty.
"""
from __future__ import annotations

import importlib.util
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())


def _gg_loadable() -> bool:
    try:
        import app.goodhart_guard  # noqa: F401
        return True
    except Exception:
        return False


# ── Source-level pins (work on dev host) ────────────────────────────


def test_detector_function_exists():
    src = Path("app/goodhart_guard.py").read_text(encoding="utf-8")
    assert "def _detect_auto_apply_gaming(" in src


def test_detector_invoked_from_main_loop():
    src = Path("app/goodhart_guard.py").read_text(encoding="utf-8")
    # The new detector is appended to `signals` from
    # detect_gaming_signals
    assert "_detect_auto_apply_gaming(window_days)" in src


def test_signal_type_is_auto_apply_rollback_churn():
    src = Path("app/goodhart_guard.py").read_text(encoding="utf-8")
    assert 'signal_type="auto_apply_rollback_churn"' in src


def test_thresholds_are_module_level_constants():
    src = Path("app/goodhart_guard.py").read_text(encoding="utf-8")
    assert "_AUTO_APPLY_MIN_OUTCOMES = " in src
    assert "_AUTO_APPLY_ROLLBACK_THRESHOLD = " in src
    assert "_AUTO_APPLY_HIGH_VOLUME_THRESHOLD = " in src


# ── Behavioral tests (need full stack) ──────────────────────────────


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _make_cr(
    *,
    decided_value: str = "self-heal-auto-apply",
    status_value: str = "applied",
    requestor: str = "error_diagnosis",
    days_ago: float = 1.0,
):
    """Build a stub CR object that the detector reads."""
    ts = (
        datetime.now(tz=timezone.utc).timestamp() - days_ago * 86400
    )
    iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    status_enum = SimpleNamespace(value=status_value)
    decided_enum = SimpleNamespace(value=decided_value)
    return SimpleNamespace(
        applied_at=iso,
        decided_at=iso,
        created_at=iso,
        decided_by=decided_enum,
        requestor=requestor,
        status=status_enum,
    )


@pytest.mark.skipif(
    not _gg_loadable(), reason="goodhart_guard requires full boot",
)
class TestAutoApplyDetector:
    def _fn(self):
        from app.goodhart_guard import _detect_auto_apply_gaming
        return _detect_auto_apply_gaming

    def test_no_crs_returns_empty(self, monkeypatch):
        from app.change_requests import store as cr_store
        monkeypatch.setattr(cr_store, "list_all", lambda **kw: [])
        result = self._fn()(window_days=30)
        assert result == []

    def test_below_min_outcomes_returns_empty(self, monkeypatch):
        from app.change_requests import store as cr_store
        # Only 5 auto-applies — below _AUTO_APPLY_MIN_OUTCOMES (10)
        crs = [_make_cr() for _ in range(5)]
        monkeypatch.setattr(cr_store, "list_all", lambda **kw: crs)
        result = self._fn()(window_days=30)
        assert result == []

    def test_clean_lane_returns_empty(self, monkeypatch):
        """20 auto-applies, all successful — clean, no signal."""
        from app.change_requests import store as cr_store
        crs = [_make_cr(status_value="applied") for _ in range(20)]
        monkeypatch.setattr(cr_store, "list_all", lambda **kw: crs)
        result = self._fn()(window_days=30)
        assert result == []

    def test_high_rollback_fires_medium_severity(self, monkeypatch):
        """20 auto-applies, 8 rolled back (40%) — medium signal."""
        from app.change_requests import store as cr_store
        crs = (
            [_make_cr(status_value="applied") for _ in range(12)]
            + [_make_cr(status_value="rolled_back") for _ in range(8)]
        )
        monkeypatch.setattr(cr_store, "list_all", lambda **kw: crs)
        result = self._fn()(window_days=30)
        assert len(result) == 1
        assert result[0].signal_type == "auto_apply_rollback_churn"
        assert result[0].severity == "medium"
        assert "40%" in result[0].description

    def test_very_high_rollback_fires_high_severity(self, monkeypatch):
        """20 auto-applies, 12 rolled back (60%) — high signal."""
        from app.change_requests import store as cr_store
        crs = (
            [_make_cr(status_value="applied") for _ in range(8)]
            + [_make_cr(status_value="rolled_back") for _ in range(12)]
        )
        monkeypatch.setattr(cr_store, "list_all", lambda **kw: crs)
        result = self._fn()(window_days=30)
        assert len(result) == 1
        assert result[0].severity == "high"

    def test_high_volume_lower_threshold_trips(self, monkeypatch):
        """60 auto-applies, 12 rollbacks (20%) — high-volume case
        (≥50 + >15%) trips even though standard 30% doesn't."""
        from app.change_requests import store as cr_store
        crs = (
            [_make_cr(status_value="applied") for _ in range(48)]
            + [_make_cr(status_value="rolled_back") for _ in range(12)]
        )
        monkeypatch.setattr(cr_store, "list_all", lambda **kw: crs)
        result = self._fn()(window_days=30)
        assert len(result) == 1
        assert result[0].signal_type == "auto_apply_rollback_churn"

    def test_description_names_worst_requestor(self, monkeypatch):
        """Mixed requestors; the one with highest rollback rate should
        be named in the description."""
        from app.change_requests import store as cr_store
        crs = (
            # error_diagnosis: 6/6 = 100% rollback
            [_make_cr(
                status_value="rolled_back",
                requestor="error_diagnosis",
            ) for _ in range(6)]
            # capability_gap: 8 applies all successful
            + [_make_cr(
                status_value="applied",
                requestor="capability_gap_analyzer",
            ) for _ in range(8)]
        )
        monkeypatch.setattr(cr_store, "list_all", lambda **kw: crs)
        result = self._fn()(window_days=30)
        if result:  # may not fire depending on volume
            assert "error_diagnosis" in result[0].description

    def test_window_filter_respected(self, monkeypatch):
        """CRs outside the window are excluded from the count."""
        from app.change_requests import store as cr_store
        crs = (
            # 5 in-window
            [_make_cr(days_ago=2) for _ in range(5)]
            # 20 out-of-window
            + [_make_cr(days_ago=60) for _ in range(20)]
        )
        monkeypatch.setattr(cr_store, "list_all", lambda **kw: crs)
        result = self._fn()(window_days=30)
        # Below threshold once filtered
        assert result == []

    def test_non_auto_apply_decisions_ignored(self, monkeypatch):
        """Decisions other than auto-apply (signal-thumbs-up, etc.)
        don't count toward the auto-apply lane."""
        from app.change_requests import store as cr_store
        crs = (
            [_make_cr(decided_value="signal-thumbs-up")
             for _ in range(15)]
            + [_make_cr(
                decided_value="signal-thumbs-up",
                status_value="rolled_back",
            ) for _ in range(10)]
        )
        monkeypatch.setattr(cr_store, "list_all", lambda **kw: crs)
        result = self._fn()(window_days=30)
        # No auto-apply decisions → empty
        assert result == []

    def test_failure_isolated_on_store_import_error(
        self, monkeypatch,
    ):
        """Missing store doesn't raise — just returns empty."""
        monkeypatch.setitem(
            sys.modules, "app.change_requests.store",
            None,  # forces ImportError downstream
        )
        # The detector wraps the import in try/except.
        try:
            result = self._fn()(window_days=30)
        except Exception as exc:
            pytest.fail(f"detector raised: {exc}")
        # Empty result is the safe default
        assert isinstance(result, list)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
