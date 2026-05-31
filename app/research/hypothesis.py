"""app.research.hypothesis — the hypothesis-generation research step.

Phase 2 of the auto-research composition layer. The headless brainstorm
primitive (``app.brainstorm.headless.generate_hypotheses``) already turns a
topic into scored, novelty-ranked ideas. This step adds the *research-specific*
layer on top of it:

  * **Grounding.** When the caller passes literature hits (the output of the
    Phase 1 ``literature_search`` step), their titles/abstracts are woven into
    the brainstorm prompt so the agents propose hypotheses that extend, test,
    or contradict prior work rather than re-deriving it from nothing.
  * **Provenance.** Each returned :class:`ResearchHypothesis` records which of
    the supplied passages it overlaps (lightweight significant-term match), so
    a downstream planner can see what prior work each hypothesis builds on —
    and so a hypothesis with grounding is more likely to yield a *citable*
    finding when it later runs through ``gate_research_evidence`` (Phase 1).

Composition only — owns no infrastructure. Ideation is delegated through a
single injectable ``generate`` seam (defaults to the headless generator,
resolved lazily); the step does NOT fetch literature itself — the research run
(Phase 3) wires ``search_literature → propose_hypotheses(literature=…)``. Every
external call is failure-isolated, so this runs on a host with no LLM /
ChromaDB / crewai: a dead generator yields ``[]``, never an exception.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# How many literature passages to fold into the prompt, and how much of each.
_MAX_GROUNDING_ITEMS = 5
_GROUNDING_SNIPPET_CHARS = 240

# A significant token: alphanumeric, length >= 4, not a common word. Used for
# the per-hypothesis grounding overlap (deliberately crude — it only has to be
# good enough to link a hypothesis to the passages that obviously seeded it).
_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "about", "above", "after", "again", "against", "their", "there",
        "these", "those", "through", "under", "until", "while", "which",
        "whom", "with", "would", "could", "should", "where", "when", "what",
        "whether", "than", "then", "that", "this", "from", "into", "over",
        "such", "some", "more", "most", "much", "very", "also", "been",
        "being", "have", "having", "does", "doing", "done", "were", "will",
        "shall", "must", "they", "them", "your", "ours", "into", "onto",
        "upon", "each", "both", "many", "only", "same", "other", "another",
    }
)


@dataclass(frozen=True)
class ResearchHypothesis:
    """One research-framed hypothesis with grounding provenance.

    ``rank`` is 1-based in the order the ideation step surfaced it (the
    headless generator already sorts novel + aesthetically-strong ideas
    first). ``grounded_in`` lists the literature ids whose significant terms
    this hypothesis overlaps — empty when no literature was supplied or no
    passage matched.
    """

    text: str
    rank: int
    role: str = "?"
    novelty: str = "novel"
    aesthetic: Optional[float] = None
    grounded_in: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "rank": self.rank,
            "role": self.role,
            "novelty": self.novelty,
            "aesthetic": self.aesthetic,
            "grounded_in": list(self.grounded_in),
            "notes": list(self.notes),
        }


# ── Literature helpers ──────────────────────────────────────────────────────


def _hit_field(hit, name: str) -> str:
    """Read a field from a LiteratureHit or a plain dict; '' when absent."""
    if isinstance(hit, dict):
        return str(hit.get(name) or "")
    return str(getattr(hit, name, "") or "")


def _significant_tokens(text: str) -> set[str]:
    return {
        t for t in _TOKEN.findall((text or "").lower())
        if len(t) >= 4 and t not in _STOPWORDS
    }


def _summarise_literature(hits: list) -> str:
    """Compact bullet list of the supplied passages for the prompt."""
    lines: list[str] = []
    for hit in hits[:_MAX_GROUNDING_ITEMS]:
        title = _hit_field(hit, "title").strip()
        snippet = (title or _hit_field(hit, "text").strip())[:_GROUNDING_SNIPPET_CHARS]
        if snippet:
            lines.append(f"- {snippet}")
    return "\n".join(lines)


def _build_grounded_prompt(question: str, hits: list) -> str:
    base = (
        "Propose distinct, specific, testable research hypotheses for the "
        "question below. Each hypothesis must be something we could "
        "investigate or run an experiment on, and should state the expected "
        "effect or relationship concretely.\n\n"
        f"Research question: {question}"
    )
    summary = _summarise_literature(hits) if hits else ""
    if summary:
        base += (
            "\n\nWhat the existing literature already reports — ground your "
            "hypotheses in this; prefer gaps, contradictions, or untested "
            "extensions over restating it:\n" + summary
        )
    return base


def _grounding_overlap(hyp_text: str, hits: list, min_shared: int) -> list[str]:
    """Literature ids whose significant terms overlap the hypothesis text."""
    hyp_tokens = _significant_tokens(hyp_text)
    if not hyp_tokens:
        return []
    grounded: list[str] = []
    for hit in hits:
        hid = _hit_field(hit, "id")
        if not hid:
            continue
        hit_tokens = _significant_tokens(
            _hit_field(hit, "title") + " " + _hit_field(hit, "text")
        )
        if len(hyp_tokens & hit_tokens) >= min_shared:
            grounded.append(hid)
    return grounded


# ── Public API ────────────────────────────────────────────────────────────


def propose_hypotheses(
    question: str,
    *,
    literature: Optional[list] = None,
    n: int = 6,
    roster: "int | list[str] | None" = None,
    technique_title: str = "How-Might-We",
    spent_so_far_usd: float = 0.0,
    min_shared_terms: int = 2,
    generate: Optional[Callable] = None,
) -> list[ResearchHypothesis]:
    """Generate research hypotheses for ``question``, grounded in ``literature``.

    ``literature`` is an optional list of ``LiteratureHit`` (or dict) passages
    from the literature-search step; when given, they are folded into the
    brainstorm prompt and each hypothesis records the passages it overlaps.
    Returns up to ``n`` ranked :class:`ResearchHypothesis`, or ``[]`` if the
    question is empty or ideation fails — never raises.

    ``generate`` is the injectable ideation seam: a
    ``headless.generate_hypotheses``-shaped callable
    ``(topic, *, n, roster, technique_title, step_prompt, spent_so_far_usd)``.
    Defaults to the real headless generator, resolved lazily.
    """
    if not question or not question.strip():
        return []
    question = question.strip()
    hits = list(literature or [])

    try:
        prompt = _build_grounded_prompt(question, hits)
    except Exception:
        logger.debug("hypothesis: prompt build failed", exc_info=True)
        prompt = None

    gen = generate
    if gen is None:
        try:
            from app.brainstorm.headless import generate_hypotheses as gen  # type: ignore
        except Exception:
            logger.debug("hypothesis: headless generator unavailable", exc_info=True)
            return []

    try:
        raw = gen(
            question,
            n=n,
            roster=roster,
            technique_title=technique_title,
            step_prompt=prompt,
            spent_so_far_usd=spent_so_far_usd,
        )
    except Exception:
        logger.debug("hypothesis: ideation failed", exc_info=True)
        return []

    out: list[ResearchHypothesis] = []
    for h in raw or []:
        text = str(getattr(h, "text", "") or "").strip()
        if not text:
            continue
        out.append(
            ResearchHypothesis(
                text=text,
                rank=len(out) + 1,
                role=str(getattr(h, "role", "?")),
                novelty=str(getattr(h, "novelty", "novel")),
                aesthetic=getattr(h, "aesthetic", None),
                grounded_in=_grounding_overlap(text, hits, min_shared_terms) if hits else [],
                notes=list(getattr(h, "notes", []) or []),
            )
        )
    return out


__all__ = ["ResearchHypothesis", "propose_hypotheses"]
