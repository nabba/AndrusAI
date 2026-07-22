"""Host-safe validation for untrusted web-search result rows."""
from __future__ import annotations

import re
from urllib.parse import urlparse

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}", re.IGNORECASE)
_QUERY_STOPWORDS = frozenset({
    "about", "after", "against", "also", "among", "before", "between",
    "could", "does", "from", "have", "into", "more", "most", "other",
    "please", "research", "should", "than", "that", "their", "these",
    "this", "those", "using", "what", "when", "where", "which", "with",
    "would", "your",
})
_ADULT_HOST_MARKERS = frozenset({
    "pornhub", "xnxx", "xvideos", "redtube", "youporn", "onlyfans",
})
_ADULT_QUERY_TERMS = frozenset({
    "adult", "erotic", "porn", "pornography", "sex", "sexual", "sexwork",
})


def _terms(text: str) -> set[str]:
    """Return significant lowercase terms for deterministic relevance checks."""
    return {
        token.lower()
        for token in _TOKEN_RE.findall(text or "")
        if token.lower() not in _QUERY_STOPWORDS
    }


def validate_search_results(
    query: str,
    rows: list[dict],
    *,
    backend: str,
) -> tuple[list[dict], int]:
    """Reject unsafe, malformed, or lexically unrelated search rows."""
    query_terms = _terms(query)
    adult_query = bool(query_terms & _ADULT_QUERY_TERMS)
    accepted: list[dict] = []
    rejected = 0

    for raw in rows or []:
        row = dict(raw or {})
        url = str(row.get("url") or "").strip()
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
        except ValueError:
            parsed = None
            host = ""

        if parsed is None or parsed.scheme not in {"http", "https"} or not host:
            rejected += 1
            continue
        if not adult_query and any(marker in host for marker in _ADULT_HOST_MARKERS):
            rejected += 1
            continue

        candidate = " ".join((
            str(row.get("title") or ""),
            str(row.get("description") or row.get("snippet") or ""),
            host.replace(".", " "),
            parsed.path.replace("/", " "),
        ))
        overlap = query_terms & _terms(candidate)
        if query_terms and not overlap:
            rejected += 1
            continue

        row["search_backend"] = str(row.get("search_backend") or backend)
        row["query_term_overlap"] = len(overlap)
        row["source_quality"] = (
            "primary-candidate"
            if host.endswith((".gov", ".edu"))
            or host in {"arxiv.org", "www.arxiv.org", "doi.org"}
            else "unrated"
        )
        accepted.append(row)

    return accepted, rejected


__all__ = ["validate_search_results"]
