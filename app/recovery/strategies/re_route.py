"""
re_route.py — recovery strategy: ask commander to re-decide, exclude
the failed crew.

Cheapest recovery path. Reuses the existing Commander._route() so the
re-routing decision benefits from all its existing intelligence
(matrix detection, attachment hints, ToM, etc.). The new constraint:
exclude the crew that just refused.
"""
from __future__ import annotations

import logging

from app.recovery.librarian import Alternative
from app.recovery.strategies import StrategyResult

logger = logging.getLogger(__name__)


def execute(task: str, alt: Alternative, ctx: dict) -> StrategyResult:
    """Force-route to ``alt.crew`` and run that crew on ``task``."""
    target_crew = alt.crew
    if not target_crew:
        return StrategyResult(success=False, error="re_route: no target crew specified")

    commander = ctx.get("commander")
    if not commander or not hasattr(commander, "_run_crew"):
        return StrategyResult(
            success=False,
            error="re_route: commander not available in context",
        )

    crew_used = ctx.get("crew_used", "")
    history = ctx.get("conversation_history", "")
    difficulty = int(ctx.get("difficulty", 5))

    logger.info(
        "recovery.re_route: %r refused — re-running with crew=%r",
        crew_used, target_crew,
    )

    try:
        result = commander._run_crew(
            target_crew, task,
            difficulty=difficulty,
            conversation_history=history,
        )
    except Exception as exc:
        return StrategyResult(
            success=False,
            error=f"re_route: {target_crew} crew raised: {exc}",
        )

    if not result or not isinstance(result, str) or len(result.strip()) < 10:
        return StrategyResult(
            success=False,
            error=f"re_route: {target_crew} returned empty/tiny output",
        )

    # Don't re-introduce the same refusal. If the new crew also refused,
    # don't claim recovery.
    from app.recovery.refusal_detector import detect_refusal
    if detect_refusal(result) is not None:
        return StrategyResult(
            success=False,
            error=f"re_route: {target_crew} also produced a refusal-shaped answer",
        )

    note = f"Redirected to {target_crew} crew (the original {crew_used} crew didn't have the right tools)."
    return StrategyResult(
        success=True,
        text=result,
        note=note,
        route_changed=True,
    )
