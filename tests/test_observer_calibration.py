"""Tests for app.trajectory.calibration.

Verify:
  - Flag-off is a perfect no-op (no JSONL file created).
  - record_calibration appends a well-formed row.
  - precision_recall_report produces correct TP/FP/FN counts.
  - _scan_and_emit triggers OBSERVER_MIS_PREDICTION only above threshold.
  - Below MIN_SAMPLES, no gap is emitted.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _FakeSettings:
    def __init__(self, calibration: bool = True):
        self.trajectory_enabled = True
        self.attribution_enabled = True
        self.tip_synthesis_enabled = False
        self.task_conditional_retrieval_enabled = False
        self.observer_calibration_enabled = calibration


def _with_flag(monkeypatch, enabled: bool = True):
    import app.config as config_mod
    monkeypatch.setattr(config_mod, "get_settings",
                        lambda: _FakeSettings(calibration=enabled))


def _make_pair(predicted: str, actual: str, traj_id: str = "traj_x",
                pred_conf: float = 0.8, attr_conf: float = 0.8):
    """Build a (trajectory, attribution) pair with the given Observer prediction
    and Attribution failure_mode."""
    from app.trajectory.types import (
        Trajectory, TrajectoryStep, AttributionRecord,
        STEP_PHASE_OBSERVER, STEP_PHASE_CREW, VERDICT_FAILURE,
    )
    traj = Trajectory(
        trajectory_id=traj_id, task_id="t", crew_name="coding",
        task_description="test",
        outcome_summary={"passed_quality_gate": False},
    )
    traj.append_step(TrajectoryStep(
        step_idx=-1, agent_role="coding", phase=STEP_PHASE_OBSERVER,
        observer_prediction={
            "predicted_failure_mode": predicted,
            "confidence": pred_conf,
        },
    ))
    traj.append_step(TrajectoryStep(
        step_idx=-1, agent_role="coding", phase=STEP_PHASE_CREW,
    ))
    attribution = AttributionRecord(
        attribution_id=f"attr_{traj_id}",
        trajectory_id=traj_id,
        verdict=VERDICT_FAILURE,
        failure_mode=actual,
        attributed_step_idx=1,
        confidence=attr_conf,
        narrative="x",
    )
    return traj, attribution


def test_flag_off_is_noop(monkeypatch, tmp_path):
    _with_flag(monkeypatch, enabled=False)
    # Redirect log path so we can verify nothing was written
    import app.trajectory.calibration as cal_mod
    monkeypatch.setattr(cal_mod, "_LOG_PATH", tmp_path / "cal.jsonl")

    from app.trajectory.calibration import record_calibration
    traj, attr = _make_pair("fix_spiral", "fix_spiral")
    assert record_calibration(traj, attr) is False
    assert not (tmp_path / "cal.jsonl").exists()


def test_records_pair_row(monkeypatch, tmp_path):
    _with_flag(monkeypatch, enabled=True)
    import app.trajectory.calibration as cal_mod
    monkeypatch.setattr(cal_mod, "_LOG_PATH", tmp_path / "cal.jsonl")

    # Prevent scan from hitting the gap store
    with patch("app.trajectory.calibration._scan_and_emit", return_value=0):
        from app.trajectory.calibration import record_calibration
        traj, attr = _make_pair("fix_spiral", "fix_spiral",
                                traj_id="traj_001", pred_conf=0.9, attr_conf=0.7)
        assert record_calibration(traj, attr) is True

    lines = (tmp_path / "cal.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["trajectory_id"] == "traj_001"
    assert row["crew_name"] == "coding"
    assert row["predicted_mode"] == "fix_spiral"
    assert row["actual_mode"] == "fix_spiral"
    assert row["predicted_confidence"] == 0.9
    assert row["attribution_confidence"] == 0.7


def test_handles_missing_observer_prediction(monkeypatch, tmp_path):
    """A trajectory without an observer step produces 'none' predicted_mode."""
    _with_flag(monkeypatch, enabled=True)
    import app.trajectory.calibration as cal_mod
    monkeypatch.setattr(cal_mod, "_LOG_PATH", tmp_path / "cal.jsonl")

    from app.trajectory.types import (
        Trajectory, TrajectoryStep, AttributionRecord,
        STEP_PHASE_CREW, VERDICT_FAILURE,
    )
    traj = Trajectory(
        trajectory_id="traj_no_obs", task_id="t", crew_name="coding",
        task_description="test",
        outcome_summary={"passed_quality_gate": False},
    )
    traj.append_step(TrajectoryStep(
        step_idx=-1, agent_role="coding", phase=STEP_PHASE_CREW,
    ))
    attr = AttributionRecord(
        attribution_id="attr_x", trajectory_id="traj_no_obs",
        verdict=VERDICT_FAILURE, failure_mode="scope_creep",
        attributed_step_idx=0, confidence=0.6, narrative="x",
    )
    with patch("app.trajectory.calibration._scan_and_emit", return_value=0):
        from app.trajectory.calibration import record_calibration
        assert record_calibration(traj, attr) is True

    row = json.loads((tmp_path / "cal.jsonl").read_text().strip())
    assert row["predicted_mode"] == "none"
    assert row["actual_mode"] == "scope_creep"


def test_precision_recall_math(monkeypatch, tmp_path):
    _with_flag(monkeypatch, enabled=True)
    import app.trajectory.calibration as cal_mod
    monkeypatch.setattr(cal_mod, "_LOG_PATH", tmp_path / "cal.jsonl")

    # Set up rows manually so we bypass _scan_and_emit's side effects.
    # For failure_mode = "fix_spiral":
    #   tp = 3  (predicted fix_spiral, actual fix_spiral)
    #   fp = 2  (predicted fix_spiral, actual != fix_spiral)
    #   fn = 1  (predicted != fix_spiral, actual fix_spiral)
    # → precision = 3/(3+2) = 0.6
    # → recall    = 3/(3+1) = 0.75
    rows = [
        {"predicted_mode": "fix_spiral", "actual_mode": "fix_spiral"},  # tp
        {"predicted_mode": "fix_spiral", "actual_mode": "fix_spiral"},  # tp
        {"predicted_mode": "fix_spiral", "actual_mode": "fix_spiral"},  # tp
        {"predicted_mode": "fix_spiral", "actual_mode": "scope_creep"}, # fp
        {"predicted_mode": "fix_spiral", "actual_mode": "none"},        # fp
        {"predicted_mode": "none", "actual_mode": "fix_spiral"},        # fn
    ]
    (tmp_path / "cal.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n"
    )

    from app.trajectory.calibration import precision_recall_report
    report = precision_recall_report()
    assert report["samples"] == 6
    fs = report["per_mode"]["fix_spiral"]
    assert fs["tp"] == 3
    assert fs["fp"] == 2
    assert fs["fn"] == 1
    assert fs["precision"] == 0.6
    assert fs["recall"] == 0.75


def test_scan_below_min_samples_no_emit(monkeypatch, tmp_path):
    """Fewer than _MIN_SAMPLES rows → no gaps, even at 100% miss rate."""
    _with_flag(monkeypatch, enabled=True)
    import app.trajectory.calibration as cal_mod
    monkeypatch.setattr(cal_mod, "_LOG_PATH", tmp_path / "cal.jsonl")

    rows = [
        {"predicted_mode": "fix_spiral", "actual_mode": "none",
         "trajectory_id": f"t{i}"}
        for i in range(5)  # < _MIN_SAMPLES (10)
    ]
    (tmp_path / "cal.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n"
    )

    with patch("app.self_improvement.gap_detector.emit_observer_mis_prediction") as mock_emit:
        from app.trajectory.calibration import _scan_and_emit
        assert _scan_and_emit() == 0
        mock_emit.assert_not_called()


def test_scan_high_fp_rate_emits_gap(monkeypatch, tmp_path):
    """When ≥70% of Observer predictions of a mode are wrong over ≥10 samples,
    emit a false_positive OBSERVER_MIS_PREDICTION gap."""
    _with_flag(monkeypatch, enabled=True)
    import app.trajectory.calibration as cal_mod
    monkeypatch.setattr(cal_mod, "_LOG_PATH", tmp_path / "cal.jsonl")

    # 12 predictions of fix_spiral, actual was fix_spiral only 3×
    # fp_rate = 9/12 = 0.75 ≥ _FP_RATE_THRESHOLD (0.70)
    rows = []
    for i in range(3):
        rows.append({"predicted_mode": "fix_spiral",
                     "actual_mode": "fix_spiral",
                     "trajectory_id": f"tp_{i}"})
    for i in range(9):
        rows.append({"predicted_mode": "fix_spiral",
                     "actual_mode": "none",
                     "trajectory_id": f"fp_{i}"})
    (tmp_path / "cal.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n"
    )

    captured: list[dict] = []
    def fake_emit(**kwargs):
        captured.append(kwargs)
        return True

    with patch("app.self_improvement.gap_detector.emit_observer_mis_prediction",
               side_effect=fake_emit):
        from app.trajectory.calibration import _scan_and_emit
        n = _scan_and_emit()

    assert n >= 1
    # At least one false_positive emission for fix_spiral
    assert any(c.get("failure_mode") == "fix_spiral"
               and c.get("miss_kind") == "false_positive"
               for c in captured)


def test_scan_high_fn_rate_emits_gap(monkeypatch, tmp_path):
    """When the actual mode was X ≥10 times and Observer missed ≥70% of them,
    emit a false_negative OBSERVER_MIS_PREDICTION gap."""
    _with_flag(monkeypatch, enabled=True)
    import app.trajectory.calibration as cal_mod
    monkeypatch.setattr(cal_mod, "_LOG_PATH", tmp_path / "cal.jsonl")

    # Actual = confidence_mirage 12 times; Observer predicted it only 2 times
    # fn_rate = 10/12 ≥ 0.70
    rows = []
    for i in range(2):
        rows.append({"predicted_mode": "confidence_mirage",
                     "actual_mode": "confidence_mirage",
                     "trajectory_id": f"tp_{i}"})
    for i in range(10):
        rows.append({"predicted_mode": "none",
                     "actual_mode": "confidence_mirage",
                     "trajectory_id": f"fn_{i}"})
    (tmp_path / "cal.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n"
    )

    captured: list[dict] = []
    def fake_emit(**kwargs):
        captured.append(kwargs)
        return True

    with patch("app.self_improvement.gap_detector.emit_observer_mis_prediction",
               side_effect=fake_emit):
        from app.trajectory.calibration import _scan_and_emit
        n = _scan_and_emit()

    assert n >= 1
    assert any(c.get("failure_mode") == "confidence_mirage"
               and c.get("miss_kind") == "false_negative"
               for c in captured)


def test_scan_low_miss_rate_no_emit(monkeypatch, tmp_path):
    """Observer with good accuracy → no gaps emitted."""
    _with_flag(monkeypatch, enabled=True)
    import app.trajectory.calibration as cal_mod
    monkeypatch.setattr(cal_mod, "_LOG_PATH", tmp_path / "cal.jsonl")

    # 10 predictions of fix_spiral, 9 correct → fp_rate = 0.10
    rows = [
        {"predicted_mode": "fix_spiral", "actual_mode": "fix_spiral",
         "trajectory_id": f"t{i}"}
        for i in range(9)
    ] + [
        {"predicted_mode": "fix_spiral", "actual_mode": "none",
         "trajectory_id": "t_fp"}
    ]
    (tmp_path / "cal.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n"
    )

    with patch("app.self_improvement.gap_detector.emit_observer_mis_prediction") as mock_emit:
        from app.trajectory.calibration import _scan_and_emit
        assert _scan_and_emit() == 0
        mock_emit.assert_not_called()


def test_tail_bounded_window(monkeypatch, tmp_path):
    """_tail reads at most _WINDOW_SIZE most-recent rows."""
    import app.trajectory.calibration as cal_mod
    monkeypatch.setattr(cal_mod, "_LOG_PATH", tmp_path / "cal.jsonl")

    rows = [{"predicted_mode": "x", "actual_mode": "y",
             "trajectory_id": f"t{i}"} for i in range(250)]
    (tmp_path / "cal.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n"
    )

    from app.trajectory.calibration import _tail, _WINDOW_SIZE
    out = _tail(_WINDOW_SIZE)
    assert len(out) == _WINDOW_SIZE
    # Most recent rows — the last one should be t249
    assert out[-1]["trajectory_id"] == "t249"
