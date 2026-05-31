"""app.research.run — the auto-research run (Phase 3).

Phases 1 and 2 built the two pure research steps:

  * ``literature.search_literature`` (Phase 1) — KB + arXiv retrieval.
  * ``hypothesis.propose_hypotheses`` (Phase 2) — grounded ideation.

Phase 3 composes them into a *run*. The roadmap (``app.research.__init__``)
fixes the shape: "the research run is an ``autonomous_executor`` ExecutorRun
whose steps carry research crew-hints." That is exactly what this module
produces — it owns no new infrastructure. The executor already supplies the
state machine, the per-run budget, the hash-chained audit, and the BLOCKED
escalation; ``ExecutorStep.crew_hint`` already exists *so a step can override
Commander's routing*. Phase 3 just teaches the system what the research
crew-hints mean.

The five-step chain, each step tagged with a ``research:*`` crew-hint::

    literature  → search_literature(question)            (Phase 1)
    hypotheses  → propose_hypotheses(question, lit=…)     (Phase 2)
    investigate → Commander dispatch (leading hypothesis)
    draft       → Commander dispatch (write up findings)
    gate        → gate_research_evidence.evaluate(draft)  (Phase 1)

Two pieces do the work:

  * :func:`plan_research` — a ``PlannerFn`` that emits the five steps.
  * :func:`make_research_adapter` — a ``CommanderFn`` that dispatches on
    ``step.crew_hint``. The structured steps (literature, hypotheses, gate)
    call the Phase 1/Phase 2 functions directly; the prose steps
    (investigate, draft) delegate to the real Commander adapter. Any
    unrecognised hint falls through to Commander, so the adapter is a strict
    superset of the plain commander adapter — wiring it into the scheduler
    would be behaviour-preserving for every non-research run.

Cross-step state rides on the steps themselves: a structured step stores its
output as JSON in ``result_text`` and the next step decodes it. This survives
persistence between scheduler ticks (the artifacts are part of the saved run)
and needs no in-memory closure state — and because
``propose_hypotheses`` already accepts plain dicts for ``literature``, the
literature step's JSON feeds straight back in.

Every seam is injectable (``search_fn`` / ``propose_fn`` / ``commander_fn`` /
``gate_fn``), defaulting to the real subsystem resolved lazily, so the whole
chain is exercisable on a host with no LLM / ChromaDB / crewai. Module load is
pure stdlib + the lightweight executor types.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from app.autonomous_executor.driver import CommanderFn, CommanderResult, advance_one_step
from app.autonomous_executor.models import (
    Budget,
    ExecutorRun,
    ExecutorStatus,
    ExecutorStep,
)

logger = logging.getLogger(__name__)


# ── Crew-hints ───────────────────────────────────────────────────────────────
# The five research crew-hints. The ``research:`` namespace keeps them from
# colliding with any future non-research hint the scheduler might route on.
HINT_LITERATURE = "research:literature"
HINT_HYPOTHESES = "research:hypotheses"
HINT_INVESTIGATE = "research:investigate"
HINT_DRAFT = "research:draft"
HINT_GATE = "research:gate"
# Optional sixth step (off by default). Renders the run's artifacts into a
# ResearchDossier PDF via ``app.research.dossier``. Kept out of the default
# plan so the five-step Phase-3 contract is unchanged; callers opt in with
# ``synthesize=True``. The primary dossier surface is the on-demand endpoint
# (works in any run state) — this step just bakes a PDF into the run itself.
HINT_SYNTHESIZE = "research:synthesize"

# How much prior context to fold into the prose-step prompts.
_MAX_LIT_FOR_PROMPT = 6
_MAX_HYP_FOR_PROMPT = 4

# Gate actions worth surfacing (substring-matched out of the gate step text).
# Longest first so the check is unambiguous.
_GATE_ACTIONS = ("peer_review", "verify")


# ── Result type ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ResearchRunOutcome:
    """A typed view of a finished research run's *research* artifacts.

    The :class:`ExecutorRun` is the source of truth; this pulls the
    research-meaningful fields out of its step results so callers (operator
    surfaces, tests) don't have to know the JSON-in-``result_text``
    convention. Defaults are conservative so a run that failed or blocked
    part-way still summarises cleanly.
    """

    question: str
    status: str
    n_literature: int = 0
    n_hypotheses: int = 0
    top_hypothesis: Optional[str] = None
    draft: str = ""
    gate_action: Optional[str] = None
    gate_note: str = ""

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "status": self.status,
            "n_literature": self.n_literature,
            "n_hypotheses": self.n_hypotheses,
            "top_hypothesis": self.top_hypothesis,
            "draft": self.draft,
            "gate_action": self.gate_action,
            "gate_note": self.gate_note,
        }


# ── Planner ──────────────────────────────────────────────────────────────────


def plan_research(
    goal: str,
    run: "ExecutorRun | None" = None,
    *,
    synthesize: bool = False,
) -> list[ExecutorStep]:
    """Return the research steps, each carrying a ``research:*`` hint.

    Matches the ``PlannerFn`` signature ``(goal, run) -> list[ExecutorStep]``
    so it can be injected straight into the driver. Only ``description`` and
    ``crew_hint`` survive (the driver re-creates each step via
    :meth:`ExecutorRun.add_step`), so those are the only fields populated.
    Raises ``ValueError`` on an empty goal — the driver catches it and marks
    the run FAILED with that reason (mirrors the default planner).

    By default this is the five-step chain (literature → hypotheses →
    investigate → draft → gate). With ``synthesize=True`` a sixth
    :data:`HINT_SYNTHESIZE` step is appended that bakes a ResearchDossier PDF
    into the run; the default stays five so the Phase-3 contract is unchanged.
    """
    q = (goal or "").strip()
    if not q:
        raise ValueError("research plan requires a non-empty goal")
    short = q if len(q) <= 120 else q[:117] + "..."
    steps = [
        ExecutorStep(
            step_id="",
            description=f"Search the literature for: {short}",
            crew_hint=HINT_LITERATURE,
        ),
        ExecutorStep(
            step_id="",
            description=f"Propose grounded hypotheses for: {short}",
            crew_hint=HINT_HYPOTHESES,
        ),
        ExecutorStep(
            step_id="",
            description=f"Investigate the leading hypothesis for: {short}",
            crew_hint=HINT_INVESTIGATE,
        ),
        ExecutorStep(
            step_id="",
            description=f"Draft research findings for: {short}",
            crew_hint=HINT_DRAFT,
        ),
        ExecutorStep(
            step_id="",
            description="Check the draft for uncited empirical claims",
            crew_hint=HINT_GATE,
        ),
    ]
    if synthesize:
        steps.append(
            ExecutorStep(
                step_id="",
                description="Synthesize a research dossier PDF from the run's artifacts",
                crew_hint=HINT_SYNTHESIZE,
            )
        )
    return steps


# ── Cross-step artifact threading ─────────────────────────────────────────────


def _encode(payload) -> str:
    try:
        return json.dumps(payload, default=str)
    except Exception:
        logger.debug("research.run: encode failed", exc_info=True)
        return "[]"


def _completed_step(run: ExecutorRun, hint: str) -> Optional[ExecutorStep]:
    """Last COMPLETED step carrying ``hint`` (None when none completed)."""
    found: Optional[ExecutorStep] = None
    for step in run.plan:
        if step.crew_hint == hint and step.status.value == "completed":
            found = step
    return found


def _decode_list(run: ExecutorRun, hint: str) -> list[dict]:
    """Decode a structured step's JSON ``result_text`` into a list of dicts."""
    step = _completed_step(run, hint)
    if step is None:
        return []
    try:
        data = json.loads(step.result_text or "[]")
        return data if isinstance(data, list) else []
    except Exception:
        logger.debug("research.run: decode failed for %s", hint, exc_info=True)
        return []


def _text_for(run: ExecutorRun, hint: str) -> str:
    """Raw ``result_text`` of the last completed step with ``hint``."""
    step = _completed_step(run, hint)
    return (step.result_text or "") if step is not None else ""


# ── Prompt builders (prose steps) ─────────────────────────────────────────────


def _lit_titles(run: ExecutorRun, *, limit: int = _MAX_LIT_FOR_PROMPT) -> list[str]:
    titles: list[str] = []
    for hit in _decode_list(run, HINT_LITERATURE)[:limit]:
        title = str(hit.get("title") or hit.get("text") or "").strip()
        if title:
            titles.append(title[:200])
    return titles


def _hypotheses(run: ExecutorRun, *, limit: int = _MAX_HYP_FOR_PROMPT) -> list[str]:
    out: list[str] = []
    for hyp in _decode_list(run, HINT_HYPOTHESES)[:limit]:
        text = str(hyp.get("text") or "").strip()
        if text:
            out.append(text)
    return out


def _build_investigate_prompt(run: ExecutorRun) -> str:
    hyps = _hypotheses(run)
    titles = _lit_titles(run)
    parts = [
        "Investigate the research question below and report concrete "
        "findings, the methods you relied on, and the evidence behind each "
        "claim. Prefer specific, checkable statements over generalities.",
        f"\nResearch question: {run.goal}",
    ]
    if hyps:
        parts.append("\nLeading hypothesis to focus on:\n- " + hyps[0])
        if len(hyps) > 1:
            parts.append(
                "Alternative hypotheses to keep in mind:\n"
                + "\n".join(f"- {h}" for h in hyps[1:])
            )
    if titles:
        parts.append("Prior literature already retrieved:\n" + "\n".join(f"- {t}" for t in titles))
    return "\n".join(parts)


def _build_draft_prompt(run: ExecutorRun) -> str:
    investigation = _text_for(run, HINT_INVESTIGATE).strip()
    hyps = _hypotheses(run)
    parts = [
        "Write a concise research-findings draft for the question below. "
        "State results concretely; whenever you make an empirical or "
        "quantitative claim, attribute it to its source (author, link, or "
        "arXiv id) so the finding is citable.",
        f"\nResearch question: {run.goal}",
    ]
    if investigation:
        parts.append("\nInvestigation notes:\n" + investigation[:4000])
    if hyps:
        parts.append("Hypotheses considered:\n" + "\n".join(f"- {h}" for h in hyps))
    return "\n".join(parts)


# ── Adapter ────────────────────────────────────────────────────────────────


def make_research_adapter(
    *,
    search_fn: Optional[Callable] = None,
    propose_fn: Optional[Callable] = None,
    commander_fn: Optional[CommanderFn] = None,
    gate_fn: Optional[Callable] = None,
) -> CommanderFn:
    """Build a ``CommanderFn`` that executes research steps by crew-hint.

    The four seams default to the real subsystems, resolved lazily so
    constructing the adapter is cheap and host-safe:

      * ``search_fn``    → ``app.research.literature.search_literature``
      * ``propose_fn``   → ``app.research.hypothesis.propose_hypotheses``
      * ``commander_fn`` → ``commander_adapter.make_commander_adapter()``
      * ``gate_fn``      → ``epistemic.gate_research_evidence.evaluate``

    Dispatch by ``step.crew_hint``:

      * literature  — search; store hits as JSON in ``result_text``.
      * hypotheses  — propose grounded in the literature step's hits; store
        hypotheses as JSON.
      * investigate / draft — build a prompt from prior artifacts and
        delegate to ``commander_fn`` (text in, text out; cost carried
        through unchanged).
      * gate — evaluate the draft; when the evidence gate escalates, the
        result text starts with ``BLOCKED:`` so the driver tips the run
        into BLOCKED for operator review (the executor's existing
        escalation path).
      * synthesize — render a ResearchDossier PDF from the run's artifacts
        (failure-isolated — needs gateway-only PDF deps).
      * anything else — delegate to ``commander_fn`` unchanged.
    """
    if search_fn is None:
        from app.research.literature import search_literature as search_fn
    if propose_fn is None:
        from app.research.hypothesis import propose_hypotheses as propose_fn
    if commander_fn is None:
        from app.autonomous_executor.commander_adapter import make_commander_adapter

        commander_fn = make_commander_adapter()
    if gate_fn is None:
        from app.epistemic.gate_research_evidence import evaluate as gate_fn

    def _delegate(step: ExecutorStep, run: ExecutorRun, prompt: str) -> CommanderResult:
        synthetic = ExecutorStep(
            step_id=step.step_id,
            description=prompt,
            crew_hint=step.crew_hint,
        )
        return commander_fn(synthetic, run)

    def _adapter(step: ExecutorStep, run: ExecutorRun) -> CommanderResult:
        hint = step.crew_hint

        if hint == HINT_LITERATURE:
            hits = search_fn(run.goal) or []
            rows = [h.to_dict() if hasattr(h, "to_dict") else dict(h) for h in hits]
            run.record_note(f"literature: {len(rows)} hit(s)")
            return CommanderResult(text=_encode(rows))

        if hint == HINT_HYPOTHESES:
            lit = _decode_list(run, HINT_LITERATURE)
            hyps = propose_fn(run.goal, literature=lit) or []
            rows = [h.to_dict() if hasattr(h, "to_dict") else dict(h) for h in hyps]
            run.record_note(f"hypotheses: {len(rows)} proposed")
            return CommanderResult(text=_encode(rows))

        if hint == HINT_INVESTIGATE:
            return _delegate(step, run, _build_investigate_prompt(run))

        if hint == HINT_DRAFT:
            return _delegate(step, run, _build_draft_prompt(run))

        if hint == HINT_GATE:
            draft = _text_for(run, HINT_DRAFT) or _text_for(run, HINT_INVESTIGATE)
            try:
                action, note = gate_fn(
                    proposal_text=draft,
                    task_id=run.run_id,
                    verdict=None,
                )
            except Exception:
                logger.debug("research.run: gate evaluation failed", exc_info=True)
                return CommanderResult(text="research-evidence gate: unavailable")
            if action:
                return CommanderResult(
                    text=f"BLOCKED: research-evidence gate escalated to {action}. {note}".strip()
                )
            detail = note or "no uncited empirical claims detected"
            return CommanderResult(text=f"research-evidence gate: clear ({detail})")

        if hint == HINT_SYNTHESIZE:
            # Bake a ResearchDossier PDF into the run. Failure-isolated: the
            # dossier render needs reportlab + pydantic (gateway-only deps), so
            # a host run with neither must not fail the step — the on-demand
            # endpoint is the primary surface; this is a convenience artifact.
            try:
                from app.research.dossier import render_research_dossier

                path = render_research_dossier(run)
                run.record_note(f"dossier: {path.name}")
                return CommanderResult(text=_encode({"dossier_path": str(path)}))
            except Exception as exc:
                logger.debug("research.run: dossier synthesis failed", exc_info=True)
                return CommanderResult(text=f"dossier synthesis unavailable: {exc}")

        # Unknown hint → behave exactly like the plain commander adapter.
        return commander_fn(step, run)

    return _adapter


# ── Run construction + driving ────────────────────────────────────────────────


def build_research_run(
    question: str,
    *,
    requestor: str = "research",
    zone: str = "autonomous",
    budget: Optional[Budget] = None,
    synthesize: bool = False,
) -> ExecutorRun:
    """Create a research ExecutorRun with its plan pre-populated.

    The run is returned in PLANNING with the plan already attached — the
    driver treats an operator-/caller-prepopulated plan as a first-class case
    and goes straight to RUNNING. ``zone`` defaults to ``"autonomous"`` so the
    research-evidence gate (which only activates outside ``chat``) engages on
    the draft. The run is NOT persisted — the caller owns the store.

    With ``synthesize=True`` a sixth dossier-render step is appended (see
    :func:`plan_research`); the default plan stays five steps.
    """
    q = (question or "").strip()
    if not q:
        raise ValueError("research run requires a non-empty question")
    run = ExecutorRun(
        run_id=f"research-{uuid.uuid4().hex[:12]}",
        goal=q,
        requestor=requestor,
        zone=zone,
        budget=budget or Budget(),
    )
    run.transition(ExecutorStatus.PLANNING)
    for step in plan_research(q, run, synthesize=synthesize):
        run.add_step(description=step.description, crew_hint=step.crew_hint)
    return run


def run_to_completion(
    run: ExecutorRun,
    *,
    adapter: Optional[CommanderFn] = None,
    planner_fn: Optional[Callable] = None,
    max_iterations: int = 12,
    bind_thread: bool = False,
) -> ExecutorRun:
    """Drive ``run`` with the research adapter until it stops advancing.

    Stops when the run reaches a terminal state, parks in BLOCKED/PAUSED
    (the gate-escalation path lands here), or ``max_iterations`` is hit
    (a safety bound — a five-step plan needs six ticks). Mutates and returns
    the run; the caller persists via ``store.save`` if desired. Composition
    only — owns no persistence.

    Phase D — ``bind_thread`` (default off). When true, the run is bound to a
    Thread before the loop and the thread is closed to match the run's final
    state after it. Binding gives the cross-run-learning loop for free —
    ``create_thread``'s ``consult_before_create`` dedups against past closures,
    and the closing transition's ``distill_on_closure`` writes the
    approaches-tried summary back into the ``lessons_learned`` KB. Off by
    default so this synchronous path stays pure composition for callers that
    don't want an operator-visible Thread; the async delegate path binds
    unconditionally (it is already gated by ``autonomous_executor_enabled``).
    Both calls are failure-isolated inside ``binding`` — an unbound or
    unclosable thread never blocks the run from completing.
    """
    adapter = adapter or make_research_adapter()
    planner_fn = planner_fn or plan_research

    if bind_thread:
        from app.research.binding import bind_run_to_thread

        bind_run_to_thread(run)

    for _ in range(max(1, max_iterations)):
        if run.is_terminal or run.status in (
            ExecutorStatus.BLOCKED,
            ExecutorStatus.PAUSED,
        ):
            break
        advance_one_step(run, commander_fn=adapter, planner_fn=planner_fn)

    if bind_thread:
        from app.research.binding import close_thread_for_run

        close_thread_for_run(run)

    return run


def summarise_run(run: ExecutorRun) -> ResearchRunOutcome:
    """Extract the research artifacts from a (finished or partial) run."""
    lit = _decode_list(run, HINT_LITERATURE)
    hyps = _decode_list(run, HINT_HYPOTHESES)
    top = None
    for hyp in hyps:
        text = str(hyp.get("text") or "").strip()
        if text:
            top = text
            break

    gate_text = _text_for(run, HINT_GATE)
    gate_action = next((a for a in _GATE_ACTIONS if a in gate_text), None)

    return ResearchRunOutcome(
        question=run.goal,
        status=run.status.value,
        n_literature=len(lit),
        n_hypotheses=len(hyps),
        top_hypothesis=top,
        draft=_text_for(run, HINT_DRAFT),
        gate_action=gate_action,
        gate_note=gate_text,
    )


__all__ = [
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
]
