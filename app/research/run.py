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
import re
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
# Phase C — the experiment spine (off by default; opt in with ``experiment=True``).
# These three steps REPLACE the single ``investigate`` step: design a runnable
# measurement, run it FULLY AUTONOMOUSLY in an ephemeral Docker sandbox, then
# analyze the result into a gated epistemic Claim. The autonomy is bounded by
# the per-run Budget, the sandbox, and the default-OFF
# ``research_experiments_enabled`` switch (see :mod:`app.research.experiment`).
HINT_DESIGN_EXPERIMENT = "research:design_experiment"
HINT_RUN_EXPERIMENT = "research:run_experiment"
HINT_ANALYZE_RESULT = "research:analyze_result"
# Optional final step (off by default). Renders the run's artifacts into a
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
    experiment: bool = False,
) -> list[ExecutorStep]:
    """Return the research steps, each carrying a ``research:*`` hint.

    Matches the ``PlannerFn`` signature ``(goal, run) -> list[ExecutorStep]``
    so it can be injected straight into the driver. Only ``description`` and
    ``crew_hint`` survive (the driver re-creates each step via
    :meth:`ExecutorRun.add_step`), so those are the only fields populated.
    Raises ``ValueError`` on an empty goal — the driver catches it and marks
    the run FAILED with that reason (mirrors the default planner).

    By default this is the five-step chain (literature → hypotheses →
    investigate → draft → gate).

    * ``experiment=True`` (Phase C) — the single ``investigate`` step is
      REPLACED by the three-step experiment spine (design_experiment →
      run_experiment → analyze_result), giving the seven-step chain
      literature → hypotheses → design_experiment → run_experiment →
      analyze_result → draft → gate.
    * ``synthesize=True`` — a trailing :data:`HINT_SYNTHESIZE` step is
      appended that bakes a ResearchDossier PDF into the run.

    With neither flag the plan is byte-identical to the original Phase-3
    five-step contract.
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
    ]
    if experiment:
        steps += [
            ExecutorStep(
                step_id="",
                description=f"Design a runnable experiment for: {short}",
                crew_hint=HINT_DESIGN_EXPERIMENT,
            ),
            ExecutorStep(
                step_id="",
                description=f"Run the designed experiment for: {short}",
                crew_hint=HINT_RUN_EXPERIMENT,
            ),
            ExecutorStep(
                step_id="",
                description=f"Analyze the experiment result for: {short}",
                crew_hint=HINT_ANALYZE_RESULT,
            ),
        ]
    else:
        steps.append(
            ExecutorStep(
                step_id="",
                description=f"Investigate the leading hypothesis for: {short}",
                crew_hint=HINT_INVESTIGATE,
            )
        )
    steps += [
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
    # Phase C: when the experiment spine ran in place of ``investigate``, the
    # analysis narrative carries the findings the draft must fold in.
    investigation = (_text_for(run, HINT_INVESTIGATE) or _text_for(run, HINT_ANALYZE_RESULT)).strip()
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


# ── Phase C: experiment spine helpers ─────────────────────────────────────────

# First fenced code block, language tag optional (``python`` / ``py`` / none).
_PY_FENCE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def _experiments_enabled() -> bool:
    """Master switch for Phase C experiments (failure-closed).

    Reads the default-OFF ``research_experiments_enabled`` runtime setting.
    Any failure (missing gateway deps on a host, unreadable settings) reads
    as OFF, so an experiment container never spawns by accident.
    """
    try:
        from app.runtime_settings import get_research_experiments_enabled

        return bool(get_research_experiments_enabled())
    except Exception:
        logger.debug("research.run: experiments-enabled read failed", exc_info=True)
        return False


def _default_gate_output(*, proposal_text: str, task_id: str, triggering_claim_id=None):
    """Default analyze_result gate — the epistemic output gate, resolved lazily.

    ``app.epistemic.orchestrator_hook`` pulls in psycopg2 (gateway-only), so it
    is imported here, not at module load, and any failure is isolated to
    ``None`` (ship). Returns the gate's ``GateResult`` (``.action`` ∈
    {ship, revise, block}, ``.final_text``) or ``None`` when unavailable.
    """
    try:
        from app.epistemic.orchestrator_hook import gate_output as _gate

        return _gate(
            proposal_text=proposal_text,
            task_id=task_id,
            triggering_claim_id=triggering_claim_id,
        )
    except Exception:
        logger.debug("research.run: epistemic gate unavailable", exc_info=True)
        return None


def _extract_python_script(text: str) -> str:
    """Pull a runnable Python script out of a Commander prose reply.

    Prefers the first fenced ```python block; falls back to treating the whole
    reply as a script when it structurally looks like Python (an import / def /
    print call). Returns ``""`` when nothing usable is found — the
    run_experiment step then records a non-blocking skipped marker.
    """
    if not text:
        return ""
    m = _PY_FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    stripped = text.strip()
    if any(tok in stripped for tok in ("import ", "def ", "print(")):
        return stripped
    return ""


def _build_design_experiment_prompt(run: ExecutorRun) -> str:
    hyps = _hypotheses(run)
    titles = _lit_titles(run)
    parts = [
        "Design a small, self-contained experiment that tests the leading "
        "hypothesis for the research question below. Write it as a single "
        "Python 3 script that uses ONLY the standard library, runs to "
        "completion in well under a minute, needs no network access and no "
        "input files, and prints its measurements to stdout as the result. "
        "Do not read or write files outside the current working directory. "
        "Output exactly ONE Python code block and nothing else.",
        f"\nResearch question: {run.goal}",
    ]
    if hyps:
        parts.append("\nLeading hypothesis to test:\n- " + hyps[0])
    if titles:
        parts.append("Prior literature for context:\n" + "\n".join(f"- {t}" for t in titles))
    return "\n".join(parts)


def _build_analysis_text(run: ExecutorRun) -> str:
    """Compose the analysis narrative from the run_experiment envelope.

    Handles the three run_experiment outcomes: a non-blocking ``skipped``
    marker (switch off / no script), a spawn/transport failure
    (``ok: False``), and a real run (``ok: True`` with the inner result's
    returncode / timed_out / stdout / stderr).
    """
    raw = _text_for(run, HINT_RUN_EXPERIMENT)
    try:
        payload = json.loads(raw or "{}")
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    if "skipped" in payload:
        return (
            f"Research question: {run.goal}\n\n"
            "Experiment not run: "
            + str(payload.get("skipped"))
            + ". No empirical measurement is available, so any findings rest "
            "on the literature and hypotheses alone."
        )

    if not payload.get("ok", False):
        return (
            f"Research question: {run.goal}\n\n"
            "Experiment failed to run: "
            + str(payload.get("error") or "unknown error")
            + ". No measurement was produced."
        )

    result = payload.get("result") or {}
    if not isinstance(result, dict):
        result = {}
    rc = result.get("returncode")
    timed_out = bool(result.get("timed_out"))
    stdout = str(result.get("stdout") or "").strip()
    stderr = str(result.get("stderr") or "").strip()
    ran_clean = bool(result.get("ok"))

    parts = [f"Research question: {run.goal}", ""]
    if timed_out:
        parts.append("The experiment timed out before completing.")
    elif ran_clean:
        parts.append("The experiment ran to completion (exit code 0).")
    else:
        parts.append(f"The experiment exited with a non-zero status (returncode={rc}).")
    if stdout:
        parts.append("\nMeasurements (stdout):\n" + stdout[:4000])
    if stderr and not ran_clean:
        parts.append("\nDiagnostics (stderr):\n" + stderr[:1000])
    parts.append(
        "\nInterpret these measurements against the leading hypothesis and "
        "state concretely what they support or refute."
    )
    return "\n".join(parts)


# ── Adapter ────────────────────────────────────────────────────────────────


def make_research_adapter(
    *,
    search_fn: Optional[Callable] = None,
    propose_fn: Optional[Callable] = None,
    commander_fn: Optional[CommanderFn] = None,
    gate_fn: Optional[Callable] = None,
    experiment_fn: Optional[Callable] = None,
    enabled_fn: Optional[Callable] = None,
    gate_output_fn: Optional[Callable] = None,
) -> CommanderFn:
    """Build a ``CommanderFn`` that executes research steps by crew-hint.

    The seams default to the real subsystems, resolved lazily so constructing
    the adapter is cheap and host-safe:

      * ``search_fn``       → ``app.research.literature.search_literature``
      * ``propose_fn``      → ``app.research.hypothesis.propose_hypotheses``
      * ``commander_fn``    → ``commander_adapter.make_commander_adapter()``
      * ``gate_fn``         → ``epistemic.gate_research_evidence.evaluate``
      * ``experiment_fn``   → ``app.research.experiment.run_experiment_script``
      * ``enabled_fn``      → :func:`_experiments_enabled` (Phase C switch)
      * ``gate_output_fn``  → :func:`_default_gate_output` (epistemic gate)

    Dispatch by ``step.crew_hint``:

      * literature  — search; store hits as JSON in ``result_text``.
      * hypotheses  — propose grounded in the literature step's hits; store
        hypotheses as JSON.
      * investigate / draft — build a prompt from prior artifacts and
        delegate to ``commander_fn`` (text in, text out; cost carried
        through unchanged).
      * design_experiment (Phase C) — delegate to ``commander_fn`` to turn the
        leading hypothesis into a runnable Python script.
      * run_experiment (Phase C) — run that script FULLY AUTONOMOUSLY in an
        ephemeral Docker sandbox via ``experiment_fn``, gated by
        ``enabled_fn()`` (off → a non-blocking ``skipped`` marker; the spine
        carries on so design + analysis still produce a draft).
      * analyze_result (Phase C) — turn the measurement into an epistemic
        Claim (best-effort) and run it through ``gate_output_fn``; an
        ``action == "block"`` verdict starts the result with ``BLOCKED:`` so
        the driver tips the run into BLOCKED for operator review.
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
    if experiment_fn is None:
        from app.research.experiment import run_experiment_script as experiment_fn
    if enabled_fn is None:
        enabled_fn = _experiments_enabled
    if gate_output_fn is None:
        gate_output_fn = _default_gate_output

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

        if hint == HINT_DESIGN_EXPERIMENT:
            # Turn the leading hypothesis into a runnable measurement script.
            return _delegate(step, run, _build_design_experiment_prompt(run))

        if hint == HINT_RUN_EXPERIMENT:
            # FULLY AUTONOMOUS — no per-experiment operator gate. Bounded by the
            # per-run Budget, the ephemeral Docker sandbox, and the default-OFF
            # ``research_experiments_enabled`` switch (read failure-closed). When
            # the switch is off we record a NON-BLOCKING skipped marker and let
            # the spine carry on — design + analysis still produce a draft.
            if not enabled_fn():
                run.record_note("experiment: skipped (research_experiments_enabled off)")
                return CommanderResult(
                    text=_encode({"skipped": "research_experiments_enabled off"})
                )
            script = _extract_python_script(_text_for(run, HINT_DESIGN_EXPERIMENT))
            if not script:
                run.record_note("experiment: skipped (no script produced)")
                return CommanderResult(
                    text=_encode({"skipped": "no experiment script produced"})
                )
            try:
                result = experiment_fn(script, timeout_s=300)
            except Exception as exc:  # experiment_fn is failure-isolated, but be safe
                logger.debug("research.run: experiment_fn raised", exc_info=True)
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            ran_ok = bool(isinstance(result, dict) and result.get("ok"))
            run.record_note(f"experiment: ran (ok={ran_ok})")
            return CommanderResult(text=_encode(result))

        if hint == HINT_ANALYZE_RESULT:
            analysis_text = _build_analysis_text(run)
            # Best-effort epistemic Claim. The ledger is host-safe (no DB deps),
            # but stay failure-isolated so a claim-store hiccup never fails the
            # run — the analysis narrative is the load-bearing output.
            try:
                from app.epistemic.ledger import (
                    Claim,
                    Evidence,
                    Ledger,
                    VerificationStatus,
                )

                led = Ledger(task_id=run.run_id)
                claim = Claim.new(
                    task_id=run.run_id,
                    agent_role="research",
                    statement=(
                        f"Experiment for '{run.goal}' produced the recorded measurements."
                    )[:300],
                    status=VerificationStatus.INFERRED,
                    evidence=(
                        Evidence(
                            kind="tool_call",
                            source_ref=f"experiment:{run.run_id}",
                            excerpt=analysis_text[:1000],
                            confidence=0.6,
                        ),
                    ),
                    load_bearing=True,
                    tags=("research", "experiment"),
                )
                led.emit(claim)
                run.record_note(f"claim emitted: {claim.claim_id}")
            except Exception:
                logger.debug("research.run: claim emit failed", exc_info=True)

            # Gate the analysis. ``block`` is the only verdict that escalates;
            # everything else ships (observe-mode always ships). The gate never
            # raises, but wrap defensively for injected fakes.
            try:
                gres = gate_output_fn(proposal_text=analysis_text, task_id=run.run_id)
            except Exception:
                logger.debug("research.run: gate_output_fn raised", exc_info=True)
                gres = None
            if gres is not None and getattr(gres, "action", None) == "block":
                reason = getattr(gres, "user_visible_reason", "") or ""
                run.record_note("analyze: epistemic gate blocked")
                return CommanderResult(
                    text=f"BLOCKED: epistemic gate blocked the experiment analysis. {reason}".strip()
                )
            final = getattr(gres, "final_text", None) if gres is not None else None
            return CommanderResult(text=(final or analysis_text))

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
    experiment: bool = False,
) -> ExecutorRun:
    """Create a research ExecutorRun with its plan pre-populated.

    The run is returned in PLANNING with the plan already attached — the
    driver treats an operator-/caller-prepopulated plan as a first-class case
    and goes straight to RUNNING. ``zone`` defaults to ``"autonomous"`` so the
    research-evidence gate (which only activates outside ``chat``) engages on
    the draft. The run is NOT persisted — the caller owns the store.

    * ``experiment=True`` (Phase C) — swap the ``investigate`` step for the
      design_experiment → run_experiment → analyze_result spine; the
      experiment runs FULLY AUTONOMOUSLY (no per-experiment operator gate)
      but stays bounded by ``budget``, the ephemeral Docker sandbox, and the
      default-OFF ``research_experiments_enabled`` switch.
    * ``synthesize=True`` — append a trailing dossier-render step.

    With neither flag the plan stays the five-step Phase-3 contract.
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
    for step in plan_research(q, run, synthesize=synthesize, experiment=experiment):
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
    "HINT_DESIGN_EXPERIMENT",
    "HINT_RUN_EXPERIMENT",
    "HINT_ANALYZE_RESULT",
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
