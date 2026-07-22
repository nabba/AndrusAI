"""Host-safe tests for the manuscript composer (Phase C core).

``llm_call`` is injected, so the per-section composition runs with no LLM. The
load-bearing properties pinned here: section order, slice-only-facts
containment, the deterministic slice-echo fallback inventing nothing, the
fact-check flagging invented numbers, and references == the (verified)
citations passed in.
"""

from __future__ import annotations

from app.research.citation import Citation, CitationStatus
from app.research.manuscript import (
    DEFAULT_SECTIONS,
    Manuscript,
    ResearchArtifacts,
    compose_manuscript,
    _fact_check,
    _render_slice,
)


ARTIFACTS = ResearchArtifacts(
    question="Is binary search faster than linear scan for membership testing?",
    literature=[
        {"title": "Deep Residual Learning", "id": "arxiv:1512.03385"},
        {"title": "Attention Is All You Need", "id": "arxiv:1706.03762"},
    ],
    hypotheses=["Binary search outperforms linear scan above ~1000 elements."],
    findings="Binary search completed in 0.3 ms vs 12 ms for linear scan.",
    measurements="binary_ms=0.3 linear_ms=12",
    citations=[Citation(doi="10.1000/x", title="Real Paper", status=CitationStatus.VERIFIED)],
)


def test_compose_produces_all_sections_in_order():
    m = compose_manuscript(ARTIFACTS, llm_call=lambda p: "Section prose.")
    assert [s.title for s in m.sections] == [s.title for s in DEFAULT_SECTIONS]
    assert m.sections[0].title == "Abstract"
    assert m.title == ARTIFACTS.question


def test_references_are_the_passed_citations():
    m = compose_manuscript(ARTIFACTS, llm_call=lambda p: "x")
    assert [c.doi for c in m.references] == ["10.1000/x"]


def test_render_slice_containment():
    rel = _render_slice(ARTIFACTS, ("literature",))
    assert "Attention Is All You Need" in rel
    assert "binary_ms" not in rel  # Related Work must not see measurements
    res = _render_slice(ARTIFACTS, ("findings", "measurements"))
    assert "binary_ms=0.3" in res
    assert "Attention Is All You Need" not in res  # Results must not see the lit list


def test_slice_echo_fallback_invents_nothing():
    m = compose_manuscript(ARTIFACTS, llm_call=lambda p: "")  # force the fallback
    results = next(s for s in m.sections if s.title == "Results")
    assert "binary_ms=0.3" in results.prose          # echoes the real measurement
    assert results.fact_check_warnings == []          # echo only contains slice facts


def test_fact_check_flags_invented_number():
    warns = _fact_check("The method was 99% faster.", "Findings: it ran in 12 ms.")
    assert any("99" in w for w in warns)
    assert _fact_check("It ran in 12 ms.", "Findings: 12 ms recorded.") == []


def test_compose_flags_invented_number_in_a_section():
    m = compose_manuscript(ARTIFACTS, llm_call=lambda p: "We observed a 99% improvement.")
    assert any(s.fact_check_warnings for s in m.sections)  # 99% is in no section's slice


def test_empty_artifacts_still_composes():
    m = compose_manuscript(ResearchArtifacts(question="q"), llm_call=lambda p: "")
    assert len(m.sections) == len(DEFAULT_SECTIONS)
    assert m.references == []
    assert all(isinstance(s.prose, str) and s.prose for s in m.sections)


def test_llm_call_exception_falls_back_cleanly():
    def boom(_p):
        raise RuntimeError("llm down")

    m = compose_manuscript(ARTIFACTS, llm_call=boom)
    assert all(s.prose for s in m.sections)  # slice-echo filled every section


def test_to_dict_shape():
    m = compose_manuscript(ARTIFACTS, llm_call=lambda p: "x")
    d = m.to_dict()
    assert set(d) == {"title", "sections", "references", "word_count", "warnings"}
    assert d["sections"][0]["title"] == "Abstract"
    assert isinstance(d["word_count"], int)


def test_default_llm_unavailable_on_host_falls_back():
    # No llm_call → _default_llm_call hits the factory, unavailable on a bare
    # host → "" → slice-echo. Must still produce every section, never raise.
    m = compose_manuscript(ARTIFACTS)
    assert len(m.sections) == len(DEFAULT_SECTIONS)
    assert all(s.prose for s in m.sections)


def test_default_llm_call_uses_research_lifecycle_completion(monkeypatch):
    from app.research.manuscript import _default_llm_call

    captured = {}

    def focused(prompt, *, role, task_hint, max_tokens):
        captured.update({
            "prompt": prompt,
            "role": role,
            "task_hint": task_hint,
            "max_tokens": max_tokens,
        })
        return "Lifecycle-wrapped manuscript prose."

    monkeypatch.setattr("app.research.run._focused_completion", focused)

    assert _default_llm_call("section prompt") == "Lifecycle-wrapped manuscript prose."
    assert captured == {
        "prompt": "section prompt",
        "role": "writing",
        "task_hint": "research manuscript section",
        "max_tokens": 1200,
    }
