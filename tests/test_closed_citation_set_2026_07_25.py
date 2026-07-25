"""Draft and critique prompts must close the citation set.

The 2026-07-25 baseline (9/12 delivered) had two report-class failures, both
blocked by the anti-fabrication check with real-but-unretrieved organisation
homepages:

    BLOCKED: … final synthesis contains citation(s) not retrieved by this run:
      https://elfond.ee, https://keskkonnaagentuur.ee, https://www.eea.europa.eu
      https://ec.europa.eu/eurostat, https://news.err.ee, https://piimaliit.ee

The gate was right — those sources were not in the evidence set. The cause was
the prompts: ``_build_draft_prompt`` said "attribute it to its source (author,
link, or arXiv id)" and ``_build_critique_prompt`` said "Preserve real
URLs/identifiers", and neither forbade *introducing* a source absent from the
supplied evidence. The model was doing what it was asked.

These tests pin the instruction in both prompts. The critique prompt matters
just as much as the draft: the evidence gate inspects ``HINT_CRITIQUE`` in
preference to ``HINT_DRAFT``, so an editor free to re-add homepages would undo
the draft's discipline.
"""
import pytest


def _run_with_one_web_source():
    from app.autonomous_executor.models import ExecutorStatus
    from app.research.run import HINT_LITERATURE, build_research_run
    import json

    run = build_research_run("Estonian forest health over the years")
    step = next(s for s in run.plan if s.crew_hint == HINT_LITERATURE)
    step.status = ExecutorStatus.COMPLETED
    step.result_text = json.dumps([{
        "source": "web",
        "id": "https://keskkonnaagentuur.ee/et/uudised/metsa-aastaraamat-2023",
        "title": "Metsa aastaraamat 2023",
        "text": "A substantive fetched passage about forest cover. " * 6,
        "metadata": {
            "url": "https://keskkonnaagentuur.ee/et/uudised/metsa-aastaraamat-2023",
            "content_fetched": True,
        },
    }])
    return run


@pytest.mark.parametrize("builder_name", ["_build_draft_prompt", "_build_critique_prompt"])
def test_prompt_closes_the_citation_set(builder_name):
    rr = pytest.importorskip("app.research.run")

    prompt = getattr(rr, builder_name)(_run_with_one_web_source())
    lowered = prompt.lower()

    assert "citation rule" in lowered, (
        f"{builder_name} must carry an explicit citation rule"
    )
    # The instruction must forbid inventing links, and must say so about the
    # specific thing the model actually did — org homepages and a Sources
    # section — not just "cite your sources".
    assert "only set of sources" in lowered or "only set of" in lowered
    assert "homepage" in lowered, (
        "must explicitly rule out organisation homepages — that is the exact "
        "failure mode observed live"
    )
    assert "references" in lowered or "sources'" in lowered, (
        "must cover the References/Sources section, where the bad links appeared"
    )


@pytest.mark.parametrize("builder_name", ["_build_draft_prompt", "_build_critique_prompt"])
def test_prompt_offers_the_sanctioned_alternative(builder_name):
    """Forbidding without an alternative just produces uncited claims."""
    rr = pytest.importorskip("app.research.run")

    prompt = getattr(rr, builder_name)(_run_with_one_web_source())
    lowered = prompt.lower()

    assert "[s<n>]" in lowered, "must point at the [Sn] label as the citation form"
    assert "not retrieved" in lowered, (
        "must tell the model how to mention an unretrieved source safely"
    )


def test_draft_prompt_still_supplies_the_evidence_and_question():
    """The new rule must not have displaced the prompt's actual content."""
    rr = pytest.importorskip("app.research.run")

    prompt = rr._build_draft_prompt(_run_with_one_web_source())

    assert "Estonian forest health" in prompt
    assert "[S1]" in prompt, "the evidence list must still be rendered"
    assert "https://keskkonnaagentuur.ee/et/uudised/metsa-aastaraamat-2023" in prompt


def test_critique_prompt_still_supplies_the_draft():
    rr = pytest.importorskip("app.research.run")
    from app.autonomous_executor.models import ExecutorStatus
    from app.research.run import HINT_DRAFT

    run = _run_with_one_web_source()
    step = next(s for s in run.plan if s.crew_hint == HINT_DRAFT)
    step.status = ExecutorStatus.COMPLETED
    step.result_text = "A draft mentioning 2.3 million hectares [S1]."

    prompt = rr._build_critique_prompt(run)
    assert "2.3 million hectares" in prompt
    assert "adversarial research editor" in prompt.lower()


def test_a_compliant_draft_clears_the_gate_end_to_end():
    """The shape the prompts now ask for must actually pass the gate.

    Guards against fixing the prompt into a form the gate still rejects.
    """
    deep_path = pytest.importorskip("app.research.deep_path")

    run = _run_with_one_web_source()
    compliant = (
        "Estonia's forest area covers roughly 2.3 million hectares [S1].\n\n"
        "Growing stock has risen over the period [S1].\n\n"
        "Sources:\n"
        "[S1] Metsa aastaraamat 2023 — "
        "https://keskkonnaagentuur.ee/et/uudised/metsa-aastaraamat-2023\n"
        "Eesti Loodusfond — organisation named but not retrieved by this run."
    )

    action, note = deep_path._deep_evidence_gate_for(run)(
        proposal_text=compliant, task_id="task",
    )

    assert action is None, f"a compliant draft must clear the gate, got: {note}"


def test_the_observed_failure_shape_still_blocks():
    """The live 07-25 output must still be caught — the gate keeps its teeth."""
    deep_path = pytest.importorskip("app.research.deep_path")

    run = _run_with_one_web_source()
    padded = (
        "Estonia's forest area covers roughly 2.3 million hectares [S1].\n\n"
        "Sources:\n"
        "- https://elfond.ee\n"
        "- https://www.eea.europa.eu\n"
    )

    action, note = deep_path._deep_evidence_gate_for(run)(
        proposal_text=padded, task_id="task",
    )

    assert action == "verify"
    assert "not retrieved" in note
    assert "elfond.ee" in note or "eea.europa.eu" in note
