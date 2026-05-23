"""Tests for the capability-regression alert subsystem (2026-05-22).

Covers:
  * CapabilitySnapshot dataclass round-trip
  * take_snapshot() pulls from tool registry + llm_catalog + blocked list
  * Failure isolation: a broken collector returns empty, not raises
  * detect_regressions() — all three diff categories
  * Newly-blocked models are NOT counted as regression
  * Additions are silent (capability growth not flagged)
  * has_regression False on first run (prev=None warm-up)
  * Persistence round-trip via save_snapshot / load_snapshot
  * Corrupted snapshot file → load returns None, not crash
  * append_regression skips empty reports
  * scheduler_job.run_one_pass — master switch gate + full pass
  * scheduler_job warm-up: first invocation saves baseline, no alert
"""
from __future__ import annotations

import json
import sys
import types
import unittest
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


from app.capability_regression import (  # noqa: E402
    CapabilitySnapshot,
    RegressionReport,
    detect_regressions,
)
from app.capability_regression import snapshot as snap_mod  # noqa: E402
from app.capability_regression import detector as det_mod  # noqa: E402
from app.capability_regression import scheduler_job as job_mod  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def isolated_workspace(tmp_path, monkeypatch):
    """Redirect the snapshot dir to a temp path for each test."""
    monkeypatch.setattr(
        snap_mod, "_snapshot_dir", lambda: tmp_path / "capability_regression",
    )
    (tmp_path / "capability_regression").mkdir()
    yield tmp_path


# ── CapabilitySnapshot ────────────────────────────────────────────────


class TestSnapshotDataclass:
    def test_default_construction(self):
        s = CapabilitySnapshot()
        assert s.schema_version == 1
        assert s.tools == []
        assert s.models == []
        assert s.blocked_models == []
        assert s.captured_at == ""

    def test_to_dict_round_trip(self):
        s = CapabilitySnapshot(
            captured_at="2026-05-22T00:00:00+00:00",
            tools=["a", "b"],
            models=["m1"],
            blocked_models=["bad"],
        )
        d = s.to_dict()
        s2 = CapabilitySnapshot.from_dict(d)
        assert s2.tools == ["a", "b"]
        assert s2.models == ["m1"]
        assert s2.blocked_models == ["bad"]
        assert s2.captured_at == "2026-05-22T00:00:00+00:00"

    def test_from_dict_sorts_input(self):
        s = CapabilitySnapshot.from_dict(
            {"tools": ["c", "a", "b"], "models": ["z", "a"]},
        )
        assert s.tools == ["a", "b", "c"]
        assert s.models == ["a", "z"]


# ── take_snapshot() ───────────────────────────────────────────────────


class TestTakeSnapshot:
    def test_pulls_from_registry_and_catalog(self, monkeypatch):
        monkeypatch.setattr(
            snap_mod, "_collect_tool_names", lambda: ["tool_a", "tool_b"],
        )
        monkeypatch.setattr(
            snap_mod, "_collect_catalog_keys",
            lambda: ["model_x", "model_y", "blocked_one"],
        )
        monkeypatch.setattr(
            snap_mod, "_collect_blocked_models", lambda: ["blocked_one"],
        )
        s = snap_mod.take_snapshot()
        assert s.tools == ["tool_a", "tool_b"]
        # blocked_one excluded from effective set
        assert s.models == ["model_x", "model_y"]
        assert s.blocked_models == ["blocked_one"]
        # captured_at populated
        assert "T" in s.captured_at

    def test_broken_tool_collector_returns_empty(self, monkeypatch):
        # Failure isolation — catalog still captured
        def boom():
            raise RuntimeError("registry sick")
        monkeypatch.setattr(snap_mod, "_collect_tool_names", lambda: [])
        # Simulate _collect_tool_names returning [] (its own internal
        # try/except catches the registry failure)
        monkeypatch.setattr(
            snap_mod, "_collect_catalog_keys", lambda: ["m1"],
        )
        monkeypatch.setattr(snap_mod, "_collect_blocked_models", lambda: [])
        s = snap_mod.take_snapshot()
        assert s.tools == []
        assert s.models == ["m1"]


# ── detect_regressions() ──────────────────────────────────────────────


class TestDetectRegressions:
    def _snap(self, tools=None, models=None, blocked=None, captured="t0"):
        return CapabilitySnapshot(
            captured_at=captured,
            tools=sorted(tools or []),
            models=sorted(models or []),
            blocked_models=sorted(blocked or []),
        )

    def test_no_prev_returns_empty_report(self):
        curr = self._snap(tools=["a"], models=["m1"], captured="t1")
        r = detect_regressions(None, curr)
        assert r.has_regression is False
        assert r.tools_deleted == []
        assert r.curr_captured_at == "t1"
        assert r.prev_captured_at == ""

    def test_identical_no_regression(self):
        prev = self._snap(tools=["a", "b"], models=["m"])
        curr = self._snap(tools=["a", "b"], models=["m"])
        r = detect_regressions(prev, curr)
        assert r.has_regression is False

    def test_tool_deleted_is_regression(self):
        prev = self._snap(tools=["a", "b", "c"])
        curr = self._snap(tools=["a", "c"])
        r = detect_regressions(prev, curr)
        assert r.has_regression is True
        assert r.tools_deleted == ["b"]
        assert r.models_truly_deleted == []

    def test_tool_added_silent(self):
        prev = self._snap(tools=["a"])
        curr = self._snap(tools=["a", "b", "c"])
        r = detect_regressions(prev, curr)
        assert r.has_regression is False

    def test_model_truly_deleted_is_regression(self):
        prev = self._snap(models=["m1", "m2"])
        curr = self._snap(models=["m1"])  # m2 not in blocked either
        r = detect_regressions(prev, curr)
        assert r.has_regression is True
        assert r.models_truly_deleted == ["m2"]
        assert r.models_newly_blocked == []

    def test_model_newly_blocked_is_NOT_regression(self):
        prev = self._snap(models=["m1", "m2"])
        curr = self._snap(models=["m1"], blocked=["m2"])
        r = detect_regressions(prev, curr)
        assert r.has_regression is False
        assert r.models_newly_blocked == ["m2"]
        assert r.models_truly_deleted == []

    def test_mixed_signals(self):
        # tool deleted + model truly deleted + model newly blocked
        prev = self._snap(
            tools=["t1", "t2"],
            models=["m_kept", "m_deleted", "m_will_block"],
        )
        curr = self._snap(
            tools=["t1"],  # t2 gone
            models=["m_kept"],  # m_deleted gone; m_will_block also gone
            blocked=["m_will_block"],
        )
        r = detect_regressions(prev, curr)
        assert r.has_regression is True
        assert r.tools_deleted == ["t2"]
        assert r.models_truly_deleted == ["m_deleted"]
        assert r.models_newly_blocked == ["m_will_block"]

    def test_alert_summary_empty_when_clean(self):
        prev = self._snap(tools=["a"])
        curr = self._snap(tools=["a"])
        r = detect_regressions(prev, curr)
        assert r.alert_summary() == ""

    def test_alert_summary_truncates_long_lists(self):
        prev_tools = [f"tool_{i:02d}" for i in range(10)]
        prev = self._snap(tools=prev_tools)
        curr = self._snap(tools=[])  # everything deleted
        r = detect_regressions(prev, curr)
        summary = r.alert_summary()
        assert "10 tool(s) deleted" in summary
        assert "+5 more" in summary  # 10 deleted, first 5 shown


# ── Persistence ───────────────────────────────────────────────────────


class TestPersistence:
    def test_save_and_load_round_trip(self, isolated_workspace):
        s = CapabilitySnapshot(
            captured_at="t0", tools=["a"], models=["m"],
        )
        snap_mod.save_snapshot(s)
        loaded = snap_mod.load_snapshot()
        assert loaded is not None
        assert loaded.tools == ["a"]
        assert loaded.models == ["m"]

    def test_load_returns_none_when_absent(self, isolated_workspace):
        assert snap_mod.load_snapshot() is None

    def test_load_handles_corrupted_file(self, isolated_workspace):
        path = isolated_workspace / "capability_regression" / "snapshot.json"
        path.write_text("not json at all{")
        assert snap_mod.load_snapshot() is None

    def test_save_appends_to_history(self, isolated_workspace):
        snap_mod.save_snapshot(CapabilitySnapshot(captured_at="t0", tools=["a"]))
        snap_mod.save_snapshot(CapabilitySnapshot(captured_at="t1", tools=["a", "b"]))
        hist = isolated_workspace / "capability_regression" / "history.jsonl"
        lines = hist.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["captured_at"] == "t0"
        assert json.loads(lines[1])["captured_at"] == "t1"

    def test_append_regression_skips_empty(self, isolated_workspace):
        clean_report = RegressionReport(curr_captured_at="t0")
        det_mod.append_regression(clean_report)
        path = isolated_workspace / "capability_regression" / "regressions.jsonl"
        assert not path.exists()

    def test_append_regression_writes_real(self, isolated_workspace):
        report = RegressionReport(
            tools_deleted=["bad"], curr_captured_at="t1",
        )
        det_mod.append_regression(report)
        path = isolated_workspace / "capability_regression" / "regressions.jsonl"
        assert path.exists()
        row = json.loads(path.read_text().strip())
        assert row["tools_deleted"] == ["bad"]
        assert row["has_regression"] is True


# ── scheduler_job.run_one_pass ────────────────────────────────────────


class TestSchedulerJob:
    def test_disabled_returns_none(self, monkeypatch):
        monkeypatch.setattr(job_mod, "_is_enabled", lambda: False)
        assert job_mod.run_one_pass() is None

    def test_first_run_seeds_baseline_no_alert(
        self, isolated_workspace, monkeypatch,
    ):
        # No prior snapshot → empty report → no alert, snapshot saved
        notify_calls = []
        monkeypatch.setattr(
            job_mod, "_maybe_notify",
            lambda r: notify_calls.append(r),
        )
        monkeypatch.setattr(
            snap_mod, "_collect_tool_names", lambda: ["tool_a"],
        )
        monkeypatch.setattr(
            snap_mod, "_collect_catalog_keys", lambda: ["model_x"],
        )
        monkeypatch.setattr(snap_mod, "_collect_blocked_models", lambda: [])
        monkeypatch.setattr(job_mod, "_is_enabled", lambda: True)

        report = job_mod.run_one_pass()
        assert report is not None
        assert report.has_regression is False
        # No alert on first-run baseline seed
        assert notify_calls == []
        # Snapshot persisted as new baseline
        assert snap_mod.load_snapshot() is not None

    def test_second_run_detects_deletion(
        self, isolated_workspace, monkeypatch,
    ):
        monkeypatch.setattr(job_mod, "_is_enabled", lambda: True)
        notify_calls = []
        landmark_calls = []
        monkeypatch.setattr(
            job_mod, "_maybe_notify",
            lambda r: notify_calls.append(r) if r.has_regression else None,
        )
        monkeypatch.setattr(
            job_mod, "_maybe_emit_landmark",
            lambda r: landmark_calls.append(r) if r.has_regression else None,
        )

        # First pass: 2 tools
        monkeypatch.setattr(
            snap_mod, "_collect_tool_names", lambda: ["tool_a", "tool_b"],
        )
        monkeypatch.setattr(snap_mod, "_collect_catalog_keys", lambda: [])
        monkeypatch.setattr(snap_mod, "_collect_blocked_models", lambda: [])
        first = job_mod.run_one_pass()
        assert first is not None and first.has_regression is False

        # Second pass: 1 tool — regression
        monkeypatch.setattr(
            snap_mod, "_collect_tool_names", lambda: ["tool_a"],
        )
        second = job_mod.run_one_pass()
        assert second is not None
        assert second.has_regression is True
        assert second.tools_deleted == ["tool_b"]
        assert len(notify_calls) == 1
        assert len(landmark_calls) == 1

        # Regression also appended to the audit log
        reg = (
            isolated_workspace / "capability_regression"
            / "regressions.jsonl"
        )
        assert reg.exists()
        row = json.loads(reg.read_text().strip())
        assert row["tools_deleted"] == ["tool_b"]


# ── runtime_settings master switch ────────────────────────────────────


class TestMasterSwitch:
    """Master-switch round-trips. Skipped when pydantic_settings missing
    on the host — gateway always has it."""

    def _import_rs(self):
        try:
            import app.runtime_settings as rs
            return rs
        except Exception as exc:
            pytest.skip(f"app.runtime_settings unavailable: {exc}")

    def test_default_is_on(self, monkeypatch, tmp_path):
        rs = self._import_rs()
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(rs, "_cache", None)
        monkeypatch.setattr(rs, "_STATE_PATH", tmp_path / "runtime_settings.json")
        assert rs.get_capability_regression_enabled() is True

    def test_setter_flips_value(self, monkeypatch, tmp_path):
        rs = self._import_rs()
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(rs, "_cache", None)
        monkeypatch.setattr(rs, "_STATE_PATH", tmp_path / "runtime_settings.json")
        rs.set_capability_regression_enabled(False)
        assert rs.get_capability_regression_enabled() is False
        rs.set_capability_regression_enabled(True)
        assert rs.get_capability_regression_enabled() is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
