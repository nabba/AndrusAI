"""
skill_chain.py — recovery strategy: invoke a matching skill from the
skills library.

First-cut implementation: search the library for a single high-match
skill, run it, return its output. Multi-skill chaining (run skill A,
pass output to skill B) is deferred to a follow-up — the dominant
case in practice is "we already know how to do this, we just need
to remember which skill describes it."

The skills library is populated by the self-improvement loop
(``app.self_improvement.integrator``); each skill is a markdown
file with a topic + steps + (sometimes) example code. Searching
uses the same retrieval the rest of the system uses, so a skill
written months ago is reachable when its topic matches a current
prompt.

Returns success=False when no skill matches well enough — the loop
falls through to ``forge_queue`` which records the gap as a future
skill candidate.
"""
from __future__ import annotations

import logging

from app.recovery.librarian import Alternative
from app.recovery.strategies import StrategyResult

logger = logging.getLogger(__name__)


# Confidence floor — below this, we treat "best match" as "no match"
# so we don't try to apply an irrelevant skill.
_MIN_RELEVANCE_SCORE = 0.55


def _search_top_skill(task: str):
    """Return the best matching skill record, or None."""
    try:
        from app.self_improvement.integrator import search_skills
        results = search_skills(task, n=3)
    except Exception as exc:
        logger.debug("skill_chain: search_skills raised: %s", exc)
        return None
    if not results:
        return None
    best = results[0]
    # SkillRecord shape: title, body, score, etc. Be defensive.
    score = getattr(best, "score", None)
    if score is not None and score < _MIN_RELEVANCE_SCORE:
        logger.info(
            "skill_chain: best match score %.2f below threshold — skipping",
            score,
        )
        return None
    return best


def execute(task: str, alt: Alternative, ctx: dict) -> StrategyResult:
    """Find a matching skill + present its content as a recovery answer.

    NOTE: 'present' rather than 'execute' — skills are markdown
    descriptions, not runnable code. The recovery answer is a
    "here's how the system has solved this before" reference,
    which is often what the user actually needed. For runnable
    code, sandbox_execute is the right strategy.
    """
    skill = _search_top_skill(task)
    if not skill:
        return StrategyResult(
            success=False,
            error="skill_chain: no skill matches above threshold",
        )

    title = getattr(skill, "title", "") or "Skill"
    body = getattr(skill, "body", "") or ""
    score = getattr(skill, "score", None)

    if not body or len(body.strip()) < 50:
        return StrategyResult(
            success=False,
            error="skill_chain: matched skill has empty/tiny body",
        )

    score_note = f" (relevance {score:.2f})" if isinstance(score, float) else ""
    answer = (
        f"I have an existing skill that addresses this — '{title}'{score_note}.\n\n"
        f"{body[:3500].strip()}"
    )
    if len(body) > 3500:
        answer += "\n\n_(skill body truncated; full content in workspace/skills/)_"

    return StrategyResult(
        success=True,
        text=answer,
        note=f"Surfaced existing skill '{title}' from the skills library.",
        route_changed=True,
    )
