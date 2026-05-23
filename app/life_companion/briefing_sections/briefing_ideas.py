"""briefing_ideas — surface the weekly LLM-proposed section ideas inline.

Reads ``proposer.recent_proposals(n=3)`` from briefing_evolution and
renders them as a quick "system thinks these would be useful" hint
in the morning briefing. Implementing any of them requires a real
candidate module under ``app/life_companion/briefing_sections/`` —
this section just makes the proposals visible so the operator can
ask the system to implement one.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

ID = "briefing-ideas"
DISPLAY_NAME = "💭 Ideas the system would propose"
DESCRIPTION = (
    "Up to 3 new briefing-section ideas the weekly LLM proposer "
    "thinks would be useful. Acting on one requires implementing a "
    "candidate module — the operator says which."
)


def gather() -> list[str]:
    try:
        from app.life_companion.briefing_evolution.proposer import recent_proposals
    except Exception:
        logger.debug("briefing_ideas: proposer import failed", exc_info=True)
        return []
    try:
        ideas = recent_proposals(n=3) or []
    except Exception:
        logger.debug("briefing_ideas: read failed", exc_info=True)
        return []
    if not ideas:
        return []
    out: list[str] = []
    for it in ideas:
        if not isinstance(it, dict):
            continue
        label = (it.get("display_name") or it.get("id") or "")[:60]
        difficulty = (it.get("implementation_difficulty") or "")[:6]
        rationale = (it.get("rationale") or it.get("description") or "")[:140]
        diff_tag = f" [{difficulty}]" if difficulty else ""
        out.append(f"  • {label}{diff_tag}")
        if rationale:
            out.append(f"     → {rationale}")
    return out
