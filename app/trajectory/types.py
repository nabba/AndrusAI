"""
app.trajectory.types — typed records for trajectory-informed memory.

Single source of truth for the structured data flowing through the
post-crew attribution pipeline (arXiv:2603.10600):

    TrajectoryStep    — one observable decision/event inside a crew execution
    Trajectory        — the ordered sequence for a single top-level task
    AttributionRecord — AttributionAnalyzer's verdict on a finished trajectory

All records are JSON-serialisable and carry provenance: every downstream
artifact (SkillDraft, SkillRecord, LearningGap) can be traced back to a
specific trajectory_id + step_idx. This is the auditability contract that
lets an operator bulk-archive every skill derived from a suspect run.

Step size is deliberately commander-level: one step per top-level decision
(routing, observer prediction, crew dispatch, reflexion retry, quality
gate) rather than per-tool-call. Commander-level granularity is where the
multi-agent decisions live — and it keeps capture overhead negligible.

IMMUTABLE — infrastructure-level types.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


# ── Step-phase enum (string constants for stable serialisation) ─────────────

STEP_PHASE_ROUTING    = "routing"      # commander picked this crew
STEP_PHASE_OBSERVER   = "observer"     # Observer ran a pre-action prediction
STEP_PHASE_CREW       = "crew"         # a crew executed (with timing/result)
STEP_PHASE_REFLEXION  = "reflexion"    # quality gate failed, trial N retry
STEP_PHASE_QUALITY    = "quality"      # post-hoc quality evaluation

_VALID_PHASES = {
    STEP_PHASE_ROUTING, STEP_PHASE_OBSERVER, STEP_PHASE_CREW,
    STEP_PHASE_REFLEXION, STEP_PHASE_QUALITY,
}

# ── Attribution verdict + tip taxonomy ──────────────────────────────────────

VERDICT_FAILURE       = "failure"        # run failed and we identified the cause
VERDICT_RECOVERY      = "recovery"       # run succeeded despite predicted failure
VERDICT_OPTIMIZATION  = "optimization"   # run succeeded but inefficiently
VERDICT_BASELINE      = "baseline"       # ordinary success — nothing distinctive

TIP_STRATEGY          = "strategy"       # re-usable positive exemplar
TIP_RECOVERY          = "recovery"       # how to escape a known failure mode
TIP_OPTIMIZATION      = "optimization"   # a faster/cheaper path to success

FAILURE_MODE_NONE     = "none"           # attributable cause but no named failure


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Records ─────────────────────────────────────────────────────────────────

@dataclass
class TrajectoryStep:
    """A single observable event inside a crew execution.

    All text fields are bounded (planned_action, tool_args_sample,
    output_sample ≤ 400 chars) — full payloads are represented by their
    sha256 hashes so the attribution prompt stays bounded regardless of
    upstream verbosity. This is essential for predictable LLM cost and
    also limits prompt-injection surface from captured tool output.
    """

    step_idx: int
    agent_role: str                    # crew_name ("research", "coding", …)
    phase: str                         # one of STEP_PHASE_*
    planned_action: str = ""           # ≤ 400 chars
    tool_name: str = ""                # "" for non-tool phases
    tool_args_sample: str = ""         # ≤ 400 chars
    tool_args_hash: str = ""
    output_sample: str = ""            # ≤ 400 chars
    output_hash: str = ""
    # Observer prediction payload if this step corresponds to an Observer
    # firing, or if the Observer fired at the preceding routing step and
    # this step is the one being predicted about.
    observer_prediction: dict = field(default_factory=dict)
    observer_recommendation_followed: bool = False
    elapsed_ms: int = 0
    tokens_prompt: int = 0
    tokens_completion: int = 0
    mcsv_snapshot: str = ""            # compact MCSV.to_context_string() slice
    started_at: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        # Normalise / bound string fields — belt-and-braces so callers who
        # hand over raw user data don't blow past the prompt budget later.
        if self.phase not in _VALID_PHASES:
            # Don't raise — trajectory capture is best-effort. Coerce to
            # a safe marker so downstream consumers can filter.
            self.phase = "unknown"
        self.planned_action = (self.planned_action or "")[:400]
        self.tool_name = (self.tool_name or "")[:100]
        self.tool_args_sample = (self.tool_args_sample or "")[:400]
        self.output_sample = (self.output_sample or "")[:400]
        self.mcsv_snapshot = (self.mcsv_snapshot or "")[:400]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TrajectoryStep":
        valid = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


@dataclass
class Trajectory:
    """The ordered sequence of steps for a single top-level task.

    `task_id` ties back to the existing conversation_store record so the
    trajectory can be joined against Firebase telemetry, benchmarks, etc.
    `crew_name` is the top-level crew the commander dispatched to; sub-crew
    calls appear as additional CREW steps inside the same Trajectory.

    `injected_skill_ids` (Phase 6) lists the SkillRecord ids surfaced by
    the context_builder for this run. At attribution time we correlate
    these ids with the outcome to compute per-tip effectiveness, which
    the Evaluator then uses to decide archival.
    """

    trajectory_id: str
    task_id: str
    crew_name: str
    task_description: str
    steps: list[TrajectoryStep] = field(default_factory=list)
    started_at: str = field(default_factory=_now_iso)
    ended_at: str = ""

    # Post-crew outcome summary — populated by on_crew_complete(). Lets the
    # Attribution Analyzer see the outcome without re-constructing CrewOutcome.
    outcome_summary: dict = field(default_factory=dict)

    # Phase 6: SkillRecord ids injected by the context_builder. Empty list
    # when task-conditional retrieval didn't fire or surfaced nothing.
    injected_skill_ids: list[str] = field(default_factory=list)

    def append_step(self, step: TrajectoryStep) -> None:
        # Enforce monotonic step_idx — if caller didn't set one, assign.
        if step.step_idx < 0:
            step.step_idx = len(self.steps)
        self.steps.append(step)

    def to_dict(self) -> dict:
        return {
            "trajectory_id": self.trajectory_id,
            "task_id": self.task_id,
            "crew_name": self.crew_name,
            "task_description": self.task_description[:2000],
            "steps": [s.to_dict() for s in self.steps],
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "outcome_summary": self.outcome_summary,
            "injected_skill_ids": list(self.injected_skill_ids),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Trajectory":
        steps = [TrajectoryStep.from_dict(s) for s in d.get("steps", [])]
        return cls(
            trajectory_id=d.get("trajectory_id", ""),
            task_id=d.get("task_id", ""),
            crew_name=d.get("crew_name", ""),
            task_description=d.get("task_description", ""),
            steps=steps,
            started_at=d.get("started_at", ""),
            ended_at=d.get("ended_at", ""),
            outcome_summary=d.get("outcome_summary", {}),
            injected_skill_ids=list(d.get("injected_skill_ids", []) or []),
        )


@dataclass
class AttributionRecord:
    """AttributionAnalyzer's verdict on a finished trajectory.

    `verdict` is the coarse-grained category; `failure_mode` reuses the
    Observer's taxonomy verbatim (same namespace — essential for the
    calibration loop in Phase 5). `attributed_step_idx` points back into
    `Trajectory.steps`, making every tip traceable to a specific decision.
    """

    attribution_id: str
    trajectory_id: str
    verdict: str                   # VERDICT_*
    failure_mode: str              # Observer taxonomy ∪ {FAILURE_MODE_NONE}
    attributed_step_idx: int
    confidence: float              # 0.0–1.0
    narrative: str = ""            # ≤ 400 chars — the "why" for the Learner
    suggested_tip_type: str = ""   # TIP_* (empty for VERDICT_BASELINE)
    created_at: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        self.narrative = (self.narrative or "")[:400]
        try:
            self.confidence = max(0.0, min(1.0, float(self.confidence)))
        except (TypeError, ValueError):
            self.confidence = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AttributionRecord":
        valid = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**valid)
