"""app.research.citation_verifier — 4-layer citation verification (anti-fabrication core).

Given a :class:`~app.research.citation.Citation`, confirm it against authoritative
sources and assign a verification status. A citation that no source can confirm
ends ``UNVERIFIED`` and is **dropped** — the same discipline the dossier composer
applies to an unsupported number, here enforced for references.

The four layers, tried in descending key-strength (first record wins):

  1. **arXiv id** — a well-formed id resolved via the arXiv API.
  2. **DOI** — resolved via CrossRef, then DataCite (registrar-grade keys).
  3. **Title** — best match from OpenAlex, then Semantic Scholar, thresholded
     by :func:`citation.title_match_score` (STRONG → VERIFIED, WEAK → AMBIGUOUS).
  4. **Relevance** (optional, gated, injectable) — an LLM judges whether the
     now-resolved paper is actually relevant to the context it backs; a real but
     irrelevant citation is downgraded (padding ≠ support).

Every resolver + the relevance judge is injected (defaults hit the network /
the LLM factory), so the whole verifier runs with no network in tests. Parallel
verification reuses the dossier collector's bounded-pool + failure-isolation
pattern in stdlib ``concurrent.futures``.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from typing import Callable, Iterable, Optional

from app.research import literature_sources as LS
from app.research.citation import (
    Citation,
    CitationStatus,
    Confidence,
    normalize_arxiv_id,
    normalize_doi,
    title_match_score,
)

logger = logging.getLogger(__name__)

# Title-match thresholds + the id/DOI title-disagreement floor + relevance floor.
_STRONG = 0.85
_WEAK = 0.60
_MISMATCH = 0.30
_RELEVANCE_MIN = 0.50


@dataclass(frozen=True)
class Resolvers:
    """The five resolver backends, injected so the verifier is host-testable."""

    arxiv: Callable = LS.resolve_arxiv
    crossref: Callable = LS.resolve_crossref_doi
    datacite: Callable = LS.resolve_datacite_doi
    openalex: Callable = LS.search_openalex_title
    semantic_scholar: Callable = LS.search_semanticscholar_title


DEFAULT_RESOLVERS = Resolvers()


@dataclass
class VerificationReport:
    """Outcome of verifying a set of references."""

    verified: list[Citation] = field(default_factory=list)
    ambiguous: list[Citation] = field(default_factory=list)
    dropped: list[Citation] = field(default_factory=list)
    kept: list[Citation] = field(default_factory=list)  # what survives into the manuscript

    def summary(self) -> dict:
        total = len(self.verified) + len(self.ambiguous) + len(self.dropped)
        return {
            "total": total,
            "verified": len(self.verified),
            "ambiguous": len(self.ambiguous),
            "dropped": len(self.dropped),
            "kept": len(self.kept),
            "verified_rate": round(len(self.verified) / total, 3) if total else 0.0,
        }


# ── Helpers ────────────────────────────────────────────────────────────────


def _safe(fn: Callable, arg: str) -> Optional[Citation]:
    """Call a resolver, treating any failure as 'no record' (resolvers are
    already failure-isolated, but the verifier must never crash on one)."""
    try:
        rec = fn(arg)
        return rec if isinstance(rec, Citation) else None
    except Exception:
        logger.debug("citation_verifier: resolver %s raised", getattr(fn, "__name__", fn), exc_info=True)
        return None


def _title_from_raw(raw: str) -> str:
    """Best-effort title query from a raw reference string: strip the DOI / arXiv
    id / URLs / labels, leave the rest. The title layer's containment scoring
    tolerates the residual author/venue tokens."""
    s = (raw or "").strip()
    if not s:
        return ""
    doi = normalize_doi(s)
    aid = normalize_arxiv_id(s)
    if doi:
        s = s.replace(doi, " ")
    if aid:
        s = s.replace(aid, " ")
    s = re.sub(r"(?i)\b(doi|arxiv)\b[:\s]*", " ", s)
    s = re.sub(r"https?://\S+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _merge(c: Citation, rec: Citation, status: CitationStatus, conf: Confidence, reason: str) -> Citation:
    """Final citation = the source's authoritative metadata + the original raw +
    the verdict. The resolved record wins on every field it actually populated."""
    return Citation(
        raw=c.raw or rec.title,
        title=rec.title or c.title,
        authors=rec.authors or c.authors,
        year=rec.year or c.year,
        doi=rec.doi or c.doi,
        arxiv_id=rec.arxiv_id or c.arxiv_id,
        url=rec.url or c.url,
        status=status,
        confidence=conf,
        source=rec.source,
        reason=reason,
    )


def _apply_relevance(result: Citation, relevance_fn: Optional[Callable], context: str) -> Citation:
    """Layer 4 — downgrade a verified-but-irrelevant citation one level."""
    if relevance_fn is None or not context or result.status not in (CitationStatus.VERIFIED, CitationStatus.AMBIGUOUS):
        return result
    try:
        score = float(relevance_fn(result, context))
    except Exception:
        logger.debug("citation_verifier: relevance_fn raised", exc_info=True)
        return result
    if score >= _RELEVANCE_MIN:
        return result
    note = f"{result.reason}; low relevance {score:.2f}"
    if result.status is CitationStatus.VERIFIED:
        return replace(result, status=CitationStatus.AMBIGUOUS, confidence=Confidence.LOW, reason=note)
    return replace(result, status=CitationStatus.UNVERIFIED, reason=note)


def _from_key_record(c: Citation, rec: Citation, *, key: str, relevance_fn, context: str) -> Citation:
    """An exact-key (arXiv/DOI) resolve is VERIFIED — unless the cited title
    flatly disagrees with the resolved record (a wrong-title-on-a-real-DOI
    fabrication), which is AMBIGUOUS for an operator to eyeball."""
    if c.title and rec.title and title_match_score(c.title, rec.title) < _MISMATCH:
        merged = _merge(c, rec, CitationStatus.AMBIGUOUS, Confidence.LOW,
                        f"{key} resolved via {rec.source.adapter} but cited title disagrees")
    else:
        merged = _merge(c, rec, CitationStatus.VERIFIED, Confidence.HIGH,
                        f"{key} resolved via {rec.source.adapter}")
    return _apply_relevance(merged, relevance_fn, context)


def verify_citation(
    c: Citation,
    *,
    resolvers: Resolvers = DEFAULT_RESOLVERS,
    relevance_fn: Optional[Callable] = None,
    context: str = "",
) -> Citation:
    """Verify one citation through the four layers. Returns a NEW Citation with a
    final status/confidence/source/reason; never mutates the input, never raises."""
    # Layer 1 — arXiv id
    aid = normalize_arxiv_id(c.arxiv_id) or normalize_arxiv_id(c.raw)
    if aid:
        rec = _safe(resolvers.arxiv, aid)
        if rec is not None:
            return _from_key_record(c, rec, key="arXiv id", relevance_fn=relevance_fn, context=context)

    # Layer 2 — DOI (CrossRef, then DataCite)
    doi = normalize_doi(c.doi) or normalize_doi(c.raw)
    if doi:
        for fn in (resolvers.crossref, resolvers.datacite):
            rec = _safe(fn, doi)
            if rec is not None:
                return _from_key_record(c, rec, key="DOI", relevance_fn=relevance_fn, context=context)

    # Layer 3 — title search (OpenAlex, then Semantic Scholar)
    title = (c.title or "").strip() or _title_from_raw(c.raw)
    if title:
        for fn in (resolvers.openalex, resolvers.semantic_scholar):
            rec = _safe(fn, title)
            if rec is None or not rec.title:
                continue
            score = title_match_score(title, rec.title)
            if score >= _STRONG:
                return _apply_relevance(
                    _merge(c, rec, CitationStatus.VERIFIED, Confidence.HIGH, f"title match {score:.2f} via {rec.source.adapter}"),
                    relevance_fn, context,
                )
            if score >= _WEAK:
                return _apply_relevance(
                    _merge(c, rec, CitationStatus.AMBIGUOUS, Confidence.MEDIUM, f"weak title match {score:.2f} via {rec.source.adapter}"),
                    relevance_fn, context,
                )
            # an unrelated top hit — try the next source before giving up

    return replace(
        c,
        status=CitationStatus.UNVERIFIED,
        confidence=Confidence.LOW,
        reason="no authoritative source confirmed this reference",
    )


def verify_references(
    citations: Iterable[Citation],
    *,
    resolvers: Resolvers = DEFAULT_RESOLVERS,
    relevance_fn: Optional[Callable] = None,
    context: str = "",
    max_parallel: int = 6,
    drop_ambiguous: bool = False,
) -> VerificationReport:
    """Verify many citations in parallel and partition them.

    ``kept`` is what survives into a manuscript: VERIFIED always, AMBIGUOUS too
    unless ``drop_ambiguous`` (a near-match is probably real — flag, don't
    discard). UNVERIFIED is always dropped — that IS the anti-hallucination.
    Failure-isolated per citation: a verification that raises becomes UNVERIFIED.
    """
    cits = list(citations)
    if not cits:
        return VerificationReport()

    results: list[Optional[Citation]] = [None] * len(cits)

    def _work(c: Citation) -> Citation:
        try:
            return verify_citation(c, resolvers=resolvers, relevance_fn=relevance_fn, context=context)
        except Exception:
            logger.debug("citation_verifier: verify_citation raised", exc_info=True)
            return replace(c, status=CitationStatus.UNVERIFIED, reason="verification raised")

    with ThreadPoolExecutor(max_workers=max(1, min(max_parallel, len(cits))), thread_name_prefix="cite-verify") as pool:
        futs = {pool.submit(_work, c): i for i, c in enumerate(cits)}
        for fut in as_completed(futs):
            results[futs[fut]] = fut.result()

    verified = [r for r in results if r and r.status is CitationStatus.VERIFIED]
    ambiguous = [r for r in results if r and r.status is CitationStatus.AMBIGUOUS]
    dropped = [r for r in results if r and r.status in (CitationStatus.UNVERIFIED, CitationStatus.FABRICATED)]
    if drop_ambiguous:
        dropped = dropped + ambiguous
        kept = list(verified)
    else:
        kept = verified + ambiguous
    return VerificationReport(verified=verified, ambiguous=ambiguous, dropped=dropped, kept=kept)


# ── Optional Layer-4 relevance judge (factory LLM; off by default) ────────────


def make_llm_relevance_fn() -> Callable[[Citation, str], float]:
    """Build a relevance judge backed by the LLM factory (the sole LLM path).

    Returns a ``(citation, context) -> float in [0,1]`` callable. Failure-isolated
    — returns 1.0 (don't penalise) when the LLM is unavailable, so a sick model
    never silently drops real citations. Pass it to ``verify_*`` only when the
    operator opts into the relevance layer.
    """

    def _relevance(citation: Citation, context: str) -> float:
        try:
            from app.llm_factory import chat_completion_for_role

            prompt = (
                "On a scale of 0.0 to 1.0, how relevant is the cited paper to the "
                "claim/context it is meant to support? Reply with ONLY the number.\n\n"
                f"Context: {context[:1500]}\n\n"
                f"Cited paper: {citation.title} ({citation.year or 'n.d.'}) "
                f"doi={citation.doi or '-'} arxiv={citation.arxiv_id or '-'}"
            )
            handle = chat_completion_for_role(role="research", task_hint="citation relevance")
            resp = handle.create(messages=[{"role": "user", "content": prompt}], max_tokens=8)
            text = (resp.choices[0].message.content or "").strip()
            m = re.search(r"[01](?:\.\d+)?", text)
            return max(0.0, min(1.0, float(m.group(0)))) if m else 1.0
        except Exception:
            logger.debug("citation_verifier: llm relevance unavailable", exc_info=True)
            return 1.0

    return _relevance


__all__ = [
    "Resolvers",
    "DEFAULT_RESOLVERS",
    "VerificationReport",
    "verify_citation",
    "verify_references",
    "make_llm_relevance_fn",
]
