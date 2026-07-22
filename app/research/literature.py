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
from typing import Callable, Iterable, Optional

from app.research.lifecycle import invoke_research_tool

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

        raw = invoke_research_tool(
            "research_kb_search",
            {
                "query": query,
                "n_results": n_results,
                "where_filter": where,
                "min_score": min_score,
            },
            lambda args: store.query(
                args.get("query", query),
                n_results=int(args.get("n_results", n_results)),
                where_filter=args.get("where_filter", where),
                min_score=float(args.get("min_score", min_score)),
            ),
            task_description=f"Research KB search: {query}",
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
        arxiv_query = build_query(terms, cats)
        atom = invoke_research_tool(
            "research_arxiv_search",
            {"query": arxiv_query, "max_results": max_results},
            lambda args: fetch(
                str(args.get("query", arxiv_query)),
                int(args.get("max_results", max_results)),
            ),
            task_description=f"arXiv search: {query}",
        )
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


def search_web(
    query: str,
    *,
    max_results: int = 8,
    backend: Optional[Callable[[str, int], list[dict]]] = None,
) -> list[LiteratureHit]:
    """Search the live web and retain URL-bearing evidence snippets.

    This is deliberately separate from :func:`search_literature`: the original
    Phase-3 contract remains KB + arXiv, while the synchronous deep-research
    path can add current, non-academic primary sources.  Returned snippets are
    evidence candidates, not automatically trusted facts.
    """
    if not query or not query.strip() or max_results <= 0:
        return []
    try:
        if backend is None:
            from app.tools.web_search import search_brave

            backend = search_brave
        rows = invoke_research_tool(
            "research_web_search",
            {"query": query, "count": max_results},
            lambda args: backend(
                str(args.get("query", query)),
                int(args.get("count", max_results)),
            ),
            task_description=f"Web search: {query}",
        ) or []
        # Custom/injected backends must meet the same contract as the default
        # cascade.  This prevents a fallback provider from bypassing the
        # relevance and sensitive-domain checks merely because it is called
        # through the research facade.
        from app.tools.search_validation import validate_search_results

        rows, rejected = validate_search_results(
            query, list(rows), backend="research-backend",
        )
        if rejected:
            logger.warning(
                "literature.search_web rejected %d irrelevant/unsafe result(s)",
                rejected,
            )
    except Exception:
        logger.debug("literature.search_web failed", exc_info=True)
        return []

    hits: list[LiteratureHit] = []
    for row in rows[:max_results]:
        url = str(row.get("url") or "").strip()
        title = str(row.get("title") or "").strip()
        snippet = str(
            row.get("description") or row.get("snippet") or row.get("text") or ""
        ).strip()
        if not (url or title or snippet):
            continue
        hits.append(
            LiteratureHit(
                source="web",
                id=url or f"web:{title}",
                title=title,
                text=snippet,
                metadata={
                    "url": url,
                    "search_backend": row.get("search_backend"),
                    "query_term_overlap": row.get("query_term_overlap"),
                    "source_quality": row.get("source_quality"),
                },
            )
        )
    return hits


def _default_fetch_backend(url: str) -> str:
    """Fetch one public page through the existing SSRF-hardened tool."""
    from app.tools.web_fetch import web_fetch

    fn = getattr(web_fetch, "func", None)
    if callable(fn):
        return str(fn(url) or "")
    runner = getattr(web_fetch, "run", None)
    if callable(runner):
        return str(runner(url) or "")
    return ""


def enrich_web_hits(
    hits: Iterable[LiteratureHit],
    *,
    max_fetch: int = 4,
    max_chars: int = 6000,
    backend: Optional[Callable[[str], str]] = None,
) -> list[LiteratureHit]:
    """Replace search snippets with bounded, fetched primary page text.

    Search-result snippets are discovery metadata, not sufficient evidence for
    a deep-research answer. This pass reads the highest-ranked HTTPS pages
    through the project's SSRF-protected fetcher. Fetch failures retain the
    original snippet and never erase a result.
    """
    fetch = backend or _default_fetch_backend
    remaining = max(0, int(max_fetch))
    out: list[LiteratureHit] = []
    failure_prefixes = (
        "fetch error:", "url blocked:", "redirect blocked:",
    )
    for hit in hits:
        url = str(hit.metadata.get("url") or hit.id or "").strip()
        metadata = dict(hit.metadata)
        fetched = ""
        if hit.source == "web" and remaining > 0 and url.startswith("https://"):
            remaining -= 1
            try:
                fetched = str(invoke_research_tool(
                    "research_web_fetch",
                    {"url": url},
                    lambda args: fetch(str(args.get("url", url))),
                    task_description=f"Fetch research source: {url}",
                ) or "").strip()
            except Exception:
                logger.debug(
                    "literature.enrich_web_hits failed for %s", url,
                    exc_info=True,
                )
            if fetched.lower().startswith(failure_prefixes):
                fetched = ""
            metadata["content_fetched"] = bool(fetched)
        text = fetched[:max_chars] if fetched else hit.text
        out.append(
            LiteratureHit(
                source=hit.source,
                id=hit.id,
                title=hit.title,
                text=text,
                score=hit.score,
                published=hit.published,
                metadata=metadata,
            )
        )
    return out


def search_deep_sources(
    query: str,
    *,
    kb_n: int = 5,
    arxiv_n: int = 5,
    web_n: int = 8,
    fetch_n: int = 4,
    store=None,
    arxiv_backend: Optional[ArxivBackend] = None,
    web_backend: Optional[Callable[[str, int], list[dict]]] = None,
    fetch_backend: Optional[Callable[[str], str]] = None,
) -> list[LiteratureHit]:
    """Combine KB, arXiv, and fetched live-web evidence for deep research."""
    literature = search_literature(
        query,
        kb_n=kb_n,
        arxiv_n=arxiv_n,
        store=store,
        arxiv_backend=arxiv_backend,
    )
    web = enrich_web_hits(
        search_web(query, max_results=web_n, backend=web_backend),
        max_fetch=fetch_n,
        backend=fetch_backend,
    )
    seen = {hit.id or f"{hit.source}:{hit.title}" for hit in literature}
    merged = list(literature)
    for hit in web:
        key = hit.id or f"{hit.source}:{hit.title}"
        if key not in seen:
            seen.add(key)
            merged.append(hit)
    return merged
