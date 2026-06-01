"""Host-safe tests for the Phase-B anti-fabrication verification step
(``app.research.run``'s ``research:verify`` hint).

The citation verifier itself is INJECTED (``verify_references_fn``), so no
network runs — these tests pin the step's *policy*: drop unverifiable
citations, and block a draft whose empirical claims trace to neither a recorded
measurement nor a verified citation.
"""

from __future__ import annotations

import json

import app.research.run as R
from app.research.citation import Citation, CitationStatus
from app.research.citation_verifier import VerificationReport
from app.autonomous_executor.driver import CommanderResult
from app.autonomous_executor.models import ExecutorStatus, StepStatus

_CLEAN_MEASUREMENT = {
    "ok": True,
    "result": {"ok": True, "returncode": 0, "stdout": "throughput=1200", "stderr": "", "timed_out": False},
}


def _report(verified=(), ambiguous=(), dropped=()):
    v, a, d = list(verified), list(ambiguous), list(dropped)
    return VerificationReport(verified=v, ambiguous=a, dropped=d, kept=v + a)


def _step(run, hint):
    return next(s for s in run.plan if s.crew_hint == hint)


def _complete(run, hint, text):
    step = _step(run, hint)
    step.status = StepStatus.COMPLETED
    step.result_text = text
    return step


def _adapter(*, enabled=True, report=None):
    return R.make_research_adapter(
        search_fn=lambda g: [],
        propose_fn=lambda q, **k: [],
        commander_fn=lambda s, r: CommanderResult(text="FALLBACK"),
        gate_fn=lambda **k: (None, ""),
        design_fn=lambda p: "",
        draft_fn=lambda p: "DRAFT",
        investigate_fn=lambda p: "",
        experiment_fn=lambda s, *, timeout_s=300: {"ok": True, "result": {}},
        enabled_fn=lambda: False,
        gate_output_fn=lambda **k: None,
        citation_verification_enabled_fn=lambda: enabled,
        verify_references_fn=lambda cits: report if report is not None else _report(),
    )


def _run(*, verify=True, experiment=False):
    run = R.build_research_run("q", verify=verify, experiment=experiment)
    run.transition(ExecutorStatus.RUNNING)
    return run


# ── Planner ───────────────────────────────────────────────────────────────────


def test_verify_flag_inserts_step_before_gate():
    hints = [s.crew_hint for s in R.plan_research("q", verify=True)]
    assert hints == [R.HINT_LITERATURE, R.HINT_HYPOTHESES, R.HINT_INVESTIGATE, R.HINT_DRAFT, R.HINT_VERIFY, R.HINT_GATE]


def test_default_plan_has_no_verify_step():
    assert R.HINT_VERIFY not in [s.crew_hint for s in R.plan_research("q")]


def test_experiment_and_verify_plan_order():
    hints = [s.crew_hint for s in R.plan_research("q", experiment=True, verify=True)]
    assert hints == [
        R.HINT_LITERATURE,
        R.HINT_HYPOTHESES,
        R.HINT_DESIGN_EXPERIMENT,
        R.HINT_RUN_EXPERIMENT,
        R.HINT_ANALYZE_RESULT,
        R.HINT_DRAFT,
        R.HINT_VERIFY,
        R.HINT_GATE,
    ]


# ── Adapter branch ─────────────────────────────────────────────────────────────


def test_verify_skipped_when_disabled_is_non_blocking():
    run = _run()
    _complete(run, R.HINT_DRAFT, "some draft")
    out = _adapter(enabled=False)(_step(run, R.HINT_VERIFY), run)
    assert "skipped" in json.loads(out.text)
    assert not out.text.startswith("BLOCKED:")


def test_verify_clear_when_citations_verified_and_no_empirical_claims():
    run = _run()
    _complete(run, R.HINT_DRAFT, "A qualitative discussion citing 10.1000/x.")
    c = Citation(doi="10.1000/x", status=CitationStatus.VERIFIED)
    out = _adapter(report=_report(verified=[c]))(_step(run, R.HINT_VERIFY), run)
    assert not out.text.startswith("BLOCKED:")
    assert json.loads(out.text)["verdict"] == "clear"


def test_verify_blocks_on_unverifiable_citation():
    run = _run()
    _complete(run, R.HINT_DRAFT, "Per 10.9999/fake we conclude X.")
    c = Citation(doi="10.9999/fake", status=CitationStatus.UNVERIFIED)
    out = _adapter(report=_report(dropped=[c]))(_step(run, R.HINT_VERIFY), run)
    assert out.text.startswith("BLOCKED:")
    assert "unverifiable citation" in out.text


def test_verify_blocks_empirical_claim_with_no_grounding():
    run = _run()  # no experiment step → no measurement
    _complete(run, R.HINT_DRAFT, "Our method is 42% faster than the baseline.")
    out = _adapter(report=_report())(_step(run, R.HINT_VERIFY), run)  # no verified citation
    assert out.text.startswith("BLOCKED:")
    assert "no recorded measurement" in out.text


def test_verify_clears_empirical_claim_backed_by_measurement():
    run = _run(verify=True, experiment=True)
    _complete(run, R.HINT_RUN_EXPERIMENT, json.dumps(_CLEAN_MEASUREMENT))
    _complete(run, R.HINT_DRAFT, "Our method achieves 1200 rps, a 42% improvement.")
    out = _adapter(report=_report())(_step(run, R.HINT_VERIFY), run)
    assert not out.text.startswith("BLOCKED:")


def test_verify_clears_empirical_claim_backed_by_verified_citation():
    run = _run()  # no measurement, but a real citation backs the number
    _complete(run, R.HINT_DRAFT, "Prior work reports 42% gains (10.1000/x).")
    c = Citation(doi="10.1000/x", status=CitationStatus.VERIFIED)
    out = _adapter(report=_report(verified=[c]))(_step(run, R.HINT_VERIFY), run)
    assert not out.text.startswith("BLOCKED:")


def test_verify_isolated_on_verifier_exception():
    def boom(_cits):
        raise RuntimeError("verifier down")

    run = _run()
    _complete(run, R.HINT_DRAFT, "draft citing 10.1000/x")
    adapter = R.make_research_adapter(
        search_fn=lambda g: [],
        propose_fn=lambda q, **k: [],
        commander_fn=lambda s, r: CommanderResult(text="FALLBACK"),
        gate_fn=lambda **k: (None, ""),
        gate_output_fn=lambda **k: None,
        enabled_fn=lambda: False,
        citation_verification_enabled_fn=lambda: True,
        verify_references_fn=boom,
    )
    out = adapter(_step(run, R.HINT_VERIFY), run)
    assert "unavailable" in out.text
    assert not out.text.startswith("BLOCKED:")


# ── End-to-end drive over a verify-bearing plan ───────────────────────────────


def test_run_to_completion_with_verify_completes():
    run = R.build_research_run("q", verify=True)
    R.run_to_completion(run, adapter=_adapter(enabled=True, report=_report()))
    assert run.status is ExecutorStatus.COMPLETED
    assert _step(run, R.HINT_VERIFY).status is StepStatus.COMPLETED
    assert all(s.status is StepStatus.COMPLETED for s in run.plan)
