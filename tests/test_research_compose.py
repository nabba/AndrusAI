"""Host-safe tests for the Phase-C/D compose step (``app.research.run``'s
``research:compose`` hint) — the wiring that makes a run actually emit
paper.tex + references.bib.

The output directory is monkeypatched to a tmp dir so real files are written
without touching the workspace; the composer's LLM falls back to slice-echo on
a host (no network). No Docker / pydantic / fastapi needed.
"""

from __future__ import annotations

import json

import app.research.run as R
from app.research.citation import Citation, CitationStatus
from app.research.citation_verifier import VerificationReport
from app.autonomous_executor.driver import CommanderResult
from app.autonomous_executor.models import ExecutorStatus, StepStatus


def _run(*, compose=True, verify=False, experiment=False):
    run = R.build_research_run("q", compose=compose, verify=verify, experiment=experiment)
    run.transition(ExecutorStatus.RUNNING)
    return run


def _step(run, hint):
    return next(s for s in run.plan if s.crew_hint == hint)


def _complete(run, hint, text):
    step = _step(run, hint)
    step.status = StepStatus.COMPLETED
    step.result_text = text
    return step


def _adapter(*, enabled=True, compose_fn=None):
    return R.make_research_adapter(
        search_fn=lambda g: [],
        propose_fn=lambda q, **k: [],
        commander_fn=lambda s, r: CommanderResult(text="FALLBACK"),
        gate_fn=lambda **k: (None, ""),
        gate_output_fn=lambda **k: None,
        enabled_fn=lambda: False,
        citation_verification_enabled_fn=lambda: False,
        compose_enabled_fn=lambda: enabled,
        compose_fn=compose_fn,
        draft_fn=lambda p: "DRAFT",
    )


# ── Planner ───────────────────────────────────────────────────────────────────


def test_compose_flag_appends_step_after_gate():
    hints = [s.crew_hint for s in R.plan_research("q", compose=True)]
    assert hints == [R.HINT_LITERATURE, R.HINT_HYPOTHESES, R.HINT_INVESTIGATE, R.HINT_DRAFT, R.HINT_GATE, R.HINT_COMPOSE]
    assert hints.index(R.HINT_COMPOSE) > hints.index(R.HINT_GATE)  # paper made only after the gate


def test_verify_and_compose_full_order():
    hints = [s.crew_hint for s in R.plan_research("q", verify=True, compose=True)]
    assert hints == [
        R.HINT_LITERATURE, R.HINT_HYPOTHESES, R.HINT_INVESTIGATE,
        R.HINT_DRAFT, R.HINT_VERIFY, R.HINT_GATE, R.HINT_COMPOSE,
    ]


def test_default_plan_has_no_compose():
    assert R.HINT_COMPOSE not in [s.crew_hint for s in R.plan_research("q")]


# ── Adapter branch ─────────────────────────────────────────────────────────────


def test_compose_skipped_when_disabled():
    run = _run()
    out = _adapter(enabled=False, compose_fn=lambda *a, **k: {})(_step(run, R.HINT_COMPOSE), run)
    assert "skipped" in json.loads(out.text)
    assert not out.text.startswith("BLOCKED:")


def test_compose_calls_compose_fn_when_enabled():
    calls = []

    def cfn(run, *, verify_references_fn):
        calls.append(run.run_id)
        return {"paper_tex": "/x/paper.tex", "references_bib": None, "sections": 7, "references": 2, "warnings": 0}

    run = _run()
    out = _adapter(enabled=True, compose_fn=cfn)(_step(run, R.HINT_COMPOSE), run)
    assert calls
    assert json.loads(out.text)["paper_tex"] == "/x/paper.tex"
    assert any("paper.tex written" in n for n in run.notes)


def test_compose_isolated_on_exception():
    def boom(run, *, verify_references_fn):
        raise RuntimeError("render down")

    run = _run()
    out = _adapter(enabled=True, compose_fn=boom)(_step(run, R.HINT_COMPOSE), run)
    assert "unavailable" in out.text
    assert not out.text.startswith("BLOCKED:")


# ── Helpers: verified-citation reuse + artifact assembly ──────────────────────


def test_kept_citations_reuses_verify_step_without_reverifying():
    run = _run(verify=True)
    c = Citation(doi="10.1000/x", title="Real", status=CitationStatus.VERIFIED)
    _complete(run, R.HINT_VERIFY, json.dumps({"verdict": "clear", "citations": {}, "kept": [c.to_dict()]}))

    def must_not_run(_cits):
        raise AssertionError("compose must reuse the verify step, not re-verify")

    kept = R._kept_citations(run, verify_references_fn=must_not_run)
    assert [x.doi for x in kept] == ["10.1000/x"]
    assert kept[0].status is CitationStatus.VERIFIED  # round-trips through Citation.from_dict


def test_kept_citations_falls_back_to_extract_and_verify():
    run = _run()  # no verify step in the plan
    _complete(run, R.HINT_DRAFT, "We build on 10.1000/x in this work.")
    seen = {}

    def vrefs(cits):
        seen["cits"] = list(cits)
        return VerificationReport(verified=list(cits), ambiguous=[], dropped=[], kept=list(cits))

    kept = R._kept_citations(run, verify_references_fn=vrefs)
    assert [c.doi for c in kept] == ["10.1000/x"]   # extracted from the draft, then verified
    assert seen["cits"], "the extracted citations were fed to the verifier"


def test_artifacts_from_run_pulls_every_field():
    run = _run(experiment=True)
    _complete(run, R.HINT_LITERATURE, json.dumps([{"title": "Paper A", "id": "x"}]))
    _complete(run, R.HINT_HYPOTHESES, json.dumps([{"text": "H1", "rank": 1}, {"text": "H2", "rank": 2}]))
    _complete(
        run,
        R.HINT_RUN_EXPERIMENT,
        json.dumps({"ok": True, "result": {"ok": True, "returncode": 0, "stdout": "m=5", "stderr": "", "timed_out": False}}),
    )
    _complete(run, R.HINT_DRAFT, "Findings text here.")
    arts = R._artifacts_from_run(run, [])
    assert arts.question == "q"
    assert arts.literature[0]["title"] == "Paper A"
    assert arts.hypotheses == ["H1", "H2"]
    assert arts.findings == "Findings text here."
    assert "m=5" in arts.measurements


# ── Real compose + render to a tmp dir ────────────────────────────────────────


def test_compose_paper_for_run_writes_real_files(tmp_path, monkeypatch):
    run = _run(verify=True)
    _complete(run, R.HINT_DRAFT, "Background discussion of the question.")
    c = Citation(title="A Real Paper", authors=("Jane Doe",), year=2020, doi="10.1000/x", status=CitationStatus.VERIFIED)
    _complete(run, R.HINT_VERIFY, json.dumps({"verdict": "clear", "citations": {}, "kept": [c.to_dict()]}))
    monkeypatch.setattr(R, "_paper_output_dir", lambda run: str(tmp_path))

    result = R._compose_paper_for_run(run, verify_references_fn=lambda cits: None)
    assert result["sections"] == 7
    assert result["references"] == 1
    assert (tmp_path / "paper.tex").exists()
    bib = (tmp_path / "references.bib").read_text(encoding="utf-8")
    assert "@article{Doe2020" in bib


def test_end_to_end_run_emits_paper(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "_paper_output_dir", lambda run: str(tmp_path))
    run = R.build_research_run("speed of binary search", compose=True)
    adapter = R.make_research_adapter(
        search_fn=lambda g: [{"title": "TAOCP Vol 3", "id": "x"}],
        propose_fn=lambda q, **k: [{"text": "binary search wins at scale", "rank": 1}],
        commander_fn=lambda s, r: CommanderResult(text="FALLBACK"),
        gate_fn=lambda **k: (None, ""),
        investigate_fn=lambda p: "Investigation notes.",
        draft_fn=lambda p: "Findings draft.",
        gate_output_fn=lambda **k: None,
        enabled_fn=lambda: False,
        citation_verification_enabled_fn=lambda: False,
        compose_enabled_fn=lambda: True,  # compose ON → uses the real _compose_paper_for_run
    )
    R.run_to_completion(run, adapter=adapter)

    assert run.status is ExecutorStatus.COMPLETED
    assert _step(run, R.HINT_COMPOSE).status is StepStatus.COMPLETED
    assert (tmp_path / "paper.tex").exists()  # the run produced an actual paper
