"""app.research.manuscript — section-by-section research manuscript composer.

Turns a research run's artifacts into a structured manuscript. Mirrors the
Company Dossier composer discipline (``app.dossier.compose``) in **pure stdlib**
(dossier pulls pydantic; the research spine stays host-importable — same call
as ``citation``):

  * **slice-only-facts** — each section is written from ONLY the artifacts in
    its slice, so a hallucination in one section can't borrow another's facts;
  * **fact-check** — a regex pass flags any quantitative token in the prose
    that isn't present in that section's slice (flagged, not deleted — the
    pipeline's ``research:verify`` step is what *enforces*);
  * **deterministic fallback** — ``_slice_echo`` renders the slice as plain
    prose when no LLM is available, so a manuscript is ALWAYS producible and
    never invents facts.

It composes what the spine already produced — the question, retrieved
literature, hypotheses, the experiment analysis, and the Phase-B *verified*
citations (the References section is exactly the citations that survived
verification). The output is a typed :class:`Manuscript` that a later LaTeX/PDF
backend (Phase D) renders; ``llm_call`` is injected so the whole composer runs
with no LLM in tests.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from app.research.citation import Citation

logger = logging.getLogger(__name__)


# ── Artifacts in / Manuscript out ────────────────────────────────────────────


@dataclass
class ResearchArtifacts:
    """The run outputs a manuscript is composed from. Plain data so the
    composer is testable without an ExecutorRun."""

    question: str
    literature: Sequence[dict] = ()      # [{"title": ..., "id": ...}, ...]
    hypotheses: Sequence[str] = ()
    findings: str = ""                   # the analysis / investigation narrative
    measurements: str = ""               # experiment stdout / measurement text
    citations: Sequence[Citation] = ()   # VERIFIED citations (Phase-B kept set)


@dataclass
class Section:
    title: str
    prose: str
    fact_check_warnings: list[str] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        return len(self.prose.split())

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "prose": self.prose,
            "fact_check_warnings": list(self.fact_check_warnings),
            "word_count": self.word_count,
        }


@dataclass
class Manuscript:
    title: str
    sections: list[Section]
    references: list[Citation]

    def all_warnings(self) -> list[str]:
        out: list[str] = []
        for s in self.sections:
            out.extend(f"[{s.title}] {w}" for w in s.fact_check_warnings)
        return out

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "sections": [s.to_dict() for s in self.sections],
            "references": [c.to_dict() for c in self.references],
            "word_count": sum(s.word_count for s in self.sections),
            "warnings": self.all_warnings(),
        }


# ── Section plan ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SectionSpec:
    title: str
    draws: tuple[str, ...]   # which ResearchArtifacts fields this section may use
    instruction: str


DEFAULT_SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec("Abstract", ("question", "hypotheses", "findings"),
                "Summarize the question, the approach, and the single key finding in 4-6 sentences."),
    SectionSpec("Introduction", ("question", "literature"),
                "Motivate the research question and situate it against the retrieved prior work."),
    SectionSpec("Related Work", ("literature",),
                "Summarize the retrieved prior work and how each piece relates to the question."),
    SectionSpec("Method", ("question", "hypotheses"),
                "Describe the leading hypothesis under test and the experimental approach taken."),
    SectionSpec("Results", ("findings", "measurements"),
                "Report the measured results concretely; attribute every number to the recorded measurement."),
    SectionSpec("Discussion", ("hypotheses", "findings"),
                "Interpret the results against the hypotheses and state the limitations honestly."),
    SectionSpec("Conclusion", ("question", "findings"),
                "State what was learned and what follows, without overclaiming."),
)

_BASE_RULES = (
    "Write in plain academic prose. Use ONLY the facts in the slice below — do "
    "not introduce numbers, results, or citations that are not present there. "
    "If the slice lacks what a sentence needs, write 'not available' rather "
    "than inventing a plausible value."
)


# ── Slice rendering + fact-check ──────────────────────────────────────────────


def _render_slice(artifacts: ResearchArtifacts, draws: tuple[str, ...]) -> str:
    """Render ONLY the drawn artifact fields as a fact list for one section."""
    parts: list[str] = []
    if "question" in draws and artifacts.question:
        parts.append(f"Research question: {artifacts.question}")
    if "literature" in draws and artifacts.literature:
        lines = []
        for hit in artifacts.literature:
            title = str((hit or {}).get("title") or (hit or {}).get("text") or "").strip()
            if title:
                ident = str((hit or {}).get("id") or "").strip()
                lines.append(f"- {title[:240]}" + (f" [{ident}]" if ident else ""))
        if lines:
            parts.append("Retrieved literature:\n" + "\n".join(lines))
    if "hypotheses" in draws and artifacts.hypotheses:
        hyps = [str(h).strip() for h in artifacts.hypotheses if str(h).strip()]
        if hyps:
            parts.append("Hypotheses:\n" + "\n".join(f"- {h}" for h in hyps))
    if "findings" in draws and artifacts.findings.strip():
        parts.append("Findings / analysis:\n" + artifacts.findings.strip()[:4000])
    if "measurements" in draws and artifacts.measurements.strip():
        parts.append("Measurements:\n" + artifacts.measurements.strip()[:2000])
    return "\n\n".join(parts)


_NUM_RE = re.compile(r"\d+(?:\.\d+)?\s*%?")


def _norm_num(tok: str) -> str:
    return re.sub(r"\s+", "", tok)


def _fact_check(prose: str, slice_text: str) -> list[str]:
    """Flag quantitative tokens in the prose that don't appear in the slice —
    the same 'did the composer invent a number?' check the dossier does. Flags;
    never deletes (the run's verify step enforces)."""
    if not prose:
        return []
    valid = {_norm_num(t) for t in _NUM_RE.findall(slice_text or "")}
    warnings: list[str] = []
    seen: set[str] = set()
    for tok in _NUM_RE.findall(prose):
        n = _norm_num(tok)
        if not n or n in seen:
            continue
        seen.add(n)
        if n not in valid:
            warnings.append(f"unverified quantitative token {tok.strip()!r} (absent from the section slice)")
    return warnings


# ── Composition ───────────────────────────────────────────────────────────────


def _build_prompt(spec: SectionSpec, slice_text: str) -> str:
    return (
        f"Write the '{spec.title}' section of a research manuscript.\n"
        f"{spec.instruction}\n\n{_BASE_RULES}\n\n"
        f"--- SLICE (the only facts you may use) ---\n{slice_text or '(no facts available for this section)'}"
    )


def _slice_echo(spec: SectionSpec, slice_text: str) -> str:
    """Deterministic fallback prose — renders the slice, inventing nothing."""
    if not slice_text.strip():
        return f"({spec.title}: no material available for this section.)"
    return f"This section draws on the following established material:\n\n{slice_text}"


def _default_llm_call(prompt: str) -> str:
    """Focused writing completion via the LLM factory (the sole LLM path)."""
    try:
        from app.llm_factory import chat_completion_for_role

        handle = chat_completion_for_role(role="writing", task_hint="manuscript section")
        resp = handle.create(messages=[{"role": "user", "content": prompt}], max_tokens=1200)
        return resp.choices[0].message.content or ""
    except Exception:
        logger.debug("manuscript: writing completion unavailable", exc_info=True)
        return ""


def compose_manuscript(
    artifacts: ResearchArtifacts,
    *,
    llm_call: Optional[Callable[[str], str]] = None,
    sections: Sequence[SectionSpec] = DEFAULT_SECTIONS,
) -> Manuscript:
    """Compose a sectioned manuscript from a run's artifacts.

    Each section is composed from ONLY its slice; an empty/failed LLM reply
    falls back to a deterministic slice-echo (never invents facts); each
    section's prose is fact-checked against its slice. The References section is
    the passed-in (verified) citations. Never raises.
    """
    call = llm_call or _default_llm_call
    out_sections: list[Section] = []
    for spec in sections:
        slice_text = _render_slice(artifacts, spec.draws)
        try:
            prose = (call(_build_prompt(spec, slice_text)) or "").strip()
        except Exception:
            logger.debug("manuscript: llm_call raised for %s", spec.title, exc_info=True)
            prose = ""
        if not prose:
            prose = _slice_echo(spec, slice_text)
        out_sections.append(Section(title=spec.title, prose=prose, fact_check_warnings=_fact_check(prose, slice_text)))

    title = (artifacts.question or "Research findings").strip()
    return Manuscript(title=title, sections=out_sections, references=list(artifacts.citations))


__all__ = [
    "ResearchArtifacts",
    "Section",
    "Manuscript",
    "SectionSpec",
    "DEFAULT_SECTIONS",
    "compose_manuscript",
]
