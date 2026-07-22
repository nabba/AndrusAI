"""Automatic deep-research activation and evidence-flow tests."""

from __future__ import annotations

from app.autonomous_executor.models import ExecutorStatus
from app.research.deep_path import (
    _deep_evidence_gate_for,
    _parse_query_plan,
    _usable_deep_evidence,
    assess_deep_research,
    collect_deep_evidence,
    execute_deep_research,
    promote_research_decisions,
)
from app.research.literature import LiteratureHit
from app.research.run import (
    HINT_CRITIQUE,
    HINT_DRAFT,
    HINT_LITERATURE,
    _build_critique_prompt,
    build_research_run,
)


def test_explicit_deep_research_clears_default_threshold() -> None:
    assessment = assess_deep_research(
        "Please do deep research on memory architectures.",
        difficulty=5,
        threshold=4,
    )
    assert assessment.use_deep
    assert assessment.score >= 4
    assert "explicit-depth request" in assessment.reasons


def test_simple_factual_lookup_stays_on_fast_research() -> None:
    assessment = assess_deep_research(
        "What is Estonia's population? Cite a source.",
        difficulty=3,
        threshold=4,
    )
    assert not assessment.use_deep


def test_complex_high_difficulty_synthesis_promotes(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.runtime_settings.get_deep_research_auto_enabled", lambda: True,
    )
    decisions = [{
        "crew": "research",
        "task": "compare evidence",
        "difficulty": 9,
    }]
    out = promote_research_decisions(
        decisions,
        user_input=(
            "Compare several competing approaches, evaluate their trade-offs, "
            "and recommend a direction with primary-source citations."
        ),
    )
    assert out[0]["crew"] == "deep_research"
    assert out[0]["deep_research_assessment"]["score"] >= 4


def test_matrix_research_is_never_replaced(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.runtime_settings.get_deep_research_auto_enabled", lambda: True,
    )
    decisions = [{
        "crew": "research",
        "task": "MATRIX TASK: call research_orchestrator(spec_json=...)",
        "difficulty": 9,
    }]
    out = promote_research_decisions(
        decisions,
        user_input="Do extensive deep research and populate all rows with sources.",
    )
    assert out[0]["crew"] == "research"


def test_critique_prompt_contains_full_evidence_not_titles_only() -> None:
    run = build_research_run("question", critique=True)
    lit_step = next(
        step for step in run.plan if step.crew_hint == HINT_LITERATURE
    )
    lit_step.status = ExecutorStatus.COMPLETED
    lit_step.result_text = (
        '[{"source":"web","id":"https://source.example",'
        '"title":"Source title","text":"Specific evidence excerpt",'
        '"metadata":{"url":"https://source.example"}}]'
    )
    draft_step = next(
        step for step in run.plan if step.crew_hint == HINT_DRAFT
    )
    draft_step.status = ExecutorStatus.COMPLETED
    draft_step.result_text = "Draft claim"

    prompt = _build_critique_prompt(run)

    assert "Specific evidence excerpt" in prompt
    assert "https://source.example" in prompt
    assert "Draft claim" in prompt
    assert any(step.crew_hint == HINT_CRITIQUE for step in run.plan)


def test_query_plan_parser_accepts_fenced_json_and_bounds_queries() -> None:
    parsed = _parse_query_plan(
        '```json\n["primary evidence one", "counter evidence two", '
        '"current evidence three", "ignored fourth"]\n```'
    )
    assert parsed == [
        "primary evidence one",
        "counter evidence two",
        "current evidence three",
    ]


def test_deep_evidence_searches_original_plus_subqueries_and_dedups() -> None:
    calls: list[str] = []

    def search(query: str) -> list[LiteratureHit]:
        calls.append(query)
        return [LiteratureHit(
            source="arxiv", id="shared" if len(calls) < 3 else "third",
            title="title", text=(f"evidence for {query}. " * 12),
        )]

    hits = collect_deep_evidence(
        "original question",
        planner_fn=lambda _q: ["focused query one", "focused query two"],
        search_fn=search,
    )
    assert calls == [
        "original question", "focused query one", "focused query two",
    ]
    assert [hit.id for hit in hits] == ["shared", "third"]


def test_synchronous_deep_path_returns_the_gated_critic_answer(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.research.deep_path.collect_deep_evidence",
        lambda _question: [LiteratureHit(
            source="web", id="https://authority.example/report",
            title="Authority report", text=("Primary evidence says 42. " * 8),
            metadata={
                "url": "https://authority.example/report",
                "content_fetched": True,
            },
        )],
    )
    monkeypatch.setattr(
        "app.research.hypothesis.propose_hypotheses",
        lambda *_args, **_kwargs: [{
            "text": "The evidence supports 42", "rank": 1,
        }],
    )

    def completion(_prompt, *, task_hint, **_kwargs):
        return {
            "research investigation": "Investigation grounded in S1.",
            "research findings draft": "Draft answer citing the report.",
            "research evidence critique and synthesis": (
                "Corrected final answer [Authority report]"
                "(https://authority.example/report)."
            ),
        }.get(task_hint, "")

    monkeypatch.setattr("app.research.run._focused_completion", completion)
    monkeypatch.setattr(
        "app.runtime_settings.get_research_citation_verification_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        "app.runtime_settings.get_deep_research_fusion_enabled", lambda: False,
    )
    monkeypatch.setattr("app.autonomous_executor.store.save", lambda _run: None)

    answer = execute_deep_research("What does the evidence show?")

    assert answer.startswith("Corrected final answer")
    assert "https://authority.example/report" in answer


def test_deep_gate_blocks_a_synthesis_untraceable_to_retrieved_sources() -> None:
    run = build_research_run("question")
    literature = next(
        step for step in run.plan if step.crew_hint == HINT_LITERATURE
    )
    literature.status = ExecutorStatus.COMPLETED
    literature.result_text = (
        '[{"source":"web","id":"https://source.example",'
        '"title":"Source","text":"Evidence repeated to form a substantive '
        'retrieved passage. Evidence repeated to form a substantive retrieved '
        'passage. Evidence repeated to form a substantive retrieved passage.",'
        '"metadata":{"url":"https://source.example","content_fetched":true}}]'
    )

    action, note = _deep_evidence_gate_for(run)(
        proposal_text="A polished but untraceable answer.",
        task_id=run.run_id,
    )

    assert action == "verify"
    assert "no identifier retrieved" in note


def test_deep_evidence_rejects_unfetched_web_snippets() -> None:
    hit = LiteratureHit(
        source="web",
        id="https://source.example/discovery-only",
        title="Discovery result",
        text="A long search snippet that repeats discovery metadata but was not " * 5,
        metadata={
            "url": "https://source.example/discovery-only",
            "content_fetched": False,
        },
    )

    hits = collect_deep_evidence(
        "original question",
        planner_fn=lambda _q: [],
        search_fn=lambda _q: [hit],
    )

    assert hits == []


def test_deep_evidence_rejects_a_malformed_kb_score() -> None:
    hit = LiteratureHit(
        source="kb",
        id="kb:result",
        title="Malformed scored result",
        text="A substantive knowledge-base excerpt with enough detail. " * 4,
        score="not-a-number",  # type: ignore[arg-type]
    )

    assert not _usable_deep_evidence(hit)


def _run_with_web_evidence() -> object:
    run = build_research_run("question")
    literature = next(
        step for step in run.plan if step.crew_hint == HINT_LITERATURE
    )
    literature.status = ExecutorStatus.COMPLETED
    literature.result_text = (
        '[{"source":"web","id":"https://source.example/report",'
        '"title":"Source","text":"A substantive fetched passage with enough '
        'detail to support the result. A substantive fetched passage with enough '
        'detail to support the result. A substantive fetched passage with detail.",'
        '"metadata":{"url":"https://source.example/report",'
        '"content_fetched":true}}]'
    )
    return run


def test_deep_gate_rejects_an_unretrieved_citation() -> None:
    action, note = _deep_evidence_gate_for(_run_with_web_evidence())(
        proposal_text=(
            "Supported source: https://source.example/report\n\n"
            "A second assertion cites https://invented.example/report."
        ),
        task_id="task",
    )

    assert action == "verify"
    assert "not retrieved" in note
    assert "invented.example" in note


def test_deep_gate_checks_empirical_paragraphs_independently() -> None:
    action, note = _deep_evidence_gate_for(_run_with_web_evidence())(
        proposal_text=(
            "The system achieved 99% accuracy.\n\n"
            "Source: https://source.example/report"
        ),
        task_id="task",
    )

    assert action == "verify"
    assert "uncited empirical" in note


def test_deep_gate_rejects_generic_source_label_for_empirical_claim() -> None:
    action, note = _deep_evidence_gate_for(_run_with_web_evidence())(
        proposal_text=(
            "The system achieved 99% accuracy. Source: unknown.\n\n"
            "Retrieved reference: https://source.example/report"
        ),
        task_id="task",
    )

    assert action == "verify"
    assert "uncited empirical" in note


def test_deep_gate_accepts_inline_retrieval_traced_empirical_claim() -> None:
    action, note = _deep_evidence_gate_for(_run_with_web_evidence())(
        proposal_text=(
            "The system achieved 99% accuracy according to "
            "https://source.example/report."
        ),
        task_id="task",
    )

    assert action is None
    assert "gate clear" in note
