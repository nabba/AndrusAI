"""
escalate_tier.py — recovery strategy: re-run the same crew with a
stronger LLM tier.

Use case: the original crew was the right specialist, but its
budget-tier model gave up too early. A premium model on the same
crew often persists through obstacles (more research depth, better
multi-step reasoning) without re-routing the entire request.
"""
from __future__ import annotations

import logging

from app.recovery.librarian import Alternative
from app.recovery.strategies import StrategyResult

logger = logging.getLogger(__name__)


def execute(task: str, alt: Alternative, ctx: dict) -> StrategyResult:
    """Re-run the failed crew at the upgraded tier."""
    target_tier = alt.tier or "premium"
    crew_used = ctx.get("crew_used", "")
    if not crew_used:
        return StrategyResult(
            success=False,
            error="escalate_tier: no original crew name in context",
        )

    commander = ctx.get("commander")
    if not commander or not hasattr(commander, "_run_crew"):
        return StrategyResult(
            success=False,
            error="escalate_tier: commander not available in context",
        )

    history = ctx.get("conversation_history", "")
    difficulty = int(ctx.get("difficulty", 5))

    logger.info(
        "recovery.escalate_tier: re-running %r at tier=%r (was budget/mid)",
        crew_used, target_tier,
    )

    # Tier override is communicated via ContextVar (existing pattern
    # in app.llm_factory.create_specialist_llm — force_tier kwarg).
    # We approximate by injecting a tier hint into the task; the
    # specialist agents respect the LLM_FORCE_TIER env-style override.
    import os
    saved_force = os.environ.get("LLM_FORCE_TIER")
    os.environ["LLM_FORCE_TIER"] = target_tier
    try:
        result = commander._run_crew(
            crew_used, task,
            difficulty=max(difficulty, 7),  # nudge difficulty up so the
                                            # selector doesn't demote
            conversation_history=history,
        )
    except Exception as exc:
        return StrategyResult(
            success=False,
            error=f"escalate_tier: {crew_used}@{target_tier} raised: {exc}",
        )
    finally:
        if saved_force is None:
            os.environ.pop("LLM_FORCE_TIER", None)
        else:
            os.environ["LLM_FORCE_TIER"] = saved_force

    if not result or not isinstance(result, str) or len(result.strip()) < 10:
        return StrategyResult(
            success=False,
            error=f"escalate_tier: {crew_used}@{target_tier} returned empty",
        )

    # Same defensive re-check
    from app.recovery.refusal_detector import detect_refusal
    if detect_refusal(result) is not None:
        return StrategyResult(
            success=False,
            error=f"escalate_tier: {crew_used}@{target_tier} also refused",
        )

    note = f"Retried with {target_tier}-tier model (original budget tier hit a dead-end)."
    return StrategyResult(
        success=True,
        text=result,
        note=note,
        route_changed=True,   # tier change → user sees a note
    )
