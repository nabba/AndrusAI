"""Host-safe tests for app.research.run (Phase 3 — the research *run*).

Every external seam is injected (``search_fn`` / ``propose_fn`` /
``commander_fn`` / ``gate_fn``) so no LLM, crewai, or ChromaDB is loaded. The
autonomous-executor state machine is real (pure-stdlib dataclasses), so these
tests exercise the genuine ``plan → run → finalise`` path: the adapter
dispatches on ``research:*`` crew-hints, structured steps thread artifacts
through ``result_text`` JSON, and the evidence-gate escalation tips the run
into BLOCKED exactly the way the driver's blocker detection expects.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import app.research.run as R
from app.autonomous_executor.driver import CommanderResult
from app.autonomous_executor.models import (
    ExecutorRun,
    ExecutorStatus,
    ExecutorStep,
    StepStatus,
)


# ── Test doubles ────────────────────────────────────────────────────────────


@dataclass
class _Hit:
    """Duck-types app.research.literature.LiteratureHit."""

    id: str
    title: str = ""
    text: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "text": self.text}


@dataclass
class _Hyp:
    """Duck-types app.research.hypothesis.ResearchHypothesis."""

    text: str
    rank: int = 1
    grounded_in: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"text": self.text, "rank": self.rank, "grounded_in": list(self.grounded_in)}


def _make_seams(
    *,
    hits=(),
    hyps=(),
    gate=(None, ""),
    investigate_text: str = "INV-NOTES",
    draft_text: str = "DRAFT-BODY",
):
    """Build the four injectable seams plus a capture dict of what they saw."""
    cap: dict = {"search_goal": None, "propose_calls": [], "prompts": [], "gate_calls": []}

    def search_fn(goal):
        cap["search_goal"] = goal
        return list(hits)

    def propose_fn(question, *, literature=None, **kw):
        cap["propose_calls"].append({"question": question, "literature": literature, "kw": kw})
        return list(hyps)

    def commander_fn(step, run):
        cap["prompts"].append({"hint": step.crew_hint, "text": step.description})
        text = investigate_text if step.crew_hint == R.HINT_INVESTIGATE else draft_text
        return CommanderResult(text=text)

    def gate_fn(*, proposal_text, task_id, verdict):
        cap["gate_calls"].append({"proposal_text": proposal_text, "task_id": task_id, "verdict": verdict})
        return gate

    seams = dict(search_fn=search_fn, propose_fn=propose_fn, commander_fn=commander_fn, gate_fn=gate_fn)
    return seams, cap


def _step(run: ExecutorRun, hint: str) -> ExecutorStep:
    return next(s for s in run.plan if s.crew_hint == hint)


def _running_run(goal: str = "speed up retrieval") -> ExecutorRun:
    run = R.build_research_run(goal)
    run.transition(ExecutorStatus.RUNNING)
    return run


# ── Planner ───────────────────────────────────────────────────────────────


def test_plan_research_emits_five_steps_in_order():
    steps = R.plan_research("how to speed up retrieval")
    assert [s.crew_hint for s in steps] == [
        R.HINT_LITERATURE,
        R.HINT_HYPOTHESES,
        R.HINT_INVESTIGATE,
        R.HINT_DRAFT,
        R.HINT_GATE,
    ]
    assert "how to speed up retrieval" in steps[0].description
    assert steps[4].description == "Check the draft for uncited empirical claims"


def test_plan_research_empty_goal_raises():
    for bad in ("", "   "):
        try:
            R.plan_research(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")


def test_plan_research_truncates_long_goal():
    steps = R.plan_research("a" * 200)
    assert steps[0].description.endswith("...")
    assert "a" * 200 not in steps[0].description
    assert "a" * 117 + "..." in steps[0].description  # goal capped at 117 + ellipsis
    assert "a" * 118 not in steps[0].description


# ── build_research_run ───────────────────────────────────────────────────────


def test_build_research_run_planning_with_five_prepopulated_steps():
    run = R.build_research_run("does caching cut retrieval latency")
    assert run.status is ExecutorStatus.PLANNING
    assert run.zone == "autonomous"
    assert run.requestor == "research"
    assert run.run_id.startswith("research-")
    assert [s.crew_hint for s in run.plan] == [
        R.HINT_LITERATURE,
        R.HINT_HYPOTHESES,
        R.HINT_INVESTIGATE,
        R.HINT_DRAFT,
        R.HINT_GATE,
    ]


def test_build_research_run_empty_question_raises():
    for bad in ("", "   "):
        try:
            R.build_research_run(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")


def test_build_research_run_custom_zone_and_requestor():
    run = R.build_research_run("topic", requestor="operator:signal:42", zone="financial")
    assert run.requestor == "operator:signal:42"
    assert run.zone == "financial"


# ── End-to-end drive (clear gate → COMPLETED) ───────────────────────────────


def test_run_to_completion_clear_path():
    seams, cap = _make_seams(
        hits=[_Hit(id="kb1", title="Retrieval latency under load")],
        hyps=[_Hyp(text="Caching cuts retrieval latency", rank=1)],
        gate=(None, "looks grounded"),
    )
    run = R.build_research_run("how to speed up retrieval")
    R.run_to_completion(run, adapter=R.make_research_adapter(**seams))

    assert run.status is ExecutorStatus.COMPLETED
    assert all(s.status is StepStatus.COMPLETED for s in run.plan)
    assert cap["search_goal"] == "how to speed up retrieval"
    # the gate ran against the draft step's output
    assert cap["gate_calls"][0]["proposal_text"] == "DRAFT-BODY"
    assert cap["gate_calls"][0]["task_id"] == run.run_id
    gate_step = _step(run, R.HINT_GATE)
    assert gate_step.result_text.startswith("research-evidence gate: clear")


def test_literature_dicts_thread_into_hypotheses_step():
    seams, cap = _make_seams(
        hits=[_Hit(id="kb1", title="Retrieval latency under load")],
        hyps=[_Hyp(text="h1", rank=1)],
    )
    run = R.build_research_run("topic")
    R.run_to_completion(run, adapter=R.make_research_adapter(**seams))
    # the literature step's JSON fed straight back into propose_fn
    assert cap["propose_calls"][0]["literature"] == [
        {"id": "kb1", "title": "Retrieval latency under load", "text": ""}
    ]


def test_run_records_artifact_count_notes():
    seams, _ = _make_seams(
        hits=[_Hit(id="a", title="t"), _Hit(id="b", title="t2")],
        hyps=[_Hyp(text="h1"), _Hyp(text="h2"), _Hyp(text="h3")],
    )
    run = R.build_research_run("topic")
    R.run_to_completion(run, adapter=R.make_research_adapter(**seams))
    joined = "\n".join(run.notes)
    assert "literature: 2 hit(s)" in joined
    assert "hypotheses: 3 proposed" in joined


def test_run_to_completion_drives_planner_from_created():
    run = ExecutorRun(run_id="r-created", goal="topic", requestor="research", zone="autonomous")
    seams, _ = _make_seams(hits=[_Hit(id="k", title="t")], hyps=[_Hyp(text="h", rank=1)])
    R.run_to_completion(run, adapter=R.make_research_adapter(**seams), planner_fn=R.plan_research)
    assert run.status is ExecutorStatus.COMPLETED
    assert [s.crew_hint for s in run.plan] == [
        R.HINT_LITERATURE,
        R.HINT_HYPOTHESES,
        R.HINT_INVESTIGATE,
        R.HINT_DRAFT,
        R.HINT_GATE,
    ]


def test_run_to_completion_respects_max_iterations():
    seams, _ = _make_seams(hits=[_Hit(id="k", title="t")], hyps=[_Hyp(text="h", rank=1)])
    run = R.build_research_run("topic")
    # planning tick + one step only — not enough to finish.
    R.run_to_completion(run, adapter=R.make_research_adapter(**seams), max_iterations=2)
    assert not run.is_terminal
    assert run.status is ExecutorStatus.RUNNING


# ── Gate escalation → BLOCKED ────────────────────────────────────────────────


def test_gate_escalation_blocks_run():
    seams, _ = _make_seams(
        hits=[_Hit(id="kb1", title="t")],
        hyps=[_Hyp(text="h1", rank=1)],
        gate=("peer_review", "2 uncited empirical claims"),
    )
    run = R.build_research_run("topic")
    R.run_to_completion(run, adapter=R.make_research_adapter(**seams))

    assert run.status is ExecutorStatus.BLOCKED
    assert "peer_review" in run.blocked_reason
    gate_step = _step(run, R.HINT_GATE)
    # the step itself completed (commander returned a valid result); it is the
    # BLOCKED: marker in its text that tips the *run* into BLOCKED.
    assert gate_step.status is StepStatus.COMPLETED
    assert gate_step.result_text.startswith("BLOCKED:")


def test_gate_verify_escalation_blocks_run():
    seams, _ = _make_seams(
        hits=[_Hit(id="kb1", title="t")],
        hyps=[_Hyp(text="h1")],
        gate=("verify", "needs a citation"),
    )
    run = R.build_research_run("topic")
    R.run_to_completion(run, adapter=R.make_research_adapter(**seams))
    assert run.status is ExecutorStatus.BLOCKED
    assert R.summarise_run(run).gate_action == "verify"


def test_gate_exception_is_isolated_and_run_completes():
    def boom_gate(**_kw):
        raise RuntimeError("gate subsystem down")

    seams, _ = _make_seams(hits=[_Hit(id="k", title="t")], hyps=[_Hyp(text="h")])
    seams["gate_fn"] = boom_gate
    run = R.build_research_run("topic")
    R.run_to_completion(run, adapter=R.make_research_adapter(**seams))
    assert run.status is ExecutorStatus.COMPLETED
    assert _step(run, R.HINT_GATE).result_text == "research-evidence gate: unavailable"


# ── Adapter dispatch (direct calls) ──────────────────────────────────────────


def test_literature_hits_without_to_dict_supported():
    seams, _ = _make_seams(
        hits=[{"id": "d1", "title": "plain dict hit", "text": ""}],
        hyps=[_Hyp(text="h")],
    )
    run = R.build_research_run("topic")
    R.run_to_completion(run, adapter=R.make_research_adapter(**seams))
    assert json.loads(_step(run, R.HINT_LITERATURE).result_text) == [
        {"id": "d1", "title": "plain dict hit", "text": ""}
    ]


def test_search_returning_none_yields_empty_literature():
    def none_search(_goal):
        return None

    seams, _ = _make_seams(hyps=[_Hyp(text="h")])
    seams["search_fn"] = none_search
    run = R.build_research_run("topic")
    R.run_to_completion(run, adapter=R.make_research_adapter(**seams))
    assert json.loads(_step(run, R.HINT_LITERATURE).result_text) == []


def test_unknown_hint_delegates_to_commander():
    seen: list = []

    def commander_fn(step, run):
        seen.append(step.crew_hint)
        return CommanderResult(text="plain commander output")

    adapter = R.make_research_adapter(
        search_fn=lambda g: [],
        propose_fn=lambda q, **k: [],
        commander_fn=commander_fn,
        gate_fn=lambda **k: (None, ""),
    )
    run = ExecutorRun(run_id="t", goal="g", requestor="x")
    out = adapter(ExecutorStep(step_id="s1", description="do a thing", crew_hint="some:other"), run)
    assert out.text == "plain commander output"
    assert seen == ["some:other"]


def test_research_adapter_nests_over_inner_adapter():
    """Strict-superset contract the scheduler relies on: when given an inner
    ``commander_fn``, the research adapter handles every research:* hint itself
    (never touching the inner adapter) and falls through to the inner adapter
    for everything else. This is what lets the scheduler nest
    ``make_research_adapter(commander_fn=make_self_improvement_adapter())``
    without any per-run routing."""
    inner_seen: list = []

    def inner(step, run):
        inner_seen.append(step.crew_hint)
        return CommanderResult(text=f"inner:{step.crew_hint}")

    adapter = R.make_research_adapter(
        search_fn=lambda g: [{"id": "k1", "title": "t"}],
        propose_fn=lambda q, **k: [],
        commander_fn=inner,
        gate_fn=lambda **k: (None, ""),
    )
    run = ExecutorRun(run_id="t", goal="g", requestor="x")

    # A research:* hint is handled by the research adapter itself — inner untouched.
    lit = adapter(
        ExecutorStep(step_id="s1", description="", crew_hint=R.HINT_LITERATURE), run
    )
    assert json.loads(lit.text) == [{"id": "k1", "title": "t"}]
    assert inner_seen == []

    # A non-research hint falls straight through to the inner adapter.
    other = adapter(
        ExecutorStep(step_id="s2", description="", crew_hint="self_improvement:job"),
        run,
    )
    assert other.text == "inner:self_improvement:job"
    assert inner_seen == ["self_improvement:job"]


def test_gate_falls_back_to_investigation_when_no_draft():
    captured: dict = {}

    def gate_fn(*, proposal_text, task_id, verdict):
        captured["proposal_text"] = proposal_text
        return (None, "")

    run = _running_run("topic")
    inv = _step(run, R.HINT_INVESTIGATE)
    inv.status = StepStatus.COMPLETED
    inv.result_text = "investigation findings"

    adapter = R.make_research_adapter(
        search_fn=lambda g: [],
        propose_fn=lambda q, **k: [],
        commander_fn=lambda s, r: CommanderResult(text=""),
        gate_fn=gate_fn,
    )
    adapter(_step(run, R.HINT_GATE), run)
    assert captured["proposal_text"] == "investigation findings"


# ── Prompt builders ──────────────────────────────────────────────────────────


def test_investigate_prompt_folds_hypothesis_and_literature():
    run = _running_run("speed up retrieval")
    lit = _step(run, R.HINT_LITERATURE)
    lit.status = StepStatus.COMPLETED
    lit.result_text = json.dumps([{"id": "kb1", "title": "Retrieval latency under load", "text": ""}])
    hyp = _step(run, R.HINT_HYPOTHESES)
    hyp.status = StepStatus.COMPLETED
    hyp.result_text = json.dumps([{"text": "Caching cuts latency", "rank": 1}])

    prompt = R._build_investigate_prompt(run)
    assert "speed up retrieval" in prompt
    assert "Caching cuts latency" in prompt
    assert "Retrieval latency under load" in prompt


def test_draft_prompt_folds_investigation_and_asks_for_citations():
    run = _running_run("topic")
    inv = _step(run, R.HINT_INVESTIGATE)
    inv.status = StepStatus.COMPLETED
    inv.result_text = "caching reduced p99 by 40%"

    prompt = R._build_draft_prompt(run)
    assert "caching reduced p99 by 40%" in prompt
    assert "topic" in prompt
    assert "attribute it to its source" in prompt


# ── summarise_run + ResearchRunOutcome ───────────────────────────────────────


def test_summarise_run_extracts_artifacts():
    seams, _ = _make_seams(
        hits=[_Hit(id="a", title="t1"), _Hit(id="b", title="t2")],
        hyps=[_Hyp(text="leading hypothesis", rank=1), _Hyp(text="alt", rank=2)],
        gate=(None, "grounded"),
    )
    run = R.build_research_run("the question")
    R.run_to_completion(run, adapter=R.make_research_adapter(**seams))

    out = R.summarise_run(run)
    assert out.question == "the question"
    assert out.status == "completed"
    assert out.n_literature == 2
    assert out.n_hypotheses == 2
    assert out.top_hypothesis == "leading hypothesis"
    assert out.draft == "DRAFT-BODY"
    assert out.gate_action is None
    assert "clear" in out.gate_note


def test_summarise_empty_run():
    run = R.build_research_run("topic")  # PLANNING — nothing completed yet
    out = R.summarise_run(run)
    assert out.n_literature == 0
    assert out.n_hypotheses == 0
    assert out.top_hypothesis is None
    assert out.draft == ""
    assert out.gate_action is None
    assert out.status == "planning"


def test_outcome_to_dict_shape():
    out = R.ResearchRunOutcome(question="q", status="completed")
    d = out.to_dict()
    assert set(d) == {
        "question",
        "status",
        "n_literature",
        "n_hypotheses",
        "top_hypothesis",
        "draft",
        "gate_action",
        "gate_note",
    }


# ── Package wiring ────────────────────────────────────────────────────────────


def test_module_listed_in_research_package():
    import app.research as research

    assert "run" in research.__all__


def test_module_exports():
    assert set(R.__all__) == {
        "HINT_LITERATURE",
        "HINT_HYPOTHESES",
        "HINT_INVESTIGATE",
        "HINT_DRAFT",
        "HINT_GATE",
        "HINT_SYNTHESIZE",
        "ResearchRunOutcome",
        "plan_research",
        "make_research_adapter",
        "build_research_run",
        "run_to_completion",
        "summarise_run",
    }
