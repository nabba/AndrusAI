"""Tests for app.trajectory.logger + store.

Verify:
  - Flag-off is a perfect no-op.
  - begin/capture/end round-trip produces a well-formed Trajectory.
  - Persistence writes a JSON sidecar that round-trips.
  - Reentrant begin_trajectory preserves the outer scope.
  - contextvars isolation: threads don't leak each other's trajectories.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _FakeSettings:
    def __init__(self, enabled: bool = True):
        self.trajectory_enabled = enabled
        self.attribution_enabled = False
        self.tip_synthesis_enabled = False
        self.task_conditional_retrieval_enabled = False
        self.observer_calibration_enabled = False


def _with_flag(enabled: bool, monkeypatch):
    import app.config as config_mod
    monkeypatch.setattr(config_mod, "get_settings", lambda: _FakeSettings(enabled))


def test_flag_off_is_noop(monkeypatch):
    _with_flag(False, monkeypatch)
    from app.trajectory.logger import (
        begin_trajectory, capture_step, end_trajectory, current_trajectory,
    )
    from app.trajectory.types import TrajectoryStep, STEP_PHASE_CREW

    assert begin_trajectory("t1", "research", "test task") is None
    assert current_trajectory() is None
    step = TrajectoryStep(
        step_idx=0, agent_role="research", phase=STEP_PHASE_CREW,
        planned_action="anything",
    )
    assert capture_step(step) is False
    assert end_trajectory() is None


def test_basic_roundtrip(monkeypatch):
    _with_flag(True, monkeypatch)
    # Reset context var between tests
    from app.trajectory import logger as tlog
    tlog._current_trajectory.set(None)

    from app.trajectory.logger import (
        begin_trajectory, capture_step, end_trajectory, current_trajectory,
    )
    from app.trajectory.types import (
        TrajectoryStep, STEP_PHASE_ROUTING, STEP_PHASE_CREW,
    )

    traj = begin_trajectory("task_42", "coding", "Implement foo")
    assert traj is not None
    assert traj.trajectory_id.startswith("traj_")
    assert traj.task_id == "task_42"
    assert traj.crew_name == "coding"
    assert current_trajectory() is traj

    assert capture_step(TrajectoryStep(
        step_idx=-1, agent_role="coding", phase=STEP_PHASE_ROUTING,
        planned_action="dispatch",
    )) is True
    assert capture_step(TrajectoryStep(
        step_idx=-1, agent_role="coding", phase=STEP_PHASE_CREW,
        planned_action="ran", output_sample="done",
    )) is True

    finalised = end_trajectory({"passed_quality_gate": True})
    assert finalised is traj
    assert len(finalised.steps) == 2
    assert finalised.steps[0].step_idx == 0
    assert finalised.steps[1].step_idx == 1
    assert finalised.ended_at
    assert finalised.outcome_summary["passed_quality_gate"] is True
    # After end, context is cleared
    assert current_trajectory() is None


def test_hash_is_filled_automatically(monkeypatch):
    _with_flag(True, monkeypatch)
    from app.trajectory import logger as tlog
    tlog._current_trajectory.set(None)

    from app.trajectory.logger import begin_trajectory, capture_step, end_trajectory
    from app.trajectory.types import TrajectoryStep, STEP_PHASE_CREW

    begin_trajectory("tid", "research", "topic")
    assert capture_step(TrajectoryStep(
        step_idx=-1, agent_role="research", phase=STEP_PHASE_CREW,
        tool_name="web_search", tool_args_sample="python tutorial",
        output_sample="lots of content here",
    )) is True
    traj = end_trajectory()
    assert traj is not None
    step = traj.steps[0]
    assert len(step.tool_args_hash) == 12
    assert len(step.output_hash) == 12
    # Hashes are deterministic
    import hashlib
    expect = hashlib.sha256(b"python tutorial").hexdigest()[:12]
    assert step.tool_args_hash == expect


def test_reentrant_begin_preserves_outer(monkeypatch):
    _with_flag(True, monkeypatch)
    from app.trajectory import logger as tlog
    tlog._current_trajectory.set(None)

    from app.trajectory.logger import begin_trajectory, current_trajectory

    outer = begin_trajectory("t1", "commander", "outer task")
    assert outer is not None

    inner = begin_trajectory("t2", "research", "inner task")
    assert inner is None
    # Outer is still active
    assert current_trajectory() is outer
    # Clean up
    from app.trajectory.logger import end_trajectory
    end_trajectory()


def test_thread_isolation(monkeypatch):
    _with_flag(True, monkeypatch)
    from app.trajectory import logger as tlog
    tlog._current_trajectory.set(None)

    import contextvars
    from app.trajectory.logger import (
        begin_trajectory, capture_step, end_trajectory, current_trajectory,
    )
    from app.trajectory.types import TrajectoryStep, STEP_PHASE_CREW

    results: dict[str, list] = {"a": [], "b": []}

    def worker(name: str):
        begin_trajectory(f"task_{name}", name, f"task {name}")
        capture_step(TrajectoryStep(
            step_idx=-1, agent_role=name, phase=STEP_PHASE_CREW,
            planned_action=f"did {name}",
        ))
        traj = end_trajectory()
        results[name].append(traj.trajectory_id if traj else None)

    # Each thread gets its own copy of the context — trajectories don't
    # bleed between them. Use Context.run so the ContextVar copy is taken.
    t1 = threading.Thread(target=lambda: contextvars.copy_context().run(worker, "a"))
    t2 = threading.Thread(target=lambda: contextvars.copy_context().run(worker, "b"))
    t1.start(); t2.start()
    t1.join();  t2.join()

    assert results["a"][0] is not None
    assert results["b"][0] is not None
    assert results["a"][0] != results["b"][0]


def test_persist_sidecar_roundtrip(monkeypatch, tmp_path):
    _with_flag(True, monkeypatch)
    from app.trajectory import logger as tlog
    tlog._current_trajectory.set(None)

    # Redirect the store's root to tmp_path so we don't write under /app.
    import app.trajectory.store as store_mod
    monkeypatch.setattr(store_mod, "_ROOT", tmp_path)

    from app.trajectory.logger import begin_trajectory, capture_step, end_trajectory
    from app.trajectory.store import persist_trajectory, load_trajectory
    from app.trajectory.types import TrajectoryStep, STEP_PHASE_CREW

    traj = begin_trajectory("task_xyz", "writing", "write a poem")
    capture_step(TrajectoryStep(
        step_idx=-1, agent_role="writing", phase=STEP_PHASE_CREW,
        planned_action="wrote", output_sample="poem here",
    ))
    finalised = end_trajectory({"passed_quality_gate": True})
    assert finalised is not None

    # Persist — should land on disk under a YYYY-MM-DD dir.
    # Chroma index upsert will no-op under test (chromadb stub).
    ok = persist_trajectory(finalised)
    assert ok is True

    # The sidecar file exists
    files = list(tmp_path.glob("*/traj_*.json"))
    assert len(files) == 1
    # And the content round-trips
    data = json.loads(files[0].read_text())
    assert data["trajectory_id"] == finalised.trajectory_id
    assert data["crew_name"] == "writing"
    assert len(data["steps"]) == 1

    # Re-hydrate via load_trajectory
    rehydrated = load_trajectory(finalised.trajectory_id)
    assert rehydrated is not None
    assert rehydrated.trajectory_id == finalised.trajectory_id
    assert rehydrated.crew_name == "writing"
    assert len(rehydrated.steps) == 1
    assert rehydrated.steps[0].phase == STEP_PHASE_CREW


def test_step_bounds_string_fields(monkeypatch):
    """TrajectoryStep.__post_init__ caps text fields to keep prompts bounded."""
    from app.trajectory.types import TrajectoryStep, STEP_PHASE_CREW

    long = "x" * 10_000
    step = TrajectoryStep(
        step_idx=0, agent_role="research", phase=STEP_PHASE_CREW,
        planned_action=long, tool_args_sample=long, output_sample=long,
        mcsv_snapshot=long, tool_name="a" * 200,
    )
    assert len(step.planned_action) == 400
    assert len(step.tool_args_sample) == 400
    assert len(step.output_sample) == 400
    assert len(step.mcsv_snapshot) == 400
    assert len(step.tool_name) == 100


def test_invalid_phase_coerced(monkeypatch):
    from app.trajectory.types import TrajectoryStep
    step = TrajectoryStep(step_idx=0, agent_role="research", phase="nonsense")
    assert step.phase == "unknown"
