"""app.research.literature_sources — resolver backends for citation verification.

One small, failure-isolated function per authoritative source. Each fetches the
record at a key (DOI / arXiv id) or the best title match, and returns a populated
:class:`~app.research.citation.Citation` *record* (metadata + ``source``) or
``None`` — it does NOT decide a verification *status*; that policy lives in one
place, :mod:`app.research.citation_verifier`.

HTTP is pure-stdlib ``urllib`` (mirroring ``app.dossier.adapters._base``'s
``http_get_json`` contract: NEVER raises, returns ``None``/``""`` on any
failure) so the research spine stays host-importable, and every resolver takes
an injectable transport (``get`` / ``get_text``) so the whole 4-layer verifier
is exercisable with no network.

Sources: arXiv (id), CrossRef + DataCite (DOI registrars), OpenAlex +
Semantic Scholar (title search). Priorities mirror the dossier hierarchy —
registrar-grade DOI authorities outrank aggregators.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Callable, Optional

from app.research.citation import Citation, Source, _now_iso, normalize_arxiv_id, normalize_doi

logger = logging.getLogger(__name__)

# Injectable transports for host tests. The real defaults hit the network.
JsonGet = Callable[[str], Optional[dict]]
TextGet = Callable[[str], Optional[str]]

_USER_AGENT = "AndrusAI-research/1.0"
_DEFAULT_TIMEOUT = 15

# Source priority (higher = more authoritative), mirroring the dossier hierarchy.
PRIORITY = {"crossref": 100, "datacite": 100, "arxiv": 90, "openalex": 80, "semantic_scholar": 70}


def http_get_json(url: str, *, timeout: int = _DEFAULT_TIMEOUT) -> Optional[dict]:
    """GET ``url`` and parse JSON. Never raises — returns ``None`` on any failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError, OSError):
        logger.debug("literature_sources: json GET failed: %s", url, exc_info=True)
        return None
    except Exception:  # defensive — a resolver must never crash the verifier
        logger.debug("literature_sources: json GET unexpected error: %s", url, exc_info=True)
        return None


def http_get_text(url: str, *, timeout: int = _DEFAULT_TIMEOUT) -> Optional[str]:
    """GET ``url`` and return text. Never raises — returns ``None`` on any failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        logger.debug("literature_sources: text GET failed: %s", url, exc_info=True)
        return None
    except Exception:
        logger.debug("literature_sources: text GET unexpected error: %s", url, exc_info=True)
        return None


# ── Small defensive extractors ────────────────────────────────────────────────


def _first_str(value) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def _int_or_none(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── Resolvers (each returns a record Citation or None; never raises) ──────────


def resolve_arxiv(arxiv_id: str, *, get_text: Optional[TextGet] = None) -> Optional[Citation]:
    """Resolve a (well-formed) arXiv id via the public ATOM API."""
    aid = normalize_arxiv_id(arxiv_id)
    if not aid:
        return None
    get_text = get_text or http_get_text
    raw = get_text(f"http://export.arxiv.org/api/query?id_list={urllib.parse.quote(aid)}&max_results=1")
    if not raw:
        return None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry = root.find("a:entry", ns)
    if entry is None:
        return None
    title_el = entry.find("a:title", ns)
    id_el = entry.find("a:id", ns)
    title = (title_el.text or "").strip() if title_el is not None else ""
    abs_url = (id_el.text or "").strip() if id_el is not None else f"https://arxiv.org/abs/{aid}"
    # arXiv returns an entry titled "Error" for malformed/empty id_list queries.
    if not title or title.lower() == "error":
        return None
    authors = tuple(
        (a.findtext("a:name", default="", namespaces=ns) or "").strip()
        for a in entry.findall("a:author", ns)
    )
    return Citation(
        title=title,
        arxiv_id=aid,
        url=abs_url,
        authors=tuple(a for a in authors if a),
        source=Source(adapter="arxiv", url=abs_url, document_id=aid, note="arxiv id resolved", accessed_at=_now_iso()),
    )


def resolve_crossref_doi(doi: str, *, get: Optional[JsonGet] = None) -> Optional[Citation]:
    """Resolve a DOI via CrossRef (registrar-grade)."""
    d = normalize_doi(doi) or (doi or "").strip().lower()
    if not d:
        return None
    get = get or http_get_json
    data = get(f"https://api.crossref.org/works/{urllib.parse.quote(d)}")
    msg = data.get("message") if isinstance(data, dict) else None
    if not isinstance(msg, dict):
        return None
    year = None
    issued = msg.get("issued") or {}
    if isinstance(issued, dict):
        parts = issued.get("date-parts") or []
        if parts and isinstance(parts, list) and parts[0]:
            year = _int_or_none(parts[0][0])
    authors = tuple(
        (f"{a.get('given', '')} {a.get('family', '')}".strip())
        for a in (msg.get("author") or [])
        if isinstance(a, dict)
    )
    return Citation(
        title=_first_str(msg.get("title")),
        doi=str(msg.get("DOI") or d),
        url=str(msg.get("URL") or f"https://doi.org/{d}"),
        year=year,
        authors=tuple(a for a in authors if a),
        source=Source(adapter="crossref", url=str(msg.get("URL") or f"https://doi.org/{d}"),
                      document_id=str(msg.get("DOI") or d), note="doi resolved", accessed_at=_now_iso()),
    )


def resolve_datacite_doi(doi: str, *, get: Optional[JsonGet] = None) -> Optional[Citation]:
    """Resolve a DOI via DataCite (registrar-grade; datasets/software/preprints)."""
    d = normalize_doi(doi) or (doi or "").strip().lower()
    if not d:
        return None
    get = get or http_get_json
    data = get(f"https://api.datacite.org/dois/{urllib.parse.quote(d)}")
    attrs = (data.get("data") or {}).get("attributes") if isinstance(data, dict) else None
    if not isinstance(attrs, dict):
        return None
    titles = attrs.get("titles") or []
    title = ""
    if isinstance(titles, list) and titles and isinstance(titles[0], dict):
        title = str(titles[0].get("title") or "")
    authors = tuple(
        str(c.get("name") or "").strip() for c in (attrs.get("creators") or []) if isinstance(c, dict)
    )
    url = str(attrs.get("url") or f"https://doi.org/{d}")
    return Citation(
        title=title,
        doi=str(attrs.get("doi") or d),
        url=url,
        year=_int_or_none(attrs.get("publicationYear")),
        authors=tuple(a for a in authors if a),
        source=Source(adapter="datacite", url=url, document_id=str(attrs.get("doi") or d),
                      note="doi resolved", accessed_at=_now_iso()),
    )


def search_openalex_title(title: str, *, get: Optional[JsonGet] = None) -> Optional[Citation]:
    """Best title match via OpenAlex (no API key required)."""
    t = (title or "").strip()
    if not t:
        return None
    get = get or http_get_json
    data = get(f"https://api.openalex.org/works?search={urllib.parse.quote(t)}&per-page=1")
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        return None
    r = results[0]
    doi = normalize_doi(str(r.get("doi") or ""))
    work_id = str(r.get("id") or "")
    authors = tuple(
        str((a.get("author") or {}).get("display_name") or "").strip()
        for a in (r.get("authorships") or [])
        if isinstance(a, dict)
    )
    return Citation(
        title=str(r.get("title") or ""),
        doi=doi,
        arxiv_id="",
        url=str(r.get("doi") or work_id),
        year=_int_or_none(r.get("publication_year")),
        authors=tuple(a for a in authors if a),
        source=Source(adapter="openalex", url=work_id, document_id=doi or work_id,
                      note="title search", accessed_at=_now_iso()),
    )


def search_semanticscholar_title(title: str, *, get: Optional[JsonGet] = None) -> Optional[Citation]:
    """Best title match via Semantic Scholar."""
    t = (title or "").strip()
    if not t:
        return None
    get = get or http_get_json
    url = (
        "https://api.semanticscholar.org/graph/v1/paper/search?"
        + urllib.parse.urlencode({"query": t, "limit": 1, "fields": "title,externalIds,url,year,authors"})
    )
    data = get(url)
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    r = rows[0]
    ext = r.get("externalIds") or {}
    doi = normalize_doi(str(ext.get("DOI") or "")) if isinstance(ext, dict) else ""
    arxiv = normalize_arxiv_id(str(ext.get("ArXiv") or "")) if isinstance(ext, dict) else ""
    authors = tuple(str(a.get("name") or "").strip() for a in (r.get("authors") or []) if isinstance(a, dict))
    page = str(r.get("url") or "")
    return Citation(
        title=str(r.get("title") or ""),
        doi=doi,
        arxiv_id=arxiv,
        url=page,
        year=_int_or_none(r.get("year")),
        authors=tuple(a for a in authors if a),
        source=Source(adapter="semantic_scholar", url=page, document_id=doi or arxiv,
                      note="title search", accessed_at=_now_iso()),
    )


__all__ = [
    "JsonGet",
    "TextGet",
    "PRIORITY",
    "http_get_json",
    "http_get_text",
    "resolve_arxiv",
    "resolve_crossref_doi",
    "resolve_datacite_doi",
    "search_openalex_title",
    "search_semanticscholar_title",
]
