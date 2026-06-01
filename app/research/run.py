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
# Phase B — anti-fabrication verification pass (off by default; opt in with
# ``verify=True``). Inserted just before the evidence gate: it verifies every
# identifier-bearing citation in the draft (dropping fabricated ones via
# ``app.research.citation_verifier``) and BLOCKS a draft whose empirical claims
# trace to NEITHER a recorded measurement NOR a verified citation — the
# "results must come from somewhere real" property.
HINT_VERIFY = "research:verify"
# Phase C/D — emit the actual paper. Composes the run's artifacts into a
# Manuscript (``app.research.manuscript``) and renders it to paper.tex +
# references.bib (``app.research.typeset_latex``). Off by default; opt in with
# ``compose=True`` + the ``research_compose_paper_enabled`` switch. Runs AFTER
# the gate, so a blocked (fabrication-flagged) draft is never turned into a paper.
HINT_COMPOSE = "research:compose"

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
    verify: bool = False,
    compose: bool = False,
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
    * ``verify=True`` (Phase B) — a :data:`HINT_VERIFY` step is inserted just
      before the gate: it verifies the draft's citations and grounds its
      empirical claims (see :func:`make_research_adapter`).
    * ``compose=True`` (Phase C/D) — a :data:`HINT_COMPOSE` step is appended
      AFTER the gate that composes the run's artifacts into a manuscript and
      renders ``paper.tex`` + ``references.bib`` (so a gate-blocked run never
      produces a paper).

    With none of the flags the plan is byte-identical to the original Phase-3
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
    steps.append(
        ExecutorStep(
            step_id="",
            description=f"Draft research findings for: {short}",
            crew_hint=HINT_DRAFT,
        )
    )
    if verify:
        steps.append(
            ExecutorStep(
                step_id="",
                description="Verify citations + ground empirical claims in the draft",
                crew_hint=HINT_VERIFY,
            )
        )
    steps.append(
        ExecutorStep(
            step_id="",
            description="Check the draft for uncited empirical claims",
            crew_hint=HINT_GATE,
        )
    )
    if compose:
        steps.append(
            ExecutorStep(
                step_id="",
                description=f"Compose paper.tex + references.bib for: {short}",
                crew_hint=HINT_COMPOSE,
            )
        )
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


def _experiment_repair_enabled() -> bool:
    """Master switch for the Phase-C experiment repair loop (failure-closed).

    Reads the default-OFF ``research_experiment_repair_enabled`` setting. When
    off, ``run_experiment`` keeps its one-shot behaviour byte-for-byte; when on,
    a failed/empty measurement is repaired-and-rerun (bounded). Any read failure
    reads as OFF so the repair loop never engages by accident.
    """
    try:
        from app.runtime_settings import get_research_experiment_repair_enabled

        return bool(get_research_experiment_repair_enabled())
    except Exception:
        logger.debug("research.run: repair-enabled read failed", exc_info=True)
        return False


def _default_experiment_repair(script: str, *, experiment_fn, goal: str, timeout_s: int = 300) -> dict:
    """Default repair seam — the bounded design→run→repair loop.

    Lazily imports ``experiment_repair`` (which lazily imports the iterate loop)
    so module load stays host-safe. ``experiment_fn`` is threaded through so the
    repair loop shares the adapter's (possibly injected) container runner, and
    ``_extract_python_script`` is reused to pull the rewritten script out of each
    repair reply.
    """
    from app.research.experiment_repair import run_experiment_with_repair

    return run_experiment_with_repair(
        script,
        experiment_fn=experiment_fn,
        extract_fn=_extract_python_script,
        goal=goal,
        timeout_s=timeout_s,
    )


# Quantitative empirical assertions (percentage / speedup / p-value / sample
# size / throughput) — the kind of claim the verification step requires to be
# grounded in a measurement or a verified citation.
_EMPIRICAL_CLAIM_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*%"
    r"|\b\d+(?:\.\d+)?\s*[x×]\b"
    r"|\bp\s*[<=>]\s*0?\.\d+"
    r"|\bn\s*=\s*\d+"
    r"|\b\d+(?:\.\d+)?\s*(?:ms|s|rps|qps|fps|gb|mb|kb|tok(?:ens?)?/s)\b",
    re.IGNORECASE,
)


def _draft_has_empirical_claims(text: str) -> bool:
    """True if the draft makes a quantitative empirical assertion that must be
    grounded in a measurement or a verified citation."""
    return bool(_EMPIRICAL_CLAIM_RE.search(text or ""))


def _run_measurement_present(run: ExecutorRun) -> bool:
    """True if the run's experiment step produced a real measurement.

    Reuses ``experiment_repair.measurement_present`` over the run_experiment
    envelope. False when there was no experiment step (the 5-step plan) or it
    skipped/failed — in which case any empirical claim needs a verified citation.
    """
    raw = _text_for(run, HINT_RUN_EXPERIMENT)
    if not raw:
        return False
    try:
        env = json.loads(raw)
        from app.research.experiment_repair import measurement_present

        return bool(measurement_present(env))
    except Exception:
        logger.debug("research.run: measurement_present read failed", exc_info=True)
        return False


def _citation_verification_enabled() -> bool:
    """Master switch for the Phase-B verification step (failure-closed)."""
    try:
        from app.runtime_settings import get_research_citation_verification_enabled

        return bool(get_research_citation_verification_enabled())
    except Exception:
        logger.debug("research.run: citation-verification-enabled read failed", exc_info=True)
        return False


def _default_verify_references(citations):
    """Default verification seam — the 4-layer citation verifier."""
    from app.research.citation_verifier import verify_references

    return verify_references(citations)


def _compose_paper_enabled() -> bool:
    """Master switch for the Phase-C/D compose step (failure-closed)."""
    try:
        from app.runtime_settings import get_research_compose_paper_enabled

        return bool(get_research_compose_paper_enabled())
    except Exception:
        logger.debug("research.run: compose-enabled read failed", exc_info=True)
        return False


def _measurements_text(run: ExecutorRun) -> str:
    """The experiment's stdout (the measurement), or '' when no experiment ran."""
    raw = _text_for(run, HINT_RUN_EXPERIMENT)
    if not raw:
        return ""
    try:
        env = json.loads(raw)
        result = env.get("result") if isinstance(env, dict) else None
        return str(result.get("stdout") or "") if isinstance(result, dict) else ""
    except Exception:
        return ""


def _kept_citations(run: ExecutorRun, verify_references_fn) -> list:
    """The verified citations for the bibliography.

    Reuses the verify step's persisted ``kept`` set when present (no second
    round of literature-API calls); otherwise extracts the draft's identifiers
    and verifies them here. Empty when neither yields anything.
    """
    from app.research.citation import Citation, extract_citations

    raw = _text_for(run, HINT_VERIFY)
    if raw:
        try:
            data = json.loads(raw)
            kept = data.get("kept") if isinstance(data, dict) else None
            if isinstance(kept, list):
                return [Citation.from_dict(d) for d in kept if isinstance(d, dict)]
        except Exception:
            logger.debug("research.run: reuse of verify kept-citations failed", exc_info=True)
    draft = _text_for(run, HINT_DRAFT) or _text_for(run, HINT_ANALYZE_RESULT) or _text_for(run, HINT_INVESTIGATE)
    try:
        report = verify_references_fn(extract_citations(draft))
        return list(getattr(report, "kept", []) or [])
    except Exception:
        logger.debug("research.run: compose-time citation verification failed", exc_info=True)
        return []


def _artifacts_from_run(run: ExecutorRun, citations: list):
    """Assemble the manuscript inputs from the run's persisted step artifacts."""
    from app.research.manuscript import ResearchArtifacts

    hyps = [str((h or {}).get("text") or "").strip() for h in _decode_list(run, HINT_HYPOTHESES)]
    return ResearchArtifacts(
        question=run.goal,
        literature=_decode_list(run, HINT_LITERATURE),
        hypotheses=[h for h in hyps if h],
        findings=(
            _text_for(run, HINT_DRAFT)
            or _text_for(run, HINT_ANALYZE_RESULT)
            or _text_for(run, HINT_INVESTIGATE)
        ),
        measurements=_measurements_text(run),
        citations=citations,
    )


def _paper_output_dir(run: ExecutorRun) -> str:
    """Per-run output directory for the rendered paper, under the workspace."""
    from pathlib import Path

    try:
        from app.paths import WORKSPACE_ROOT

        base = Path(WORKSPACE_ROOT)
    except Exception:
        base = Path("workspace")
    return str(base / "research" / "papers" / run.run_id)


def _compose_paper_for_run(run: ExecutorRun, *, verify_references_fn) -> dict:
    """Compose the run into a Manuscript and render paper.tex + references.bib."""
    from app.research.manuscript import compose_manuscript
    from app.research.typeset_latex import render_latex

    citations = _kept_citations(run, verify_references_fn)
    manuscript = compose_manuscript(_artifacts_from_run(run, citations))
    rendered = render_latex(manuscript, output_dir=_paper_output_dir(run))
    return {
        "paper_tex": str(rendered.tex_path) if rendered.tex_path else None,
        "references_bib": str(rendered.bib_path) if rendered.bib_path else None,
        "sections": len(manuscript.sections),
        "references": len(manuscript.references),
        "warnings": len(manuscript.all_warnings()),
    }


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


def _focused_completion(
    prompt: str, *, role: str, task_hint: str, max_tokens: int = 2500
) -> str:
    """Run one focused, factory-routed completion and return its text.

    The prose/code steps (design_experiment, investigate, draft) each need a
    single on-topic artifact, so they go through the factory's sanctioned
    raw-completion path — NOT the conversational ``Commander``, whose
    intent-routing/persona returned status chronicles for these structured
    prompts (observed live). Mirrors how ``literature``/``hypotheses`` call
    focused functions rather than the Commander. Failure-isolated: returns
    ``""`` on any error so the step degrades gracefully.
    """
    try:
        from app.llm_factory import chat_completion_for_role

        handle = chat_completion_for_role(role=role, task_hint=task_hint)
        resp = handle.create(
            messages=[{"role": "user", "content": prompt}], max_tokens=max_tokens
        )
        return resp.choices[0].message.content or ""
    except Exception:
        logger.debug(
            "research.run: focused completion failed (role=%s)", role, exc_info=True
        )
        return ""


def _default_design_experiment(prompt: str) -> str:
    """Author a runnable measurement script (code-gen role)."""
    return _focused_completion(prompt, role="coding", task_hint="research experiment script")


def _default_investigate(prompt: str) -> str:
    """Investigate the leading hypothesis as focused research prose."""
    return _focused_completion(prompt, role="research", task_hint="research investigation")


def _default_draft(prompt: str) -> str:
    """Write up the findings (writing role; allow a longer answer)."""
    return _focused_completion(
        prompt, role="writing", task_hint="research findings draft", max_tokens=3000
    )


def _ensure_research_task_row(run: ExecutorRun) -> None:
    """Register a ``control_plane.crew_tasks`` row for the research run.

    ``epistemic_claims.task_id`` FKs into ``crew_tasks.id``; research runs live
    in the autonomous-executor store, not ``crew_tasks``, so analyze_result's
    Claim would fail the FK and never persist (only the in-memory ledger kept
    it). Upserting a lightweight task row makes the run first-class for the
    epistemic tables. Failure-isolated — never blocks the run (a host without a
    DB simply skips, exactly as before).
    """
    try:
        from app.control_plane.db import execute

        execute(
            """
            INSERT INTO control_plane.crew_tasks (id, crew, state, summary)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (run.run_id, "research", "running", (run.goal or "")[:500]),
        )
    except Exception:
        logger.debug("research.run: ensure crew_tasks row failed", exc_info=True)


def make_research_adapter(
    *,
    search_fn: Optional[Callable] = None,
    propose_fn: Optional[Callable] = None,
    commander_fn: Optional[CommanderFn] = None,
    gate_fn: Optional[Callable] = None,
    experiment_fn: Optional[Callable] = None,
    enabled_fn: Optional[Callable] = None,
    repair_enabled_fn: Optional[Callable] = None,
    experiment_repair_fn: Optional[Callable] = None,
    gate_output_fn: Optional[Callable] = None,
    design_fn: Optional[Callable] = None,
    investigate_fn: Optional[Callable] = None,
    draft_fn: Optional[Callable] = None,
    verify_references_fn: Optional[Callable] = None,
    citation_verification_enabled_fn: Optional[Callable] = None,
    compose_fn: Optional[Callable] = None,
    compose_enabled_fn: Optional[Callable] = None,
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
      * ``design_fn``       → :func:`_default_design_experiment` (code-gen)
      * ``investigate_fn``  → :func:`_default_investigate` (research prose)
      * ``draft_fn``        → :func:`_default_draft` (write-up)

    Dispatch by ``step.crew_hint``:

      * literature  — search; store hits as JSON in ``result_text``.
      * hypotheses  — propose grounded in the literature step's hits; store
        hypotheses as JSON.
      * investigate / draft — build a prompt from prior artifacts and run a
        focused completion via ``investigate_fn`` / ``draft_fn`` (text in,
        text out), NOT the conversational Commander.
      * design_experiment (Phase C) — author a runnable Python script from the
        leading hypothesis via ``design_fn`` (a focused code-gen completion,
        NOT the conversational Commander, which returns a status chronicle).
      * run_experiment (Phase C) — run that script FULLY AUTONOMOUSLY in an
        ephemeral Docker sandbox via ``experiment_fn``, gated by
        ``enabled_fn()`` (off → a non-blocking ``skipped`` marker; the spine
        carries on so design + analysis still produce a draft). When
        ``repair_enabled_fn()`` is also on, the run goes through
        ``experiment_repair_fn`` — a bounded design→run→repair loop that
        rewrites + re-runs a failed/empty measurement — instead of a single
        shot; off (the default) keeps the one-shot path byte-for-byte.
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
    if repair_enabled_fn is None:
        repair_enabled_fn = _experiment_repair_enabled
    if experiment_repair_fn is None:
        experiment_repair_fn = _default_experiment_repair
    if gate_output_fn is None:
        gate_output_fn = _default_gate_output
    if design_fn is None:
        design_fn = _default_design_experiment
    if investigate_fn is None:
        investigate_fn = _default_investigate
    if draft_fn is None:
        draft_fn = _default_draft
    if verify_references_fn is None:
        verify_references_fn = _default_verify_references
    if citation_verification_enabled_fn is None:
        citation_verification_enabled_fn = _citation_verification_enabled
    if compose_fn is None:
        compose_fn = _compose_paper_for_run
    if compose_enabled_fn is None:
        compose_enabled_fn = _compose_paper_enabled

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
            # Focused research-prose completion, NOT the conversational
            # Commander (which returned status chronicles for these prompts).
            return CommanderResult(
                text=investigate_fn(_build_investigate_prompt(run)) or ""
            )

        if hint == HINT_DESIGN_EXPERIMENT:
            # Author the measurement script via a focused code-gen completion
            # (NOT the conversational Commander, which returned a status
            # chronicle instead of code — see _default_design_experiment).
            return CommanderResult(
                text=design_fn(_build_design_experiment_prompt(run)) or ""
            )

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
            if repair_enabled_fn():
                # Bounded design→run→repair (default-OFF, additional to the
                # ``research_experiments_enabled`` gate above): a failed/empty
                # measurement is rewritten and re-run, each round in a fresh
                # network=none container; the repair completion runs here,
                # gateway-side. Failure-isolated — the spine carries on either way.
                try:
                    result = experiment_repair_fn(
                        script, experiment_fn=experiment_fn, goal=run.goal, timeout_s=300
                    )
                except Exception as exc:
                    logger.debug("research.run: experiment_repair_fn raised", exc_info=True)
                    result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                rep = result.get("repair") if isinstance(result, dict) else None
                rounds = rep.get("rounds") if isinstance(rep, dict) else "?"
                ran_ok = bool(isinstance(result, dict) and result.get("ok"))
                run.record_note(f"experiment: ran with repair (ok={ran_ok}, rounds={rounds})")
                return CommanderResult(text=_encode(result))
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
                _ensure_research_task_row(run)
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
            # Focused write-up completion, NOT the conversational Commander.
            return CommanderResult(text=draft_fn(_build_draft_prompt(run)) or "")

        if hint == HINT_VERIFY:
            # Phase B — anti-fabrication pass. Gated by
            # ``citation_verification_enabled_fn`` (off → a non-blocking skip;
            # the spine still gates + completes). Escalates (BLOCKED) when a
            # cited identifier doesn't resolve, OR when the draft asserts an
            # empirical result backed by neither a recorded measurement nor a
            # verified citation. Failure-isolated — a sick verifier never blocks.
            if not citation_verification_enabled_fn():
                run.record_note("verify: skipped (research_citation_verification_enabled off)")
                return CommanderResult(
                    text=_encode({"skipped": "research_citation_verification_enabled off"})
                )
            draft = (
                _text_for(run, HINT_DRAFT)
                or _text_for(run, HINT_ANALYZE_RESULT)
                or _text_for(run, HINT_INVESTIGATE)
            )
            from app.research.citation import extract_citations

            try:
                report = verify_references_fn(extract_citations(draft))
            except Exception:
                logger.debug("research.run: verify_references_fn raised", exc_info=True)
                run.record_note("verify: unavailable")
                return CommanderResult(text="research-citation verification: unavailable")
            dropped = list(getattr(report, "dropped", []) or [])
            verified = list(getattr(report, "verified", []) or [])
            measured = _run_measurement_present(run)
            has_empirical = _draft_has_empirical_claims(draft)
            reasons = []
            if dropped:
                reasons.append(f"{len(dropped)} unverifiable citation(s)")
            if has_empirical and not measured and not verified:
                reasons.append("empirical claims with no recorded measurement or verified citation")
            if reasons:
                detail = "; ".join(reasons)
                run.record_note(f"verify: escalate ({detail})")
                return CommanderResult(text=f"BLOCKED: anti-fabrication verification — {detail}")
            summary = report.summary() if hasattr(report, "summary") else {}
            kept = list(getattr(report, "kept", []) or [])
            run.record_note(f"verify: clear (verified={len(verified)}, dropped={len(dropped)})")
            return CommanderResult(
                text=_encode({
                    "verdict": "clear",
                    "citations": summary,
                    # Persist the verified set so a downstream compose step can
                    # reuse it for the bibliography without re-verifying (avoids
                    # a second round of literature-API calls).
                    "kept": [c.to_dict() for c in kept if hasattr(c, "to_dict")],
                })
            )

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

        if hint == HINT_COMPOSE:
            # Phase C/D — emit the paper. Gated by ``compose_enabled_fn`` (off →
            # non-blocking skip). Reached only after the gate, so a blocked
            # (fabrication-flagged) run never produces a paper. Failure-isolated:
            # a render failure degrades to a note, never fails the run.
            if not compose_enabled_fn():
                run.record_note("compose: skipped (research_compose_paper_enabled off)")
                return CommanderResult(
                    text=_encode({"skipped": "research_compose_paper_enabled off"})
                )
            try:
                result = compose_fn(run, verify_references_fn=verify_references_fn)
            except Exception as exc:
                logger.debug("research.run: compose_fn raised", exc_info=True)
                return CommanderResult(text=f"paper composition unavailable: {exc}")
            run.record_note(
                f"compose: paper.tex written ({result.get('sections')} sections, "
                f"{result.get('references')} refs)"
            )
            return CommanderResult(text=_encode(result))

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


# ── Delegate-input parsing (Signal / CLI) ─────────────────────────────────────

_DELEGATE_FLAG_WORDS = ("experiment", "verify", "compose", "synthesize")


def parse_delegate_flags(arg: str) -> tuple[dict, str]:
    """Split leading research flag-words off a ``/delegate research …`` argument.

    Returns ``({experiment, verify, compose, synthesize: bool}, goal)``. Flag
    words (optionally ``--``-prefixed) are consumed from the FRONT until the
    first non-flag token; everything after is the goal. So
    ``"verify compose how fast is X"`` → ({verify, compose}, "how fast is X").
    Pure + stdlib so the Signal command parser is host-testable.
    """
    flags = {w: False for w in _DELEGATE_FLAG_WORDS}
    tokens = (arg or "").split()
    i = 0
    while i < len(tokens):
        word = tokens[i].lower().lstrip("-")
        if word in flags:
            flags[word] = True
            i += 1
        else:
            break
    return flags, " ".join(tokens[i:]).strip()


# ── Run construction + driving ────────────────────────────────────────────────


def build_research_run(
    question: str,
    *,
    requestor: str = "research",
    zone: str = "autonomous",
    budget: Optional[Budget] = None,
    synthesize: bool = False,
    experiment: bool = False,
    verify: bool = False,
    compose: bool = False,
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
    * ``verify=True`` (Phase B) — insert the anti-fabrication ``verify`` step
      before the gate.
    * ``compose=True`` (Phase C/D) — append a ``compose`` step AFTER the gate
      that renders ``paper.tex`` + ``references.bib`` from the run's artifacts.

    With none of the flags the plan stays the five-step Phase-3 contract.
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
    for step in plan_research(
        q, run, synthesize=synthesize, experiment=experiment, verify=verify, compose=compose
    ):
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
    "HINT_VERIFY",
    "HINT_COMPOSE",
    "ResearchRunOutcome",
    "plan_research",
    "parse_delegate_flags",
    "make_research_adapter",
    "build_research_run",
    "run_to_completion",
    "summarise_run",
]
