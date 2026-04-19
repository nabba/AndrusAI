"""Tests for trajectory health metrics (Phase 6 observability)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _FakeSettings:
    def __init__(self, trajectory=True, tip_synth=False):
        self.trajectory_enabled = trajectory
        self.attribution_enabled = True
        self.tip_synthesis_enabled = tip_synth
        self.task_conditional_retrieval_enabled = False
        self.observer_calibration_enabled = True


def _with_settings(monkeypatch, **kw):
    import app.config as config_mod
    monkeypatch.setattr(config_mod, "get_settings",
                        lambda: _FakeSettings(**kw))


def test_trajectory_health_summary_flag_off(monkeypatch):
    _with_settings(monkeypatch, trajectory=False)
    from app.self_improvement.metrics import trajectory_health_summary
    out = trajectory_health_summary()
    assert out["trajectories_captured"] == 0
    assert out["attribution_fire_rate"] == 0.0
    assert out["verdict_counts"] == {
        "failure": 0, "recovery": 0, "optimization": 0, "baseline": 0,
    }


def test_trajectory_health_summary_full(monkeypatch, tmp_path):
    _with_settings(monkeypatch, trajectory=True, tip_synth=True)
    # Redirect trajectory storage to tmp_path
    import app.trajectory.store as store_mod
    import app.trajectory.calibration as cal_mod
    import app.trajectory.effectiveness as eff_mod
    monkeypatch.setattr(store_mod, "_ROOT", tmp_path)
    monkeypatch.setattr(cal_mod, "_LOG_PATH", tmp_path / "cal.jsonl")
    monkeypatch.setattr(eff_mod, "_LOG_PATH", tmp_path / "eff.jsonl")

    from app.trajectory.types import (
        Trajectory, TrajectoryStep, AttributionRecord,
        STEP_PHASE_CREW, VERDICT_FAILURE, VERDICT_BASELINE,
    )
    from app.trajectory.store import persist_trajectory, persist_attribution

    # Build 3 trajectories — 2 with attribution
    for i, verdict in enumerate([VERDICT_FAILURE, VERDICT_BASELINE, None]):
        traj = Trajectory(
            trajectory_id=f"traj_{i}", task_id="t", crew_name="coding",
            task_description=f"task {i}",
            started_at="2026-04-19T10:00:00+00:00",
            outcome_summary={"passed_quality_gate": verdict != VERDICT_FAILURE},
        )
        traj.append_step(TrajectoryStep(
            step_idx=-1, agent_role="coding", phase=STEP_PHASE_CREW,
        ))
        persist_trajectory(traj)
        if verdict is not None:
            attr = AttributionRecord(
                attribution_id=f"attr_{i}", trajectory_id=f"traj_{i}",
                verdict=verdict, failure_mode="none",
                attributed_step_idx=0, confidence=0.5, narrative="x",
            )
            persist_attribution(traj, attr)

    # Empty calibration + effectiveness logs are fine
    from app.self_improvement.metrics import trajectory_health_summary
    out = trajectory_health_summary()
    assert out["trajectories_captured"] == 3
    assert out["attributions_recorded"] == 2
    # fire rate = 2/3 ≈ 0.667
    assert 0.6 < out["attribution_fire_rate"] < 0.7
    assert out["verdict_counts"]["failure"] == 1
    assert out["verdict_counts"]["baseline"] == 1
    assert out["trajectory_tips_enabled"] is True
    # Calibration report is present (empty is still valid shape)
    assert "observer_calibration" in out
    assert "top_tips" in out
    assert "worst_tips" in out


def test_health_summary_includes_trajectory_block(monkeypatch):
    _with_settings(monkeypatch, trajectory=True)
    from app.self_improvement.metrics import health_summary
    out = health_summary()
    # `trajectory` sub-dict must always be present (may be the zero-defaults)
    assert "trajectory" in out
    assert isinstance(out["trajectory"], dict)
