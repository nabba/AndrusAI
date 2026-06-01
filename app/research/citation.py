"""app.research.citation — provenance-bearing academic citations + verification status.

The anti-hallucination core of the paper factory. Mirrors the Company Dossier
provenance model (``app.dossier.schema``'s ``Source`` / ``Confidence`` /
``DossierField``) in **pure stdlib**, because the dossier subsystem pulls
pydantic and the ``app.research`` spine is deliberately host-importable (the
same call ``source_ledger`` / ``decade_recall`` / ``conversation_memory`` make).
Field names match ``dossier.schema.Source`` so a later bridge is mechanical.

The discipline is the dossier's, applied to references instead of numbers: a
citation carries where it came from + how confident the match is + a
verification status; one that does not verify is **dropped** from a manuscript
exactly the way an unsupported number is dropped from a dossier section.

Pure stdlib + regex (no LLM, no network, no embedding) — the resolver backends
that actually hit the literature APIs live in ``app.research.literature_sources``;
this module is just the typed shape + the normalizers/similarity they share.
"""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


class Confidence(str, enum.Enum):
    """Calibrated match confidence — values + float mapping mirror
    ``app.dossier.schema.Confidence`` so the two domains reconcile identically."""

    EXACT = "exact"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    ESTIMATED = "estimated"

    def to_score(self) -> float:
        return {"exact": 1.0, "high": 0.9, "medium": 0.7, "low": 0.4, "estimated": 0.3}[self.value]


class CitationStatus(str, enum.Enum):
    """Outcome of verification. Only ``VERIFIED`` (and, by operator policy,
    ``AMBIGUOUS``) survive into a manuscript; ``UNVERIFIED`` / ``FABRICATED``
    are dropped."""

    VERIFIED = "verified"        # resolved against an authoritative source, strong match
    AMBIGUOUS = "ambiguous"      # resolved but a weak/near match — keep low-confidence or escalate
    UNVERIFIED = "unverified"    # no source could confirm it → drop
    FABRICATED = "fabricated"    # well-formed id/DOI that resolves to nothing → drop, loudly


@dataclass(frozen=True)
class Source:
    """Where a verified citation was confirmed. Field names match
    ``dossier.schema.Source`` (``document_id`` carries the DOI / arXiv id /
    OpenAlex work id, exactly as it carries a SEC accession there)."""

    adapter: str                 # "crossref" / "arxiv" / "openalex" / ...
    url: str = ""                # human-verifiable link
    document_id: str = ""        # DOI / arXiv id / OpenAlex id
    note: str = ""               # e.g. "title match 0.94"
    accessed_at: str = ""        # ISO-8601; stamped by the resolver

    def to_dict(self) -> dict:
        return {
            "adapter": self.adapter,
            "url": self.url,
            "document_id": self.document_id,
            "note": self.note,
            "accessed_at": self.accessed_at,
        }


@dataclass
class Citation:
    """One academic reference + its provenance + verification status."""

    raw: str = ""                          # the reference as it appeared in the draft
    title: str = ""
    authors: tuple[str, ...] = ()
    year: Optional[int] = None
    doi: str = ""
    arxiv_id: str = ""
    url: str = ""
    status: CitationStatus = CitationStatus.UNVERIFIED
    confidence: Confidence = Confidence.LOW
    source: Optional[Source] = None
    reason: str = ""                       # how it matched / why it failed

    def verified(self) -> bool:
        return self.status is CitationStatus.VERIFIED

    def to_dict(self) -> dict:
        return {
            "raw": self.raw,
            "title": self.title,
            "authors": list(self.authors),
            "year": self.year,
            "doi": self.doi,
            "arxiv_id": self.arxiv_id,
            "url": self.url,
            "status": self.status.value,
            "confidence": self.confidence.value,
            "source": self.source.to_dict() if self.source else None,
            "reason": self.reason,
        }


# ── Normalizers ──────────────────────────────────────────────────────────────

# Modern arXiv ids: 2305.12345 / 2305.12345v2. Old style: hep-th/0601001.
_ARXIV_NEW_RE = re.compile(r"\b(\d{4}\.\d{4,5})(v\d+)?\b")
_ARXIV_OLD_RE = re.compile(r"\b([a-z][a-z\-]*(?:\.[A-Z]{2})?/\d{7})(v\d+)?\b")
# DOI: 10.<registrant>/<suffix>. Stop at whitespace; trim trailing sentence punctuation.
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.IGNORECASE)
_DOI_TRAILING = re.compile(r"[.,;:)\]}>]+$")
_WORD_RE = re.compile(r"[a-z0-9]+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_arxiv_id(text: str) -> str:
    """Extract a canonical arXiv id (version stripped) from a string, or ''."""
    if not text:
        return ""
    s = str(text)
    # Strip a leading "arXiv:" label if present.
    s = re.sub(r"(?i)\barxiv:\s*", " ", s)
    m = _ARXIV_NEW_RE.search(s)
    if m:
        return m.group(1)
    m = _ARXIV_OLD_RE.search(s)
    if m:
        return m.group(1)
    return ""


def normalize_doi(text: str) -> str:
    """Extract a canonical, lower-cased DOI from a string, or ''."""
    if not text:
        return ""
    m = _DOI_RE.search(str(text))
    if not m:
        return ""
    doi = _DOI_TRAILING.sub("", m.group(1))
    return doi.lower()


def _title_tokens(title: str) -> set[str]:
    return set(_WORD_RE.findall((title or "").lower()))


def extract_citations(text: str) -> list["Citation"]:
    """Pull identifier-bearing citations (DOIs, arXiv ids) out of free text.

    Deliberately extracts only the reliably machine-verifiable identifiers — it
    does NOT try to parse author-year reference strings (a separate, fuzzy
    problem). Each unique DOI / arXiv id becomes one ``Citation`` for the
    verifier; deduped by identifier. This is what lets a finished draft be
    checked for fabricated references: every id it cites must resolve.
    """
    if not text:
        return []
    out: list[Citation] = []
    seen: set[tuple[str, str]] = set()
    for m in _DOI_RE.finditer(text):
        doi = _DOI_TRAILING.sub("", m.group(1)).lower()
        key = ("doi", doi)
        if doi and key not in seen:
            seen.add(key)
            out.append(Citation(raw=m.group(0).strip(), doi=doi))
    for rx in (_ARXIV_NEW_RE, _ARXIV_OLD_RE):
        for m in rx.finditer(text):
            aid = m.group(1)
            key = ("arxiv", aid)
            if key not in seen:
                seen.add(key)
                out.append(Citation(raw=m.group(0).strip(), arxiv_id=aid))
    return out


def title_similarity(a: str, b: str) -> float:
    """Token-Jaccard similarity of two titles in [0, 1] — order-insensitive and
    robust to punctuation/casing (the codebase's idiom; no embedding dep)."""
    ta, tb = _title_tokens(a), _title_tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def title_match_score(query: str, candidate: str) -> float:
    """How well a resolved ``candidate`` title matches a ``query`` title in [0, 1].

    ``max`` of two views, so it works for BOTH a clean structured query title
    and a noisy raw reference string (authors + title + venue):

      * Jaccard — symmetric overlap (good when both are clean titles).
      * Containment — fraction of the *candidate*'s tokens present in the query
        (good when the query carries extra author/venue tokens that would drag
        Jaccard down). Guarded: containment only counts for candidates of ≥4
        tokens, so a 1–2 word candidate can't spuriously "match" any long text.
    """
    q, c = _title_tokens(query), _title_tokens(candidate)
    if not q or not c:
        return 0.0
    inter = len(q & c)
    jaccard = inter / len(q | c)
    containment = (inter / len(c)) if len(c) >= 4 else 0.0
    return max(jaccard, containment)


__all__ = [
    "Confidence",
    "CitationStatus",
    "Source",
    "Citation",
    "normalize_arxiv_id",
    "normalize_doi",
    "extract_citations",
    "title_similarity",
    "title_match_score",
]
