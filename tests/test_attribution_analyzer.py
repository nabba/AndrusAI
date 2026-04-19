"""Tests for app.trajectory.attribution.

These tests exercise:
  - _should_analyze gate logic (skip baseline, fire on failure/retry/slow/recovery)
  - field validation/coercion of parsed LLM output
  - safe_default behavior on crewai/LLM unavailable
  - gap emission with proper provenance
  - persistence of the AttributionRecord alongside the trajectory sidecar
  - baseline verdicts never emit gaps

The analyzer's LLM call is mocked — we validate the plumbing, not the
model's reasoning quality (that's a separate eval harness concern).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _FakeSettings:
    def __init__(self, attribution: bool = True, trajectory: bool = True):
        self.trajectory_enabled = trajectory
        self.attribution_enabled = attribution
        self.tip_synthesis_enabled = False
        self.task_conditional_retrieval_enabled = False
        self.observer_calibration_enabled = False


def _with_flags(monkeypatch, attribution: bool = True, trajectory: bool = True):
    import app.config as config_mod
    monkeypatch.setattr(config_mod, "get_settings",
                        lambda: _FakeSettings(attribution=attribution, trajectory=trajectory))


# ── Gate logic tests (no LLM) ────────────────────────────────────────────


def _make_trajectory(outcome: dict, steps_kwargs: list[dict] | None = None):
    from app.trajectory.types import (
        Trajectory, TrajectoryStep, STEP_PHASE_CREW, STEP_PHASE_OBSERVER,
    )
    import uuid
    traj = Trajectory(
        trajectory_id=f"traj_{uuid.uuid4().hex[:16]}",
        task_id="t", crew_name="research",
        task_description="test task",
        outcome_summary=outcome,
    )
    for kw in (steps_kwargs or []):
        phase = kw.pop("phase", STEP_PHASE_CREW)
        traj.append_step(TrajectoryStep(step_idx=-1, phase=phase, **kw))
    return traj


def test_gate_baseline_success_skipped():
    from app.trajectory.attribution import _should_analyze
    traj = _make_trajectory({
        "passed_quality_gate": True, "retries": 0,
        "duration_s": 5.0, "difficulty": 3, "reflexion_exhausted": False,
    })
    fire, reason = _should_analyze(traj)
    assert fire is False
    assert reason == "baseline"


def test_gate_failed_quality_fires():
    from app.trajectory.attribution import _should_analyze
    traj = _make_trajectory({
        "passed_quality_gate": False, "retries": 0,
        "duration_s": 5.0, "difficulty": 3,
    })
    fire, reason = _should_analyze(traj)
    assert fire is True
    assert reason == "quality_gate_failed"


def test_gate_retries_fires():
    from app.trajectory.attribution import _should_analyze
    traj = _make_trajectory({
        "passed_quality_gate": True, "retries": 1,
        "duration_s": 5.0, "difficulty": 3,
    })
    fire, reason = _should_analyze(traj)
    assert fire is True
    assert reason == "retried"


def test_gate_reflexion_exhausted_fires():
    from app.trajectory.attribution import _should_analyze
    traj = _make_trajectory({
        "passed_quality_gate": False, "retries": 2,
        "reflexion_exhausted": True,
        "duration_s": 5.0, "difficulty": 3,
    })
    fire, reason = _should_analyze(traj)
    assert fire is True
    # quality_gate_failed trips first; either acceptable outcome
    assert reason in ("quality_gate_failed", "reflexion_exhausted")


def test_gate_slow_fires():
    from app.trajectory.attribution import _should_analyze
    traj = _make_trajectory({
        "passed_quality_gate": True, "retries": 0,
        "duration_s": 60.0, "difficulty": 3,
    })
    fire, reason = _should_analyze(traj)
    assert fire is True
    assert reason == "slow"


def test_gate_recovery_fires_when_observer_predicted_failure():
    """Observer predicted confidence_mirage@0.8 but run succeeded → extract recovery."""
    from app.trajectory.types import (
        Trajectory, TrajectoryStep, STEP_PHASE_OBSERVER, STEP_PHASE_CREW,
    )
    from app.trajectory.attribution import _should_analyze
    traj = Trajectory(
        trajectory_id="traj_recovery", task_id="t",
        crew_name="research", task_description="test",
        outcome_summary={
            "passed_quality_gate": True, "retries": 0,
            "duration_s": 10.0, "difficulty": 3,
        },
    )
    traj.append_step(TrajectoryStep(
        step_idx=-1, agent_role="research", phase=STEP_PHASE_OBSERVER,
        observer_prediction={
            "predicted_failure_mode": "confidence_mirage",
            "confidence": 0.8,
            "recommendation": "verify with source",
        },
    ))
    traj.append_step(TrajectoryStep(
        step_idx=-1, agent_role="research", phase=STEP_PHASE_CREW,
        planned_action="executed", output_sample="ok",
    ))
    fire, reason = _should_analyze(traj)
    assert fire is True
    assert reason == "recovery"


def test_gate_observer_low_confidence_no_recovery_signal():
    """Observer's low-confidence prediction doesn't trigger recovery analysis."""
    from app.trajectory.types import (
        Trajectory, TrajectoryStep, STEP_PHASE_OBSERVER,
    )
    from app.trajectory.attribution import _should_analyze
    traj = Trajectory(
        trajectory_id="traj_low", task_id="t",
        crew_name="research", task_description="test",
        outcome_summary={
            "passed_quality_gate": True, "retries": 0,
            "duration_s": 5.0, "difficulty": 3,
        },
    )
    traj.append_step(TrajectoryStep(
        step_idx=-1, agent_role="research", phase=STEP_PHASE_OBSERVER,
        observer_prediction={"predicted_failure_mode": "fix_spiral", "confidence": 0.3},
    ))
    fire, reason = _should_analyze(traj)
    assert fire is False
    assert reason == "baseline"


# ── Safe-default tests ────────────────────────────────────────────────────


def test_maybe_analyze_returns_none_when_gate_off():
    """Baseline runs skip the LLM entirely."""
    from app.trajectory.attribution import maybe_analyze
    traj = _make_trajectory({
        "passed_quality_gate": True, "retries": 0,
        "duration_s": 5.0, "difficulty": 3,
    })
    result = maybe_analyze(traj)
    assert result is None


def test_analyze_returns_safe_default_when_crewai_missing(monkeypatch):
    """If crewai can't be imported, analyze returns a safe_default record (never raises)."""
    # Force the `from crewai import Task, Crew, Process` inside analyze() to fail
    # by masking the crewai module.
    with patch.dict(sys.modules, {"crewai": None}):
        from app.trajectory.attribution import analyze, VERDICT_BASELINE, FAILURE_MODE_NONE
        traj = _make_trajectory({
            "passed_quality_gate": False, "retries": 0,
            "duration_s": 5.0, "difficulty": 3,
        })
        result = analyze(traj)
        assert result is not None
        assert result.verdict == VERDICT_BASELINE
        assert result.failure_mode == FAILURE_MODE_NONE
        assert result.confidence == 0.0


# ── Field validation / coercion tests (mock the LLM) ────────────────────


def _mock_llm_returning(json_text: str):
    """Patch the crew.kickoff() path to return a specific raw string."""
    mock_crew_instance = MagicMock()
    mock_crew_instance.kickoff.return_value = json_text

    patches = [
        patch("app.trajectory.attribution._create_analyzer", return_value=MagicMock()),
    ]
    return patches, mock_crew_instance


def test_analyze_validates_verdict_coerces_invalid_to_baseline(monkeypatch, tmp_path):
    # Redirect persistence to tmp_path so tests don't scribble under /app.
    import app.trajectory.store as store_mod
    monkeypatch.setattr(store_mod, "_ROOT", tmp_path)

    raw = '{"verdict": "bogus", "failure_mode": "confidence_mirage", ' \
          '"attributed_step_idx": 0, "confidence": 0.9, ' \
          '"narrative": "n", "suggested_tip_type": "strategy"}'

    with patch("app.trajectory.attribution._create_analyzer", return_value=MagicMock()), \
         patch("crewai.Task"), \
         patch("crewai.Crew") as MockCrew, \
         patch("crewai.Process"):
        MockCrew.return_value.kickoff.return_value = raw
        from app.trajectory.attribution import analyze, VERDICT_BASELINE
        traj = _make_trajectory(
            {"passed_quality_gate": False, "difficulty": 3},
            [{"agent_role": "research", "planned_action": "did thing"}],
        )
        # Prevent gap emission from hitting ChromaDB
        with patch("app.trajectory.attribution._emit_gap"):
            record = analyze(traj)
    assert record is not None
    assert record.verdict == VERDICT_BASELINE  # coerced


def test_analyze_coerces_out_of_range_confidence(monkeypatch, tmp_path):
    import app.trajectory.store as store_mod
    monkeypatch.setattr(store_mod, "_ROOT", tmp_path)

    raw = '{"verdict": "failure", "failure_mode": "fix_spiral", ' \
          '"attributed_step_idx": 0, "confidence": 2.5, ' \
          '"narrative": "stuck in loop", "suggested_tip_type": "recovery"}'

    with patch("app.trajectory.attribution._create_analyzer", return_value=MagicMock()), \
         patch("crewai.Task"), \
         patch("crewai.Crew") as MockCrew, \
         patch("crewai.Process"):
        MockCrew.return_value.kickoff.return_value = raw
        from app.trajectory.attribution import analyze
        traj = _make_trajectory(
            {"passed_quality_gate": False, "difficulty": 3},
            [{"agent_role": "research", "planned_action": "did thing"}],
        )
        with patch("app.trajectory.attribution._emit_gap"):
            record = analyze(traj)
    assert record is not None
    assert record.confidence == 1.0  # clamped


def test_failure_verdict_plus_strategy_tip_coerced_to_recovery(monkeypatch, tmp_path):
    """If the model picks 'strategy' alongside 'failure', we correct to 'recovery'.

    Failure verdicts can't produce strategy tips — strategy = positive exemplar,
    but failure means we didn't do well. Coerce to recovery.
    """
    import app.trajectory.store as store_mod
    monkeypatch.setattr(store_mod, "_ROOT", tmp_path)

    raw = '{"verdict": "failure", "failure_mode": "fix_spiral", ' \
          '"attributed_step_idx": 0, "confidence": 0.7, ' \
          '"narrative": "stuck", "suggested_tip_type": "strategy"}'

    with patch("app.trajectory.attribution._create_analyzer", return_value=MagicMock()), \
         patch("crewai.Task"), \
         patch("crewai.Crew") as MockCrew, \
         patch("crewai.Process"):
        MockCrew.return_value.kickoff.return_value = raw
        from app.trajectory.attribution import analyze, TIP_RECOVERY
        traj = _make_trajectory(
            {"passed_quality_gate": False, "difficulty": 3},
            [{"agent_role": "research", "planned_action": "did thing"}],
        )
        with patch("app.trajectory.attribution._emit_gap"):
            record = analyze(traj)
    assert record is not None
    assert record.suggested_tip_type == TIP_RECOVERY


def test_baseline_verdict_skips_gap_emission(monkeypatch, tmp_path):
    """A baseline verdict must not produce a LearningGap."""
    import app.trajectory.store as store_mod
    monkeypatch.setattr(store_mod, "_ROOT", tmp_path)

    raw = '{"verdict": "baseline", "failure_mode": "none", ' \
          '"attributed_step_idx": -1, "confidence": 0.4, ' \
          '"narrative": "nothing distinctive", "suggested_tip_type": ""}'

    # emit_trajectory_attribution is lazily imported inside _emit_gap,
    # so patch it at its definition site (gap_detector module).
    with patch("app.trajectory.attribution._create_analyzer", return_value=MagicMock()), \
         patch("crewai.Task"), \
         patch("crewai.Crew") as MockCrew, \
         patch("crewai.Process"), \
         patch("app.self_improvement.gap_detector.emit_trajectory_attribution") as mock_emit:
        MockCrew.return_value.kickoff.return_value = raw
        from app.trajectory.attribution import analyze, VERDICT_BASELINE
        traj = _make_trajectory(
            {"passed_quality_gate": False, "difficulty": 3},
            [{"agent_role": "research", "planned_action": "did thing"}],
        )
        record = analyze(traj)
        assert record is not None
        assert record.verdict == VERDICT_BASELINE
        # Must not be called for baseline verdicts
        mock_emit.assert_not_called()


def test_gap_emitted_with_proper_fields(monkeypatch, tmp_path):
    """A real (non-baseline) verdict triggers emit_trajectory_attribution with
    the record's fields wired through correctly."""
    import app.trajectory.store as store_mod
    monkeypatch.setattr(store_mod, "_ROOT", tmp_path)

    raw = '{"verdict": "failure", "failure_mode": "fix_spiral", ' \
          '"attributed_step_idx": 0, "confidence": 0.8, ' \
          '"narrative": "retried same failing fix", ' \
          '"suggested_tip_type": "recovery"}'

    captured: dict = {}
    def fake_emit(**kwargs):
        captured.update(kwargs)
        return True

    with patch("app.trajectory.attribution._create_analyzer", return_value=MagicMock()), \
         patch("crewai.Task"), \
         patch("crewai.Crew") as MockCrew, \
         patch("crewai.Process"), \
         patch("app.self_improvement.gap_detector.emit_trajectory_attribution",
               side_effect=fake_emit):
        MockCrew.return_value.kickoff.return_value = raw
        from app.trajectory.attribution import analyze
        traj = _make_trajectory(
            {"passed_quality_gate": False, "difficulty": 3},
            [{"agent_role": "research", "planned_action": "did thing"}],
        )
        record = analyze(traj)

    assert record is not None
    assert captured.get("crew_name") == "research"
    assert captured.get("verdict") == "failure"
    assert captured.get("failure_mode") == "fix_spiral"
    assert captured.get("confidence") == 0.8
    assert captured.get("suggested_tip_type") == "recovery"
    assert captured.get("attribution_id") == record.attribution_id


def test_attribution_record_persisted_alongside_trajectory(monkeypatch, tmp_path):
    """Round-trip: persisted attribution is loadable by trajectory_id."""
    import app.trajectory.store as store_mod
    monkeypatch.setattr(store_mod, "_ROOT", tmp_path)

    raw = '{"verdict": "optimization", "failure_mode": "none", ' \
          '"attributed_step_idx": 1, "confidence": 0.6, ' \
          '"narrative": "could have cached", ' \
          '"suggested_tip_type": "optimization"}'

    with patch("app.trajectory.attribution._create_analyzer", return_value=MagicMock()), \
         patch("crewai.Task"), \
         patch("crewai.Crew") as MockCrew, \
         patch("crewai.Process"), \
         patch("app.self_improvement.gap_detector.emit_trajectory_attribution"):
        MockCrew.return_value.kickoff.return_value = raw
        from app.trajectory.attribution import analyze
        from app.trajectory.store import load_attribution
        from app.trajectory.types import (
            Trajectory, TrajectoryStep, STEP_PHASE_CREW,
        )
        # Build a trajectory with a valid `started_at` so persistence dir
        # resolves to the date-based path.
        traj = Trajectory(
            trajectory_id="traj_persist_test", task_id="t",
            crew_name="coding", task_description="test task",
            started_at="2026-04-19T10:00:00+00:00",
            outcome_summary={"passed_quality_gate": False},
        )
        traj.append_step(TrajectoryStep(
            step_idx=-1, agent_role="coding", phase=STEP_PHASE_CREW,
            planned_action="did thing",
        ))
        record = analyze(traj)

    assert record is not None
    # File should be on disk
    files = list(tmp_path.rglob("*.attribution.json"))
    assert len(files) == 1

    # Load it back
    loaded = load_attribution("traj_persist_test")
    assert loaded is not None
    assert loaded.attribution_id == record.attribution_id
    assert loaded.verdict == "optimization"
    assert loaded.suggested_tip_type == "optimization"
