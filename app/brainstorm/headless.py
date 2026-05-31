"""Headless brainstorm — generate scored hypotheses without a human in the loop.

The interactive facilitator (``app.brainstorm.facilitator``) is turn-based: it
emits one step, waits for the user, then continues. An autonomous research run
has no human to answer mid-session, so this module runs a single **seed round**
of the multi-agent brainstorm (``multi_agent.gather_seed``) and scores every
resulting idea with the same creativity primitives the interactive report uses
(``creativity.novelty_wrap`` + ``creativity.aesthetic_score``) — exactly the
annotation ``brainstorm.report._annotate_text`` already applies, just driven
programmatically and returned as typed records instead of prose.

Each idea becomes a :class:`Hypothesis` carrying its novelty verdict and
aesthetic score. The default ordering surfaces genuinely-novel, aesthetically
strong ideas first; the caller (e.g. a research planner) decides what to do
with restated / previously-rejected ones.

Every external call is injectable (``gather`` / ``assess`` / ``score_fn``) and
failure-isolated, so this runs on a host with no LLM, ChromaDB, or crewai.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Novelty verdict → sort priority (lower = surfaced first). Keyed by the
# string values of creativity.novelty_wrap.NoveltyVerdict so we never have to
# import the enum here.
_NOVELTY_RANK = {
    "novel": 0,
    "recombination": 1,
    "restated": 2,
    "rejected_before": 3,
}

_LIST_MARKER = re.compile(r"^\s*(?:\d+[.)]|[-*•·])\s+")


@dataclass(frozen=True)
class Hypothesis:
    """One scored idea from a headless brainstorm seed round."""

    text: str
    role: str
    novelty: str = "novel"
    aesthetic: Optional[float] = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "role": self.role,
            "novelty": self.novelty,
            "aesthetic": self.aesthetic,
            "notes": list(self.notes),
        }


def _split_numbered(text: str) -> list[str]:
    """Split an agent's numbered/bulleted list into individual ideas.

    Lines beginning with ``1.`` / ``2)`` / ``-`` / ``*`` / ``•`` start a new
    item; lines without a marker are treated as continuations of the current
    item. A blob with no markers becomes a single item.
    """
    items: list[str] = []
    current: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if _LIST_MARKER.match(raw):
            if current:
                items.append(" ".join(current).strip())
                current = []
            current.append(_LIST_MARKER.sub("", raw).strip())
        else:
            current.append(line)
    if current:
        items.append(" ".join(current).strip())
    return [i for i in items if i]


def _default_step_prompt(topic: str) -> str:
    return (
        f"Generate distinct, specific, testable hypotheses or research "
        f"approaches for the following topic. Each should be something we "
        f"could actually investigate or run an experiment on.\n\nTopic: {topic}"
    )


def _assess_novelty(text: str, assess: Optional[Callable]) -> tuple[str, list[str]]:
    """Return ``(verdict_value, notes)``; defaults to ('novel', []) on any failure."""
    fn = assess
    if fn is None:
        try:
            from app.creativity.novelty_wrap import assess_brainstorm_idea as fn  # type: ignore
        except Exception:
            return "novel", []
    try:
        wrap = fn(text)
        verdict = getattr(wrap, "verdict", "novel")
        verdict = str(getattr(verdict, "value", verdict)).lower()
        notes = list(getattr(wrap, "notes", []) or [])
        return verdict, notes
    except Exception:
        logger.debug("headless: novelty assessment failed", exc_info=True)
        return "novel", []


def _score_aesthetic(text: str, score_fn: Optional[Callable]) -> Optional[float]:
    fn = score_fn
    if fn is None:
        try:
            from app.creativity.aesthetic_score import score as fn  # type: ignore
        except Exception:
            return None
    try:
        return fn(text)
    except Exception:
        logger.debug("headless: aesthetic score failed", exc_info=True)
        return None


def generate_hypotheses(
    topic: str,
    *,
    n: int = 6,
    roster: "int | list[str] | None" = None,
    technique_title: str = "How-Might-We",
    step_prompt: Optional[str] = None,
    spent_so_far_usd: float = 0.0,
    gather: Optional[Callable] = None,
    assess: Optional[Callable] = None,
    score_fn: Optional[Callable] = None,
) -> list[Hypothesis]:
    """Run one headless seed round and return up to ``n`` scored hypotheses.

    Ordering: novel ideas first (by novelty verdict), ties broken by higher
    aesthetic score. Duplicate idea texts are collapsed. Returns ``[]`` if the
    topic is empty or every agent fails — never raises.

    Injectable seams (all default to the real subsystem, resolved lazily):
      * ``gather``   — ``multi_agent.gather_seed``-shaped callable.
      * ``assess``   — ``novelty_wrap.assess_brainstorm_idea``-shaped callable.
      * ``score_fn`` — ``aesthetic_score.score``-shaped callable.
    """
    if not topic or not topic.strip():
        return []
    topic = topic.strip()

    try:
        from app.brainstorm.multi_agent import resolve_roster

        roster_list = resolve_roster(roster) if not isinstance(roster, list) else roster
        if not roster_list:
            from app.brainstorm.multi_agent import DEFAULT_ROSTER

            roster_list = list(DEFAULT_ROSTER)
    except Exception:
        logger.debug("headless: roster resolution failed", exc_info=True)
        return []

    gather_fn = gather
    if gather_fn is None:
        try:
            from app.brainstorm.multi_agent import gather_seed as gather_fn  # type: ignore
        except Exception:
            logger.debug("headless: gather_seed unavailable", exc_info=True)
            return []

    try:
        responses = gather_fn(
            technique_title=technique_title,
            topic=topic,
            step_prompt=step_prompt or _default_step_prompt(topic),
            roster=roster_list,
            spent_so_far_usd=spent_so_far_usd,
        )
    except Exception:
        logger.debug("headless: gather round failed", exc_info=True)
        return []

    seen: set[str] = set()
    hyps: list[Hypothesis] = []
    for resp in responses or []:
        if getattr(resp, "error", None):
            continue
        role = str(getattr(resp, "role", "?"))
        for idea in _split_numbered(getattr(resp, "text", "")):
            key = idea.lower()
            if key in seen:
                continue
            seen.add(key)
            verdict, notes = _assess_novelty(idea, assess)
            hyps.append(
                Hypothesis(
                    text=idea,
                    role=role,
                    novelty=verdict,
                    aesthetic=_score_aesthetic(idea, score_fn),
                    notes=notes,
                )
            )

    hyps.sort(key=lambda h: (_NOVELTY_RANK.get(h.novelty, 9), -(h.aesthetic or 0.0)))
    return hyps[: max(0, n)]
