"""Tests for app.trajectory.replay (Phase 6).

Verify:
  - replay() returns {} shape with None pieces when nothing exists
  - Full replay after trajectory + attribution + calibration + effectiveness
  - format_text renders all sections without raising
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_replay_missing_trajectory_returns_empty_bundle():
    from app.trajectory.replay import replay
    b = replay("traj_nonexistent")
    assert b["trajectory_id"] == "traj_nonexistent"
    assert b["trajectory"] is None
    assert b["attribution"] is None
    assert b["calibration"] is None
    assert b["effectiveness_rows"] == []


def test_replay_full_bundle(monkeypatch, tmp_path):
    """Full replay when all pieces exist on disk."""
    import app.trajectory.store as store_mod
    import app.trajectory.calibration as cal_mod
    import app.trajectory.effectiveness as eff_mod
    monkeypatch.setattr(store_mod, "_ROOT", tmp_path)
    monkeypatch.setattr(cal_mod, "_LOG_PATH", tmp_path / "cal.jsonl")
    monkeypatch.setattr(eff_mod, "_LOG_PATH", tmp_path / "eff.jsonl")

    # 1. Trajectory sidecar
    from app.trajectory.types import (
        Trajectory, TrajectoryStep, AttributionRecord,
        STEP_PHASE_OBSERVER, STEP_PHASE_CREW,
        VERDICT_FAILURE,
    )
    from app.trajectory.store import persist_trajectory, persist_attribution

    traj = Trajectory(
        trajectory_id="traj_full_1", task_id="t", crew_name="coding",
        task_description="broken fix",
        started_at="2026-04-19T10:00:00+00:00",
        outcome_summary={"passed_quality_gate": False,
                          "retries": 2, "difficulty": 6},
    )
    traj.append_step(TrajectoryStep(
        step_idx=-1, agent_role="coding", phase=STEP_PHASE_OBSERVER,
        observer_prediction={"predicted_failure_mode": "fix_spiral",
                              "confidence": 0.8},
    ))
    traj.append_step(TrajectoryStep(
        step_idx=-1, agent_role="coding", phase=STEP_PHASE_CREW,
        planned_action="ran broken loop", elapsed_ms=42000,
    ))
    traj.injected_skill_ids = ["skill_A", "skill_B"]
    assert persist_trajectory(traj) is True

    # 2. Attribution
    attr = AttributionRecord(
        attribution_id="attr_1", trajectory_id="traj_full_1",
        verdict=VERDICT_FAILURE, failure_mode="fix_spiral",
        attributed_step_idx=1, confidence=0.7,
        narrative="classic fix_spiral",
    )
    assert persist_attribution(traj, attr) is True

    # 3. Calibration row
    with open(tmp_path / "cal.jsonl", "w") as fh:
        fh.write(json.dumps({
            "trajectory_id": "traj_full_1",
            "predicted_mode": "fix_spiral",
            "predicted_confidence": 0.8,
            "actual_mode": "fix_spiral",
        }) + "\n")

    # 4. Effectiveness rows
    with open(tmp_path / "eff.jsonl", "w") as fh:
        for sid in ("skill_A", "skill_B"):
            fh.write(json.dumps({
                "skill_id": sid, "trajectory_id": "traj_full_1",
                "passed_quality_gate": False, "verdict": "failure",
            }) + "\n")

    from app.trajectory.replay import replay
    b = replay("traj_full_1")
    assert b["trajectory"] is not None
    assert b["trajectory"]["trajectory_id"] == "traj_full_1"
    assert len(b["trajectory"]["steps"]) == 2
    assert b["trajectory"]["injected_skill_ids"] == ["skill_A", "skill_B"]
    assert b["attribution"] is not None
    assert b["attribution"]["verdict"] == "failure"
    assert b["calibration"]["predicted_mode"] == "fix_spiral"
    assert len(b["effectiveness_rows"]) == 2
    assert {r["skill_id"] for r in b["effectiveness_rows"]} == {"skill_A", "skill_B"}


def test_format_text_renders_without_raising(monkeypatch, tmp_path):
    """format_text must produce a string — even on missing pieces — never raise."""
    import app.trajectory.store as store_mod
    monkeypatch.setattr(store_mod, "_ROOT", tmp_path)

    # Missing trajectory
    from app.trajectory.replay import format_text
    out = format_text("traj_missing")
    assert "not found" in out

    # With trajectory only
    from app.trajectory.types import (
        Trajectory, TrajectoryStep, STEP_PHASE_CREW,
    )
    from app.trajectory.store import persist_trajectory
    traj = Trajectory(
        trajectory_id="traj_fmt", task_id="t", crew_name="research",
        task_description="what is x",
        started_at="2026-04-19T10:00:00+00:00",
        outcome_summary={"passed_quality_gate": True},
    )
    traj.append_step(TrajectoryStep(
        step_idx=-1, agent_role="research", phase=STEP_PHASE_CREW,
        planned_action="ran", output_sample="done",
    ))
    persist_trajectory(traj)

    out = format_text("traj_fmt")
    assert "traj_fmt" in out
    assert "research" in out
    # When attribution missing, explicit note is rendered
    assert "attribution: (none" in out
