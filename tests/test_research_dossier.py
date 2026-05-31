"""Tests for app.research.dossier (Phase B — research run → PDF dossier).

Two tiers, split on dependency weight:

  * Assembly (``build_research_dossier``) is pure stdlib, so the bulk of these
    tests run on a bare host — they drive a research run with injected seams
    (no LLM / crewai / ChromaDB) or set step state directly, then assert the
    six sections and their content.
  * Render (``render_research_dossier``) needs reportlab + pydantic (importing
    ``app.dossier.typeset`` pulls pydantic via ``compose``/``schema``); those
    tests ``importorskip`` the heavy deps. The RuntimeError-on-no-reportlab
    path is forced by blanking ``typeset._RL_PACK``.

The synthesize crew-hint (the optional sixth step) is exercised through the
real adapter + driver so we prove it (a) is appended only on opt-in and (b) is
failure-isolated: the run completes even when the PDF toolchain is absent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

import app.research.dossier as D
import app.research.run as R
from app.autonomous_executor.driver import CommanderResult
from app.autonomous_executor.models import ExecutorStep, StepStatus


# ── Test doubles (mirror tests/test_research_run.py) ──────────────────────────


@dataclass
class _Hit:
    id: str
    title: str = ""
    text: str = ""
    source: str = "kb"
    published: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "text": self.text,
            "source": self.source,
            "published": self.published,
        }


@dataclass
class _Hyp:
    text: str
    rank: int = 1
    novelty: str = ""

    def to_dict(self) -> dict:
        return {"text": self.text, "rank": self.rank, "novelty": self.novelty}


def _make_seams(*, hits=(), hyps=(), gate=(None, ""), investigate_text="INV-NOTES", draft_text="DRAFT-BODY"):
    def search_fn(goal):
        return list(hits)

    def propose_fn(question, *, literature=None, **kw):
        return list(hyps)

    def commander_fn(step, run):
        return CommanderResult(
            text=investigate_text if step.crew_hint == R.HINT_INVESTIGATE else draft_text
        )

    def gate_fn(*, proposal_text, task_id, verdict):
        return gate

    return dict(search_fn=search_fn, propose_fn=propose_fn, commander_fn=commander_fn, gate_fn=gate_fn)


def _completed(run, hint: str, text: str) -> ExecutorStep:
    """Mark the step carrying ``hint`` COMPLETED with ``text`` — the unit
    boundary for assembly tests (no driver/state-machine coupling)."""
    step = next(s for s in run.plan if s.crew_hint == hint)
    step.status = StepStatus.COMPLETED
    step.result_text = text
    return step


def _section(dossier, key: str):
    return next(s for s in dossier.sections if s.key == key)


def _driven_run(question="does caching cut retrieval latency", **seam_kw):
    """A run driven to completion through the real adapter + driver."""
    run = R.build_research_run(question)
    R.run_to_completion(run, adapter=R.make_research_adapter(**_make_seams(**seam_kw)))
    return run


# ── Assembly: section structure ───────────────────────────────────────────────


def test_build_dossier_emits_six_sections_in_order():
    run = _driven_run(
        hits=[_Hit(id="kb1", title="Cache study")],
        hyps=[_Hyp(text="Caching cuts latency", rank=1)],
    )
    dossier = D.build_research_dossier(run)
    assert [s.key for s in dossier.sections] == [
        "summary",
        "literature",
        "hypotheses",
        "investigation",
        "findings",
        "gate",
    ]


def test_summary_section_carries_question_counts_and_leading_hypothesis():
    run = _driven_run(
        question="how to speed up retrieval",
        hits=[_Hit(id="a", title="t1"), _Hit(id="b", title="t2")],
        hyps=[_Hyp(text="leading hypothesis", rank=1), _Hyp(text="alt", rank=2)],
    )
    summary = _section(D.build_research_dossier(run), "summary").prose
    assert "how to speed up retrieval" in summary
    assert "2 source(s)" in summary
    assert "leading hypothesis" in summary


def test_literature_section_lists_titles_with_source_tag():
    run = _driven_run(hits=[_Hit(id="kb1", title="Retrieval under load", source="arxiv")])
    lit = _section(D.build_research_dossier(run), "literature").prose
    assert "Retrieval under load" in lit
    assert "arxiv" in lit


def test_hypotheses_section_lists_ranked_text():
    run = _driven_run(hyps=[_Hyp(text="Caching cuts latency", rank=1, novelty="NOVEL")])
    hyp = _section(D.build_research_dossier(run), "hypotheses").prose
    assert "Caching cuts latency" in hyp
    assert "NOVEL" in hyp


def test_investigation_and_findings_carry_step_text():
    run = _driven_run(investigate_text="p99 dropped 40%", draft_text="Caching reduced p99 [kb1].")
    dossier = D.build_research_dossier(run)
    assert "p99 dropped 40%" in _section(dossier, "investigation").prose
    assert "Caching reduced p99 [kb1]." in _section(dossier, "findings").prose


# ── Assembly: gate verdict → data-quality flag ────────────────────────────────


def test_gate_clear_has_no_warnings():
    run = _driven_run(gate=(None, "grounded"))
    gate = _section(D.build_research_dossier(run), "gate")
    assert gate.fact_check_warnings == []
    assert "cleared" in gate.prose.lower()


def test_gate_escalation_becomes_fact_check_warning():
    # Drive the gate step directly so the test is independent of how the
    # driver tips a BLOCKED run — the dossier reads the gate step's text.
    run = R.build_research_run("topic")
    _completed(run, R.HINT_DRAFT, "Latency fell 40%.")
    _completed(
        run,
        R.HINT_GATE,
        "BLOCKED: research-evidence gate escalated to peer_review. uncited claim",
    )
    dossier = D.build_research_dossier(run)
    gate = _section(dossier, "gate")
    assert dossier.gate_action == "peer_review"
    assert gate.fact_check_warnings, "escalation must surface as a flag"
    assert "peer_review" in gate.fact_check_warnings[0]
    assert "peer_review" in _section(dossier, "summary").prose


# ── Assembly: empty / partial run renders honest placeholders ─────────────────


def test_empty_run_assembles_with_placeholders_not_errors():
    run = R.build_research_run("untouched question")  # PLANNING, nothing done
    dossier = D.build_research_dossier(run)
    assert len(dossier.sections) == 6
    assert dossier.literature == []
    assert dossier.n_hypotheses == 0
    assert "No literature" in _section(dossier, "literature").prose
    assert "No hypotheses" in _section(dossier, "hypotheses").prose


def test_dossier_to_dict_shape():
    run = _driven_run(hits=[_Hit(id="a", title="t1")], hyps=[_Hyp(text="h1")])
    d = D.build_research_dossier(run).to_dict()
    assert set(d) == {
        "question",
        "status",
        "n_literature",
        "n_hypotheses",
        "gate_action",
        "gate_note",
        "sections",
    }
    assert d["n_literature"] == 1
    assert [s["key"] for s in d["sections"]] == [
        "summary",
        "literature",
        "hypotheses",
        "investigation",
        "findings",
        "gate",
    ]


# ── Synthesize crew-hint (the optional sixth step) ────────────────────────────


def test_plan_default_is_five_steps_no_synthesize():
    steps = R.plan_research("q")
    assert len(steps) == 5
    assert R.HINT_SYNTHESIZE not in [s.crew_hint for s in steps]


def test_plan_synthesize_appends_sixth_step():
    steps = R.plan_research("q", synthesize=True)
    assert len(steps) == 6
    assert steps[5].crew_hint == R.HINT_SYNTHESIZE


def test_build_research_run_synthesize_appends_sixth_step():
    run = R.build_research_run("q", synthesize=True)
    hints = [s.crew_hint for s in run.plan]
    assert len(hints) == 6
    assert hints[-1] == R.HINT_SYNTHESIZE


def test_build_research_run_default_omits_synthesize():
    run = R.build_research_run("q")
    assert R.HINT_SYNTHESIZE not in [s.crew_hint for s in run.plan]


def test_synthesize_step_is_failure_isolated_and_run_completes(tmp_path, monkeypatch):
    """The synthesize step needs gateway-only PDF deps. Whether or not they are
    present, the adapter must return a result (never raise) so the run reaches a
    terminal state. With reportlab+pydantic present it renders to tmp_path; on a
    bare host it records 'unavailable' — either way the step COMPLETES."""
    monkeypatch.setenv("DOSSIER_OUTPUT_DIR", str(tmp_path))
    run = R.build_research_run("does caching cut latency", synthesize=True)
    R.run_to_completion(
        run,
        adapter=R.make_research_adapter(
            **_make_seams(hits=[_Hit(id="a", title="t1")], hyps=[_Hyp(text="h1")])
        ),
    )
    synth = next(s for s in run.plan if s.crew_hint == R.HINT_SYNTHESIZE)
    assert synth.status is StepStatus.COMPLETED
    assert run.is_terminal


# ── Render (heavy deps; guarded) ──────────────────────────────────────────────


def test_render_writes_a_real_pdf(tmp_path):
    pytest.importorskip("pydantic")
    pytest.importorskip("reportlab")
    run = _driven_run(
        hits=[_Hit(id="kb1", title="Cache study", source="arxiv", published="2025")],
        hyps=[_Hyp(text="Caching cuts latency", rank=1)],
    )
    out = tmp_path / "research.pdf"
    path = D.render_research_dossier(run, output_path=str(out))
    assert path == out
    assert path.exists()
    assert path.read_bytes()[:4] == b"%PDF"
    assert path.stat().st_size > 1000  # a genuine multi-page document


def test_render_raises_runtime_error_without_reportlab(monkeypatch):
    pytest.importorskip("pydantic")  # needed to import typeset at all
    from app.dossier import typeset as T

    monkeypatch.setattr(T, "_RL_PACK", {}, raising=False)
    run = R.build_research_run("q")
    with pytest.raises(RuntimeError):
        D.render_research_dossier(run)


def test_render_default_filename_lands_in_output_dir(tmp_path, monkeypatch):
    pytest.importorskip("pydantic")
    pytest.importorskip("reportlab")
    monkeypatch.setenv("DOSSIER_OUTPUT_DIR", str(tmp_path))
    run = _driven_run()
    path = D.render_research_dossier(run)  # no output_path → default name
    assert path.parent == tmp_path
    assert path.name.startswith("research_")
    assert path.suffix == ".pdf"
    assert run.run_id in path.name


# ── Module wiring ─────────────────────────────────────────────────────────────


def test_module_exports():
    assert set(D.__all__) == {
        "ResearchSection",
        "ResearchDossier",
        "build_research_dossier",
        "render_research_dossier",
    }
