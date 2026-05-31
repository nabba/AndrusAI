"""Host-safe tests for app.research.literature.

Both backends are injected (fake store / fake arXiv 4-tuple) so nothing here
needs ChromaDB or network — the production lazy imports are never reached.
"""

from __future__ import annotations

from app.research import literature as L
from app.research.literature import LiteratureHit


# ── fakes ───────────────────────────────────────────────────────────────────


class _FakeStore:
    def __init__(self, rows, *, raises=False):
        self._rows = rows
        self._raises = raises
        self.calls = []

    def query(self, query_text, n_results=5, where_filter=None, min_score=0.0):
        self.calls.append((query_text, n_results, where_filter, min_score))
        if self._raises:
            raise RuntimeError("boom")
        return list(self._rows)


def _fake_backend(records, *, fetch_returns="<atom/>", parse_raises=False):
    """Return a (build_query, fetch, parse, categories) 4-tuple of fakes."""
    captured = {}

    def build_query(terms, cats):
        captured["terms"] = terms
        captured["cats"] = cats
        return "QUERY"

    def fetch(query, max_results):
        captured["fetch_args"] = (query, max_results)
        return fetch_returns

    def parse(xml, lookback_days):
        captured["parse_args"] = (xml, lookback_days)
        if parse_raises:
            raise ValueError("bad xml")
        return list(records)

    backend = (build_query, fetch, parse, ("cs.AI", "cs.LG"))
    return backend, captured


# ── search_kb ─────────────────────────────────────────────────────────────


def test_search_kb_maps_rows_to_hits():
    store = _FakeStore([
        {"id": "epi_1", "text": "abc", "score": 0.81,
         "metadata": {"title": "Paper A", "date": "2026-01-01"}},
    ])
    hits = L.search_kb("agents", store=store)
    assert len(hits) == 1
    h = hits[0]
    assert isinstance(h, LiteratureHit)
    assert h.source == "kb"
    assert h.id == "epi_1"
    assert h.title == "Paper A"
    assert h.text == "abc"
    assert h.score == 0.81
    assert h.published == "2026-01-01"


def test_search_kb_title_falls_back_to_source_file():
    store = _FakeStore([
        {"id": "x", "text": "t", "score": 0.5, "metadata": {"source_file": "foo.pdf"}},
    ])
    hits = L.search_kb("q", store=store)
    assert hits[0].title == "foo.pdf"


def test_search_kb_empty_query_returns_empty():
    store = _FakeStore([{"id": "x", "text": "t", "metadata": {}}])
    assert L.search_kb("   ", store=store) == []
    assert store.calls == []  # never queried


def test_search_kb_failure_isolated():
    assert L.search_kb("q", store=_FakeStore([], raises=True)) == []


def test_search_kb_passes_explicit_params():
    store = _FakeStore([])
    L.search_kb("q", n_results=3, min_score=0.7, where={"k": "v"}, store=store)
    assert store.calls == [("q", 3, {"k": "v"}, 0.7)]


# ── _query_terms ────────────────────────────────────────────────────────────


def test_query_terms_single_phrase():
    assert L._query_terms("machine learning") == ["machine learning"]


def test_query_terms_comma_separated():
    assert L._query_terms("agents, planning, RL") == ["agents", "planning", "RL"]


def test_query_terms_drops_empty():
    assert L._query_terms(" , agents , ") == ["agents"]


# ── search_arxiv ────────────────────────────────────────────────────────────


def test_search_arxiv_maps_records():
    backend, captured = _fake_backend([
        {"id": "http://arxiv.org/abs/1", "title": "T1",
         "abstract": "A1", "published": "2026-05-01", "categories": ["cs.AI"]},
    ])
    hits = L.search_arxiv("agents", backend=backend)
    assert len(hits) == 1
    h = hits[0]
    assert h.source == "arxiv"
    assert h.id == "http://arxiv.org/abs/1"
    assert h.title == "T1"
    assert h.text == "A1"
    assert h.score is None
    assert h.published == "2026-05-01"
    assert h.metadata["categories"] == ["cs.AI"]
    # terms threaded into the builder
    assert captured["terms"] == ["agents"]


def test_search_arxiv_truncates_to_max_results():
    records = [
        {"id": str(i), "title": f"T{i}", "abstract": "a", "published": "2026-01-01"}
        for i in range(10)
    ]
    backend, _ = _fake_backend(records)
    hits = L.search_arxiv("q", max_results=3, backend=backend)
    assert len(hits) == 3


def test_search_arxiv_empty_feed_returns_empty():
    backend, _ = _fake_backend([], fetch_returns="")
    assert L.search_arxiv("q", backend=backend) == []


def test_search_arxiv_parse_failure_isolated():
    backend, _ = _fake_backend([], parse_raises=True)
    assert L.search_arxiv("q", backend=backend) == []


def test_search_arxiv_empty_query_returns_empty():
    backend, captured = _fake_backend([])
    assert L.search_arxiv("  ", backend=backend) == []
    assert "fetch_args" not in captured  # never fetched


# ── search_literature ───────────────────────────────────────────────────────


def test_search_literature_kb_first_then_arxiv():
    store = _FakeStore([
        {"id": "k1", "text": "kbtext", "score": 0.9, "metadata": {"title": "KB1"}},
    ])
    backend, _ = _fake_backend([
        {"id": "a1", "title": "AX1", "abstract": "ab", "published": "2026-01-01"},
    ])
    hits = L.search_literature("q", store=store, arxiv_backend=backend)
    assert [h.source for h in hits] == ["kb", "arxiv"]
    assert [h.id for h in hits] == ["k1", "a1"]


def test_search_literature_dedups_by_id():
    store = _FakeStore([
        {"id": "same", "text": "kb", "score": 0.9, "metadata": {"title": "KB"}},
    ])
    backend, _ = _fake_backend([
        {"id": "same", "title": "arxiv-dup", "abstract": "ab", "published": "2026-01-01"},
        {"id": "other", "title": "arxiv-2", "abstract": "ab", "published": "2026-01-01"},
    ])
    hits = L.search_literature("q", store=store, arxiv_backend=backend)
    ids = [h.id for h in hits]
    assert ids == ["same", "other"]
    # the kept "same" is the KB one (first occurrence)
    assert next(h for h in hits if h.id == "same").source == "kb"


def test_search_literature_one_source_failure_does_not_suppress_other():
    dead_store = _FakeStore([], raises=True)
    backend, _ = _fake_backend([
        {"id": "a1", "title": "AX1", "abstract": "ab", "published": "2026-01-01"},
    ])
    hits = L.search_literature("q", store=dead_store, arxiv_backend=backend)
    assert [h.source for h in hits] == ["arxiv"]


def test_search_literature_respects_zero_counts():
    store = _FakeStore([{"id": "k1", "text": "t", "score": 0.5, "metadata": {}}])
    backend, captured = _fake_backend([
        {"id": "a1", "title": "T", "abstract": "ab", "published": "2026-01-01"},
    ])
    # arxiv_n=0 → arXiv backend never consulted
    hits = L.search_literature("q", store=store, arxiv_n=0, arxiv_backend=backend)
    assert [h.source for h in hits] == ["kb"]
    assert "fetch_args" not in captured


# ── LiteratureHit ─────────────────────────────────────────────────────────


def test_hit_to_dict_round_trips_fields():
    h = LiteratureHit(source="kb", id="x", title="T", text="body",
                      score=0.42, published="2026-01-01", metadata={"a": 1})
    d = h.to_dict()
    assert d == {
        "source": "kb", "id": "x", "title": "T", "text": "body",
        "score": 0.42, "published": "2026-01-01", "metadata": {"a": 1},
    }
