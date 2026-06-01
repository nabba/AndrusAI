"""Host-safe tests for the 4-layer citation verifier (anti-fabrication core).

Every resolver is injected (a ``Resolvers`` with fakes), so no network runs —
the defaults would hit arXiv/CrossRef/DataCite/OpenAlex/Semantic Scholar, so
each test ALWAYS overrides all five via ``_resolvers(...)`` (unspecified ones
return None). Pure stdlib import (no pydantic/fastapi), so it runs on a plain host.
"""

from __future__ import annotations

import app.research.citation as C
from app.research.citation import Citation, CitationStatus, Confidence, Source
from app.research.citation_verifier import (
    Resolvers,
    verify_citation,
    verify_references,
)


# ── Fakes ─────────────────────────────────────────────────────────────────────


def _rec(adapter, *, title="", doi="", arxiv_id="", url="http://x", year=2017, authors=("A. Author",)):
    return Citation(
        title=title, doi=doi, arxiv_id=arxiv_id, url=url, year=year, authors=authors,
        source=Source(adapter=adapter, url=url, document_id=doi or arxiv_id, accessed_at="t"),
    )


def _resolvers(*, arxiv=None, crossref=None, datacite=None, openalex=None, semantic_scholar=None):
    """Build a Resolvers with ONLY the given fakes live; the rest return None
    (so a test never accidentally falls through to a real network resolver)."""
    none = lambda *a, **k: None  # noqa: E731
    return Resolvers(
        arxiv=arxiv or none,
        crossref=crossref or none,
        datacite=datacite or none,
        openalex=openalex or none,
        semantic_scholar=semantic_scholar or none,
    )


# ── Normalizers + scoring ─────────────────────────────────────────────────────


def test_normalize_arxiv_id():
    assert C.normalize_arxiv_id("arXiv:2305.12345v2") == "2305.12345"
    assert C.normalize_arxiv_id("see 2305.12345 for details") == "2305.12345"
    assert C.normalize_arxiv_id("hep-th/0601001") == "hep-th/0601001"
    assert C.normalize_arxiv_id("no id here") == ""


def test_normalize_doi():
    assert C.normalize_doi("https://doi.org/10.1145/3292500.3330701") == "10.1145/3292500.3330701"
    assert C.normalize_doi("DOI: 10.1038/nature12373.") == "10.1038/nature12373"  # trailing period trimmed
    assert C.normalize_doi("nothing") == ""


def test_title_match_score_containment_beats_jaccard_for_noisy_query():
    noisy = "Vaswani et al. 2017. Attention Is All You Need. In NeurIPS."
    clean = "Attention Is All You Need"
    assert C.title_match_score(noisy, clean) >= 0.85       # containment carries it
    assert C.title_similarity(noisy, clean) < 0.6          # raw Jaccard would have failed


def test_title_match_score_short_candidate_cannot_spuriously_match():
    # A 1–2 token candidate must NOT match an arbitrary long text via containment.
    assert C.title_match_score("a long sentence about transformers and attention", "attention") < 0.5


# ── extract_citations ─────────────────────────────────────────────────────────


def test_extract_citations_pulls_dois_and_arxiv_ids():
    text = (
        "We build on Transformers (arXiv:1706.03762) and ResNet "
        "(https://doi.org/10.1109/CVPR.2016.90). See also 2305.12345 and "
        "doi:10.1038/nature12373 for context."
    )
    keys = {(c.doi or c.arxiv_id) for c in C.extract_citations(text)}
    assert "1706.03762" in keys
    assert "2305.12345" in keys
    assert "10.1109/cvpr.2016.90" in keys
    assert "10.1038/nature12373" in keys


def test_extract_citations_skips_invalid_doi_and_dedups():
    text = "10.1/x is not a DOI. But 10.1000/y appears twice: 10.1000/y."
    dois = [c.doi for c in C.extract_citations(text) if c.doi]
    assert dois == ["10.1000/y"]  # one-digit registrant rejected; duplicate collapsed


def test_extract_citations_empty():
    assert C.extract_citations("") == []
    assert C.extract_citations("no identifiers here at all") == []


# ── Layer 1: arXiv id ─────────────────────────────────────────────────────────


def test_verifies_via_arxiv_id():
    out = verify_citation(
        Citation(arxiv_id="2305.12345", title="Attention Is All You Need"),
        resolvers=_resolvers(arxiv=lambda aid: _rec("arxiv", title="Attention Is All You Need", arxiv_id=aid)),
    )
    assert out.status is CitationStatus.VERIFIED
    assert out.confidence is Confidence.HIGH
    assert out.source.adapter == "arxiv"
    assert "arXiv id resolved" in out.reason


def test_unresolvable_arxiv_with_nothing_else_is_unverified():
    out = verify_citation(
        Citation(arxiv_id="2305.99999"),  # well-formed but resolver returns None
        resolvers=_resolvers(),  # everything misses
    )
    assert out.status is CitationStatus.UNVERIFIED
    assert "no authoritative source" in out.reason


# ── Layer 2: DOI ──────────────────────────────────────────────────────────────


def test_verifies_via_crossref_doi():
    out = verify_citation(
        Citation(doi="10.1145/3292500.3330701", title="Some Paper"),
        resolvers=_resolvers(crossref=lambda d: _rec("crossref", title="Some Paper", doi=d)),
    )
    assert out.status is CitationStatus.VERIFIED
    assert out.source.adapter == "crossref"


def test_falls_back_to_datacite_when_crossref_misses():
    out = verify_citation(
        Citation(doi="10.5281/zenodo.123", title="A Dataset"),
        resolvers=_resolvers(
            crossref=lambda d: None,
            datacite=lambda d: _rec("datacite", title="A Dataset", doi=d),
        ),
    )
    assert out.status is CitationStatus.VERIFIED
    assert out.source.adapter == "datacite"


def test_doi_resolves_but_title_disagrees_is_ambiguous():
    out = verify_citation(
        Citation(doi="10.1000/x", title="Neural Machine Translation by Jointly Learning"),
        resolvers=_resolvers(crossref=lambda d: _rec("crossref", title="An Unrelated Paper About Beetles", doi=d)),
    )
    assert out.status is CitationStatus.AMBIGUOUS
    assert "disagrees" in out.reason


# ── Layer 3: title search ─────────────────────────────────────────────────────


def test_strong_title_match_is_verified():
    out = verify_citation(
        Citation(title="Deep Residual Learning for Image Recognition"),
        resolvers=_resolvers(openalex=lambda t: _rec("openalex", title="Deep Residual Learning for Image Recognition", doi="10.1000/resnet")),
    )
    assert out.status is CitationStatus.VERIFIED
    assert out.doi == "10.1000/resnet"  # authoritative metadata merged in


def test_weak_title_match_is_ambiguous():
    out = verify_citation(
        Citation(title="Deep Residual Learning for Image Recognition Tasks"),
        resolvers=_resolvers(openalex=lambda t: _rec("openalex", title="Deep Residual Learning Networks")),
    )
    assert out.status is CitationStatus.AMBIGUOUS


def test_unrelated_top_hit_is_unverified():
    out = verify_citation(
        Citation(title="Quantum Error Correction Thresholds"),
        resolvers=_resolvers(openalex=lambda t: _rec("openalex", title="A Survey of Cat Memes Online")),
    )
    assert out.status is CitationStatus.UNVERIFIED


def test_title_search_falls_back_to_semantic_scholar():
    out = verify_citation(
        Citation(title="Attention Is All You Need"),
        resolvers=_resolvers(
            openalex=lambda t: None,
            semantic_scholar=lambda t: _rec("semantic_scholar", title="Attention Is All You Need", arxiv_id="1706.03762"),
        ),
    )
    assert out.status is CitationStatus.VERIFIED
    assert out.source.adapter == "semantic_scholar"


def test_noisy_raw_reference_matches_by_containment():
    out = verify_citation(
        Citation(raw="Vaswani et al. (2017). Attention Is All You Need. In NeurIPS, pp. 5998-6008."),
        resolvers=_resolvers(openalex=lambda t: _rec("openalex", title="Attention Is All You Need")),
    )
    assert out.status is CitationStatus.VERIFIED


# ── Layer 4: relevance ────────────────────────────────────────────────────────


def test_relevance_downgrades_verified_to_ambiguous():
    out = verify_citation(
        Citation(doi="10.1000/x", title="Some Paper"),
        resolvers=_resolvers(crossref=lambda d: _rec("crossref", title="Some Paper", doi=d)),
        relevance_fn=lambda cit, ctx: 0.2,
        context="a claim the paper does not actually support",
    )
    assert out.status is CitationStatus.AMBIGUOUS
    assert "low relevance" in out.reason


def test_high_relevance_keeps_verified():
    out = verify_citation(
        Citation(doi="10.1000/x", title="Some Paper"),
        resolvers=_resolvers(crossref=lambda d: _rec("crossref", title="Some Paper", doi=d)),
        relevance_fn=lambda cit, ctx: 0.95,
        context="a claim the paper supports",
    )
    assert out.status is CitationStatus.VERIFIED


# ── verify_references: partition + drop ───────────────────────────────────────


def test_verify_references_partitions_and_drops_unverified():
    rs = _resolvers(
        crossref=lambda d: _rec("crossref", title="Real", doi=d) if d == "10.1000/real" else None,
        openalex=lambda t: _rec("openalex", title="Weakish Title Match Here") if "weak" in t.lower() else None,
    )
    cites = [
        Citation(doi="10.1000/real", title="Real"),                       # → VERIFIED
        Citation(title="weak title match here approximately maybe"),    # → AMBIGUOUS (containment)
        Citation(title="totally fabricated nonexistent paper"),         # → UNVERIFIED (no resolver hit)
    ]
    report = verify_references(cites, resolvers=rs)
    assert len(report.verified) == 1
    assert len(report.ambiguous) == 1
    assert len(report.dropped) == 1
    assert len(report.kept) == 2  # verified + ambiguous survive by default
    s = report.summary()
    assert s["total"] == 3 and s["dropped"] == 1


def test_drop_ambiguous_moves_them_to_dropped():
    rs = _resolvers(openalex=lambda t: _rec("openalex", title="Deep Residual Learning Networks"))
    cites = [Citation(title="Deep Residual Learning for Image Recognition Tasks")]  # AMBIGUOUS
    report = verify_references(cites, resolvers=rs, drop_ambiguous=True)
    assert report.kept == []
    assert len(report.dropped) == 1


def test_verify_references_empty():
    report = verify_references([])
    assert report.summary()["total"] == 0
    assert report.kept == []


def test_verification_isolated_on_resolver_exception():
    def boom(_arg):
        raise RuntimeError("network on fire")

    report = verify_references([Citation(doi="10.1000/x", title="X")], resolvers=_resolvers(crossref=boom))
    assert len(report.dropped) == 1  # a raising resolver → UNVERIFIED, never a crash
    assert report.dropped[0].status is CitationStatus.UNVERIFIED
