"""Tests for Phase 6: tip effectiveness tracking.

Verify:
  - Trajectory.injected_skill_ids persistence round-trips
  - note_injected_skills appends unique ids to the active trajectory
  - record_use writes one JSONL row per injected skill
  - effectiveness_report aggregates correctly
  - top_tips / worst_tips obey the min_uses gate
  - Flag-off is a no-op (nothing written)
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _FakeSettings:
    def __init__(self, trajectory: bool = True):
        self.trajectory_enabled = trajectory
        self.attribution_enabled = False
        self.tip_synthesis_enabled = False
        self.task_conditional_retrieval_enabled = False
        self.observer_calibration_enabled = False


def _with_trajectory(monkeypatch, enabled: bool = True):
    import app.config as config_mod
    monkeypatch.setattr(config_mod, "get_settings",
                        lambda: _FakeSettings(trajectory=enabled))


def _reset_context():
    from app.trajectory import logger as tlog
    tlog._current_trajectory.set(None)


def test_injected_skill_ids_persist_roundtrip():
    """Serialise/deserialise — ids survive."""
    from app.trajectory.types import Trajectory, TrajectoryStep, STEP_PHASE_CREW
    traj = Trajectory(
        trajectory_id="traj_inj_1", task_id="t", crew_name="research",
        task_description="x",
    )
    traj.append_step(TrajectoryStep(
        step_idx=-1, agent_role=traj.crew_name, phase=STEP_PHASE_CREW,
    ))
    traj.injected_skill_ids.extend(["skill_a", "skill_b"])

    d = traj.to_dict()
    assert d["injected_skill_ids"] == ["skill_a", "skill_b"]

    rehydrated = Trajectory.from_dict(d)
    assert rehydrated.injected_skill_ids == ["skill_a", "skill_b"]


def test_note_injected_skills_basic(monkeypatch):
    _with_trajectory(monkeypatch, True)
    _reset_context()

    from app.trajectory.logger import begin_trajectory, note_injected_skills, end_trajectory
    traj = begin_trajectory("t", "research", "task")
    assert note_injected_skills(["a", "b", "c"]) is True
    assert traj.injected_skill_ids == ["a", "b", "c"]
    end_trajectory()


def test_note_injected_skills_dedups(monkeypatch):
    _with_trajectory(monkeypatch, True)
    _reset_context()

    from app.trajectory.logger import begin_trajectory, note_injected_skills, end_trajectory
    traj = begin_trajectory("t", "research", "task")
    note_injected_skills(["a", "b"])
    note_injected_skills(["b", "c", ""])   # empty id filtered, b not re-added
    assert traj.injected_skill_ids == ["a", "b", "c"]
    end_trajectory()


def test_note_injected_skills_noop_when_flag_off(monkeypatch):
    _with_trajectory(monkeypatch, False)
    _reset_context()
    from app.trajectory.logger import note_injected_skills
    assert note_injected_skills(["a"]) is False


def test_note_injected_skills_without_active_trajectory(monkeypatch):
    _with_trajectory(monkeypatch, True)
    _reset_context()
    from app.trajectory.logger import note_injected_skills
    assert note_injected_skills(["a"]) is False


def test_record_use_writes_one_row_per_skill(monkeypatch, tmp_path):
    _with_trajectory(monkeypatch, True)
    import app.trajectory.effectiveness as eff_mod
    monkeypatch.setattr(eff_mod, "_LOG_PATH", tmp_path / "eff.jsonl")

    from app.trajectory.types import Trajectory, TrajectoryStep, STEP_PHASE_CREW
    traj = Trajectory(
        trajectory_id="traj_e_1", task_id="t", crew_name="coding",
        task_description="t",
        outcome_summary={"passed_quality_gate": True, "retries": 0,
                         "difficulty": 5},
    )
    traj.append_step(TrajectoryStep(
        step_idx=-1, agent_role=traj.crew_name, phase=STEP_PHASE_CREW,
    ))
    traj.injected_skill_ids = ["skill_1", "skill_2", "skill_3"]

    from app.trajectory.effectiveness import record_use
    n = record_use(traj)
    assert n == 3

    lines = (tmp_path / "eff.jsonl").read_text().strip().splitlines()
    assert len(lines) == 3
    rows = [json.loads(line) for line in lines]
    assert {r["skill_id"] for r in rows} == {"skill_1", "skill_2", "skill_3"}
    for r in rows:
        assert r["trajectory_id"] == "traj_e_1"
        assert r["crew_name"] == "coding"
        assert r["passed_quality_gate"] is True


def test_record_use_with_attribution_captures_verdict(monkeypatch, tmp_path):
    _with_trajectory(monkeypatch, True)
    import app.trajectory.effectiveness as eff_mod
    monkeypatch.setattr(eff_mod, "_LOG_PATH", tmp_path / "eff.jsonl")

    from app.trajectory.types import (
        Trajectory, TrajectoryStep, AttributionRecord,
        STEP_PHASE_CREW, VERDICT_RECOVERY,
    )
    traj = Trajectory(
        trajectory_id="traj_a", task_id="t", crew_name="coding",
        task_description="t",
        outcome_summary={"passed_quality_gate": True, "retries": 1,
                         "difficulty": 5},
    )
    traj.append_step(TrajectoryStep(
        step_idx=-1, agent_role=traj.crew_name, phase=STEP_PHASE_CREW,
    ))
    traj.injected_skill_ids = ["skill_x"]
    attr = AttributionRecord(
        attribution_id="a", trajectory_id="traj_a",
        verdict=VERDICT_RECOVERY, failure_mode="fix_spiral",
        attributed_step_idx=0, confidence=0.7, narrative="recovered well",
        suggested_tip_type="recovery",
    )
    from app.trajectory.effectiveness import record_use
    assert record_use(traj, attr) == 1
    row = json.loads((tmp_path / "eff.jsonl").read_text().strip())
    assert row["verdict"] == "recovery"
    assert row["failure_mode"] == "fix_spiral"
    assert row["attribution_confidence"] == 0.7


def test_record_use_noop_without_injected_skills(monkeypatch, tmp_path):
    _with_trajectory(monkeypatch, True)
    import app.trajectory.effectiveness as eff_mod
    monkeypatch.setattr(eff_mod, "_LOG_PATH", tmp_path / "eff.jsonl")

    from app.trajectory.types import Trajectory
    traj = Trajectory(
        trajectory_id="traj_none", task_id="t", crew_name="c",
        task_description="t",
        outcome_summary={"passed_quality_gate": True},
    )
    # injected_skill_ids is [] → no rows
    from app.trajectory.effectiveness import record_use
    assert record_use(traj) == 0
    assert not (tmp_path / "eff.jsonl").exists()


def test_record_use_noop_when_flag_off(monkeypatch, tmp_path):
    _with_trajectory(monkeypatch, False)
    import app.trajectory.effectiveness as eff_mod
    monkeypatch.setattr(eff_mod, "_LOG_PATH", tmp_path / "eff.jsonl")

    from app.trajectory.types import Trajectory
    traj = Trajectory(
        trajectory_id="traj_off", task_id="t", crew_name="c",
        task_description="t",
        outcome_summary={"passed_quality_gate": True},
    )
    traj.injected_skill_ids = ["s1"]
    from app.trajectory.effectiveness import record_use
    assert record_use(traj) == 0
    assert not (tmp_path / "eff.jsonl").exists()


def test_effectiveness_report_aggregation(monkeypatch, tmp_path):
    import app.trajectory.effectiveness as eff_mod
    monkeypatch.setattr(eff_mod, "_LOG_PATH", tmp_path / "eff.jsonl")

    # skill_A: 3 uses, 2 successes → 0.667 effectiveness
    # skill_B: 2 uses, 0 successes → 0.0 effectiveness
    rows = [
        {"skill_id": "skill_A", "trajectory_id": "t1",
         "passed_quality_gate": True,  "retries": 0, "verdict": "recovery"},
        {"skill_id": "skill_A", "trajectory_id": "t2",
         "passed_quality_gate": True,  "retries": 1, "verdict": "baseline"},
        {"skill_id": "skill_A", "trajectory_id": "t3",
         "passed_quality_gate": False, "retries": 2, "verdict": "failure"},
        {"skill_id": "skill_B", "trajectory_id": "t4",
         "passed_quality_gate": False, "retries": 3, "verdict": "failure"},
        {"skill_id": "skill_B", "trajectory_id": "t5",
         "passed_quality_gate": False, "retries": 2, "verdict": "failure"},
    ]
    (tmp_path / "eff.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n"
    )

    from app.trajectory.effectiveness import effectiveness_report
    rep = effectiveness_report()
    assert rep["samples"] == 5
    a = rep["per_tip"]["skill_A"]
    assert a["uses"] == 3
    assert a["successes"] == 2
    assert a["failures"] == 1
    assert a["recoveries"] == 1
    assert a["effectiveness"] == 0.667
    assert a["retries_avg"] == 1.0   # (0+1+2)/3

    b = rep["per_tip"]["skill_B"]
    assert b["uses"] == 2
    assert b["successes"] == 0
    assert b["effectiveness"] == 0.0


def test_effectiveness_report_single_skill_filter(monkeypatch, tmp_path):
    import app.trajectory.effectiveness as eff_mod
    monkeypatch.setattr(eff_mod, "_LOG_PATH", tmp_path / "eff.jsonl")

    rows = [
        {"skill_id": "A", "passed_quality_gate": True, "retries": 0,
         "verdict": "baseline"},
        {"skill_id": "B", "passed_quality_gate": False, "retries": 0,
         "verdict": "failure"},
    ]
    (tmp_path / "eff.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n"
    )

    from app.trajectory.effectiveness import effectiveness_report
    rep = effectiveness_report(skill_record_id="A")
    assert set(rep["per_tip"].keys()) == {"A"}


def test_top_worst_tips_respects_min_uses(monkeypatch, tmp_path):
    import app.trajectory.effectiveness as eff_mod
    monkeypatch.setattr(eff_mod, "_LOG_PATH", tmp_path / "eff.jsonl")

    # tip_hot: 12 uses all successful  → effectiveness 1.0, qualifies
    # tip_small: 5 uses, 0 successful → effectiveness 0.0 but fails min_uses=10
    rows = []
    for i in range(12):
        rows.append({"skill_id": "tip_hot", "trajectory_id": f"h{i}",
                      "passed_quality_gate": True, "retries": 0,
                      "verdict": "baseline"})
    for i in range(5):
        rows.append({"skill_id": "tip_small", "trajectory_id": f"s{i}",
                      "passed_quality_gate": False, "retries": 0,
                      "verdict": "failure"})
    (tmp_path / "eff.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n"
    )

    from app.trajectory.effectiveness import top_tips, worst_tips
    top = top_tips(k=5, min_uses=10)
    assert any(t["skill_id"] == "tip_hot" for t in top)
    assert not any(t["skill_id"] == "tip_small" for t in top)

    worst = worst_tips(k=5, min_uses=10)
    assert any(t["skill_id"] == "tip_hot" for t in worst)
    assert not any(t["skill_id"] == "tip_small" for t in worst)
