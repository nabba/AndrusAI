"""Literature search — one callable surface over the two sources AndrusAI
already has: the local episteme KB (ChromaDB) and live arXiv.

This is the ``literature_search`` research step. It is a thin *composition*:

  * ``search_kb``    → ``EpistemeStore.query`` (semantic vector search over
                       the research KB).
  * ``search_arxiv`` → the arXiv ATOM internals already in
                       ``app.episteme.paper_pipeline`` (``_build_arxiv_query``
                       / ``_fetch_arxiv_atom`` / ``_parse_atom``), which the
                       daily paper-pipeline already uses but never exposed as
                       a callable search.
  * ``search_literature`` → both, merged into one ranked list.

Both backends are reached through a single injectable seam each (``store`` for
the KB; ``backend`` for arXiv) so the module is exercisable on a host without
the ChromaDB stack — the heavy ``app.episteme`` import only fires on the real
production path. Every public function is failure-isolated: a dead network or
an empty/unavailable KB yields ``[]``, never an exception.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ── Result type ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LiteratureHit:
    """One retrieved passage or paper.

    ``source`` is ``"kb"`` (local episteme passage) or ``"arxiv"`` (live
    paper). ``score`` is a 0–1 relevance for KB hits and ``None`` for arXiv
    (arXiv ranks by recency, not similarity). ``metadata`` carries the
    source-specific extras (KB chunk metadata; arXiv categories/published).
    """

    source: str
    id: str
    title: str
    text: str
    score: Optional[float] = None
    published: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "id": self.id,
            "title": self.title,
            "text": self.text,
            "score": self.score,
            "published": self.published,
            "metadata": dict(self.metadata),
        }


# ── KB search ───────────────────────────────────────────────────────────────


def search_kb(
    query: str,
    *,
    n_results: Optional[int] = None,
    min_score: Optional[float] = None,
    where: Optional[dict] = None,
    store=None,
) -> list[LiteratureHit]:
    """Semantic search over the local episteme KB.

    ``store`` is injectable (anything exposing ``.query(query_text, n_results,
    where_filter, min_score)`` → list of ``{text, metadata, score, id}``); when
    omitted the real ``EpistemeStore`` singleton is resolved lazily so callers
    on a host without ChromaDB can still import this module. Returns ``[]`` on
    any failure.
    """
    if not query or not query.strip():
        return []
    try:
        if store is None:
            from app.episteme import config
            from app.episteme.vectorstore import get_store

            store = get_store()
            if n_results is None:
                n_results = config.DEFAULT_TOP_K
            if min_score is None:
                min_score = config.MIN_RELEVANCE_SCORE
        if n_results is None:
            n_results = 5
        if min_score is None:
            min_score = 0.0

        raw = store.query(
            query,
            n_results=n_results,
            where_filter=where,
            min_score=min_score,
        )
    except Exception:
        logger.debug("literature.search_kb failed", exc_info=True)
        return []

    hits: list[LiteratureHit] = []
    for row in raw or []:
        meta = row.get("metadata") or {}
        hits.append(
            LiteratureHit(
                source="kb",
                id=str(row.get("id") or ""),
                title=str(meta.get("title") or meta.get("source_file") or "")[:300],
                text=str(row.get("text") or ""),
                score=row.get("score"),
                published=meta.get("date") or meta.get("published"),
                metadata=meta,
            )
        )
    return hits


# ── arXiv search ──────────────────────────────────────────────────────────

# Backend = (build_query, fetch, parse, default_categories). Production reads
# the four arXiv internals from paper_pipeline (one lazy import, in-container
# only); tests pass a fake 4-tuple so no ChromaDB/network is touched.
ArxivBackend = tuple[Callable, Callable, Callable, tuple]


def _default_arxiv_backend() -> ArxivBackend:
    from app.episteme.paper_pipeline import (  # lazy: pulls chromadb via episteme __init__
        _DEFAULT_CATEGORIES,
        _build_arxiv_query,
        _fetch_arxiv_atom,
        _parse_atom,
    )

    return _build_arxiv_query, _fetch_arxiv_atom, _parse_atom, _DEFAULT_CATEGORIES


def _query_terms(query: str) -> list[str]:
    """Split a free-text query into arXiv phrase terms.

    Comma-separated → one phrase term each (OR-joined by the query builder);
    no comma → the whole string is a single phrase term. Empty terms dropped.
    """
    if "," in query:
        terms = [t.strip() for t in query.split(",")]
    else:
        terms = [query.strip()]
    return [t for t in terms if t]


def search_arxiv(
    query: str,
    *,
    max_results: int = 8,
    lookback_days: int = 3650,
    categories: Optional[tuple] = None,
    backend: Optional[ArxivBackend] = None,
) -> list[LiteratureHit]:
    """Live arXiv search via the existing paper-pipeline internals.

    ``lookback_days`` defaults wide (~10y) so a literature search isn't
    silently clipped to recent submissions the way the daily pipeline is;
    pass a small value to restrict to fresh work. Returns ``[]`` on any
    failure (network, parse, empty feed).
    """
    terms = _query_terms(query)
    if not terms:
        return []
    try:
        build_query, fetch, parse, default_categories = backend or _default_arxiv_backend()
        cats = categories or default_categories
        atom = fetch(build_query(terms, cats), max_results)
        if not atom:
            return []
        records = parse(atom, lookback_days)
    except Exception:
        logger.debug("literature.search_arxiv failed", exc_info=True)
        return []

    hits: list[LiteratureHit] = []
    for rec in records or []:
        hits.append(
            LiteratureHit(
                source="arxiv",
                id=str(rec.get("id") or ""),
                title=str(rec.get("title") or ""),
                text=str(rec.get("abstract") or ""),
                score=None,
                published=rec.get("published"),
                metadata={"categories": rec.get("categories") or []},
            )
        )
    return hits[:max_results]


# ── Combined ────────────────────────────────────────────────────────────────


def search_literature(
    query: str,
    *,
    kb_n: int = 5,
    arxiv_n: int = 5,
    lookback_days: int = 3650,
    store=None,
    arxiv_backend: Optional[ArxivBackend] = None,
) -> list[LiteratureHit]:
    """Search both the local KB and arXiv; return KB hits first, then arXiv.

    Each source is isolated — one source failing (or being unavailable) never
    suppresses the other. Duplicate ids (same paper already in the KB) are
    dropped, keeping the first occurrence.
    """
    kb_hits = search_kb(query, n_results=kb_n, store=store) if kb_n > 0 else []
    arxiv_hits = (
        search_arxiv(
            query,
            max_results=arxiv_n,
            lookback_days=lookback_days,
            backend=arxiv_backend,
        )
        if arxiv_n > 0
        else []
    )

    seen: set[str] = set()
    merged: list[LiteratureHit] = []
    for hit in [*kb_hits, *arxiv_hits]:
        key = hit.id or f"{hit.source}:{hit.title}"
        if key in seen:
            continue
        seen.add(key)
        merged.append(hit)
    return merged
