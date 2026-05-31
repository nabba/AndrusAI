"""Host-safe tests for app.research.hypothesis (Phase 2).

The ideation seam (``generate``) is injected everywhere so no LLM, crewai, or
ChromaDB is loaded. The grounding logic + record shaping are pure and exercised
with plain strings / dataclasses. One test wires the *real* headless generator
with a fake ``gather`` to prove the two research-layer steps compose.
"""

from __future__ import annotations

from dataclasses import dataclass

import app.research.hypothesis as H


# ── Test doubles ────────────────────────────────────────────────────────────


@dataclass
class _Idea:
    """Duck-types app.brainstorm.headless.Hypothesis."""

    text: str
    role: str = "researcher"
    novelty: str = "novel"
    aesthetic: float | None = None
    notes: list | None = None


@dataclass
class _Hit:
    """Duck-types app.research.literature.LiteratureHit."""

    id: str
    title: str = ""
    text: str = ""


def _gen_returning(*ideas):
    """A generate-seam that returns the given ideas and captures its kwargs."""
    captured: dict = {}

    def _gen(topic, **kwargs):
        captured["topic"] = topic
        captured.update(kwargs)
        return list(ideas)

    _gen.captured = captured  # type: ignore[attr-defined]
    return _gen


# ── Activation / no-op guards ────────────────────────────────────────────────


def test_empty_question_returns_empty():
    assert H.propose_hypotheses("", generate=_gen_returning(_Idea("x"))) == []
    assert H.propose_hypotheses("   ", generate=_gen_returning(_Idea("x"))) == []


def test_generator_exception_is_isolated():
    def boom(_topic, **_kw):
        raise RuntimeError("ideation down")

    assert H.propose_hypotheses("does X improve Y", generate=boom) == []


# ── Record shaping ────────────────────────────────────────────────────────────


def test_wraps_generator_into_ranked_records():
    gen = _gen_returning(
        _Idea("Caching cuts retrieval latency", role="coder", novelty="novel", aesthetic=0.8),
        _Idea("Bigger batches raise throughput", role="researcher", novelty="recombination"),
    )
    out = H.propose_hypotheses("how to speed up retrieval", generate=gen)
    assert [h.rank for h in out] == [1, 2]
    assert out[0].text == "Caching cuts retrieval latency"
    assert out[0].role == "coder"
    assert out[0].novelty == "novel"
    assert out[0].aesthetic == 0.8
    assert out[1].novelty == "recombination"


def test_blank_idea_text_skipped_and_ranks_stay_contiguous():
    gen = _gen_returning(_Idea("real one"), _Idea("   "), _Idea("real two"))
    out = H.propose_hypotheses("topic", generate=gen)
    assert [h.text for h in out] == ["real one", "real two"]
    assert [h.rank for h in out] == [1, 2]


def test_to_dict_shape():
    gen = _gen_returning(_Idea("a hypothesis", notes=["n1"]))
    out = H.propose_hypotheses("topic", generate=gen)
    d = out[0].to_dict()
    assert set(d) == {"text", "rank", "role", "novelty", "aesthetic", "grounded_in", "notes"}
    assert d["notes"] == ["n1"]


def test_none_idea_list_returns_empty():
    out = H.propose_hypotheses("topic", generate=lambda _t, **_k: None)
    assert out == []


# ── Prompt grounding ──────────────────────────────────────────────────────────


def test_prompt_contains_question_when_ungrounded():
    gen = _gen_returning(_Idea("x"))
    H.propose_hypotheses("does retrieval caching help", generate=gen)
    prompt = gen.captured["step_prompt"]
    assert "does retrieval caching help" in prompt
    assert "literature" not in prompt.lower()


def test_literature_woven_into_prompt():
    gen = _gen_returning(_Idea("x"))
    hits = [_Hit(id="kb1", title="Retrieval latency under load")]
    H.propose_hypotheses("speed up retrieval", literature=hits, generate=gen)
    prompt = gen.captured["step_prompt"]
    assert "literature" in prompt.lower()
    assert "Retrieval latency under load" in prompt


def test_prompt_caps_grounding_items():
    gen = _gen_returning(_Idea("x"))
    hits = [_Hit(id=f"k{i}", title=f"Paper number {i} about systems") for i in range(20)]
    H.propose_hypotheses("topic", literature=hits, generate=gen)
    prompt = gen.captured["step_prompt"]
    assert prompt.count("- Paper number") <= H._MAX_GROUNDING_ITEMS


# ── Per-hypothesis grounding provenance ───────────────────────────────────────


def test_grounding_overlap_links_matching_literature():
    gen = _gen_returning(_Idea("Test whether retrieval latency drops with caching"))
    hits = [
        _Hit(id="kb1", title="Retrieval latency under heavy load"),
        _Hit(id="kb2", title="Unrelated work on protein folding"),
    ]
    out = H.propose_hypotheses("speed up retrieval", literature=hits, generate=gen)
    assert out[0].grounded_in == ["kb1"]


def test_no_literature_means_empty_grounding():
    gen = _gen_returning(_Idea("Retrieval latency drops with caching"))
    out = H.propose_hypotheses("topic", generate=gen)
    assert out[0].grounded_in == []


def test_dict_literature_hits_supported():
    gen = _gen_returning(_Idea("Caching reduces retrieval latency markedly"))
    hits = [{"id": "d1", "title": "Retrieval latency study", "text": ""}]
    out = H.propose_hypotheses("topic", literature=hits, generate=gen)
    assert out[0].grounded_in == ["d1"]


def test_min_shared_terms_threshold_suppresses_weak_overlap():
    gen = _gen_returning(_Idea("Caching reduces retrieval latency"))
    hits = [_Hit(id="kb1", title="Retrieval throughput benchmarks")]  # shares only 'retrieval'
    out = H.propose_hypotheses("topic", literature=hits, generate=gen, min_shared_terms=2)
    assert out[0].grounded_in == []
    out2 = H.propose_hypotheses("topic", literature=hits, generate=gen, min_shared_terms=1)
    assert out2[0].grounded_in == ["kb1"]


def test_hit_without_id_is_never_grounded():
    gen = _gen_returning(_Idea("Retrieval latency caching study results"))
    hits = [_Hit(id="", title="Retrieval latency caching")]
    out = H.propose_hypotheses("topic", literature=hits, generate=gen)
    assert out[0].grounded_in == []


# ── Pure helpers ──────────────────────────────────────────────────────────────


def test_significant_tokens_filters_short_and_stopwords():
    toks = H._significant_tokens("The retrieval LATENCY is in a big with under system")
    assert "retrieval" in toks
    assert "latency" in toks
    assert "system" in toks
    assert "the" not in toks  # too short
    assert "with" not in toks  # stopword
    assert "under" not in toks  # stopword
    assert "big" not in toks  # too short
    assert "is" not in toks  # too short


# ── Composition with the real headless generator ──────────────────────────────


def test_composes_with_real_headless_via_fake_gather():
    from app.brainstorm import headless

    @dataclass
    class _Resp:
        role: str
        text: str
        error: object = None

    def fake_gather(**_kw):
        return [_Resp("researcher", "1. Cache embeddings to cut latency\n2. Shard the index")]

    def gen(topic, **kw):
        return headless.generate_hypotheses(topic, gather=fake_gather, **kw)

    out = H.propose_hypotheses("how to cut retrieval latency", generate=gen, n=5)
    assert len(out) == 2
    assert [h.rank for h in out] == [1, 2]
    assert any("Cache embeddings" in h.text for h in out)


# ── Package wiring ────────────────────────────────────────────────────────────


def test_module_listed_in_research_package():
    import app.research as research

    assert "hypothesis" in research.__all__


def test_module_exports():
    assert set(H.__all__) == {"ResearchHypothesis", "propose_hypotheses"}
