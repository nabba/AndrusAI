"""
app.trajectory — Trajectory-informed memory (arXiv:2603.10600).

Four infrastructure components implement the paper's pipeline:

    1. types         — TrajectoryStep, Trajectory, AttributionRecord
    2. logger        — per-crew capture (contextvars-scoped, thread-safe)
    3. store         — JSON sidecar + compact Chroma index for replay & lookup
    4. attribution   — post-hoc AttributionAnalyzer (mirror of the Observer)
    5. tip_builder   — attribution → CrewAI Task for trajectory-sourced learning
    6. calibration   — Observer ↔ Attribution precision/recall tracker

The package is deliberately isolated from `app.agents/` and
`app.self_improvement/` so the Self-Improver cannot modify attribution
logic (preserves the CLAUDE.md safety invariant — evaluation functions
live at infrastructure level).

All behaviour is flag-gated (`settings.trajectory_enabled` and siblings).
With all flags off, importing this module has no effect on existing
execution paths.

IMMUTABLE — infrastructure-level package.
"""

from app.trajectory.types import (
    TrajectoryStep, Trajectory, AttributionRecord,
    STEP_PHASE_ROUTING, STEP_PHASE_OBSERVER, STEP_PHASE_CREW,
    STEP_PHASE_REFLEXION, STEP_PHASE_QUALITY,
    VERDICT_FAILURE, VERDICT_RECOVERY, VERDICT_OPTIMIZATION, VERDICT_BASELINE,
    TIP_STRATEGY, TIP_RECOVERY, TIP_OPTIMIZATION,
    FAILURE_MODE_NONE,
)
from app.trajectory.logger import (
    begin_trajectory, capture_step, capture_observer_prediction,
    end_trajectory, on_crew_complete, note_injected_skills,
)
from app.trajectory.store import (
    persist_trajectory, load_trajectory, list_recent_trajectories,
    persist_attribution, load_attribution,
)
from app.trajectory.attribution import (
    analyze, maybe_analyze,
)
from app.trajectory.tip_builder import (
    build_tip_prompt, build_tip_topic, build_tip_task, build_draft,
)
from app.trajectory.context_builder import compose_trajectory_hint_block
from app.trajectory.calibration import (
    record_calibration, precision_recall_report,
)
from app.trajectory.effectiveness import (
    record_use, effectiveness_report, top_tips, worst_tips,
    MIN_USES_FOR_ACTION,
)
from app.trajectory.replay import replay, format_text

__all__ = [
    # types
    "TrajectoryStep", "Trajectory", "AttributionRecord",
    "STEP_PHASE_ROUTING", "STEP_PHASE_OBSERVER", "STEP_PHASE_CREW",
    "STEP_PHASE_REFLEXION", "STEP_PHASE_QUALITY",
    "VERDICT_FAILURE", "VERDICT_RECOVERY", "VERDICT_OPTIMIZATION", "VERDICT_BASELINE",
    "TIP_STRATEGY", "TIP_RECOVERY", "TIP_OPTIMIZATION",
    "FAILURE_MODE_NONE",
    # logger
    "begin_trajectory", "capture_step", "capture_observer_prediction",
    "end_trajectory", "on_crew_complete", "note_injected_skills",
    # store
    "persist_trajectory", "load_trajectory", "list_recent_trajectories",
    "persist_attribution", "load_attribution",
    # attribution
    "analyze", "maybe_analyze",
    # tip_builder
    "build_tip_prompt", "build_tip_topic", "build_tip_task", "build_draft",
    # context_builder
    "compose_trajectory_hint_block",
    # calibration
    "record_calibration", "precision_recall_report",
    # effectiveness (Phase 6)
    "record_use", "effectiveness_report", "top_tips", "worst_tips",
    "MIN_USES_FOR_ACTION",
    # replay (Phase 6)
    "replay", "format_text",
]
