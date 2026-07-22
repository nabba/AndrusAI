"""
critic_crew.py — Adversarial review crew for high-difficulty tasks.

Invoked by Commander for difficulty ≥ 7 tasks. The Critic agent reviews
the crew's output against a 7-point checklist (from souls/critic.md),
challenges weak claims, and flags issues before the user sees the response.

This is NOT the same as vetting (app/vetting.py). Vetting is a quick
single-pass check. The Critic is a deeper adversarial review with
memory access, philosophy grounding, and multi-dimensional analysis.

Only runs on high-difficulty tasks to avoid adding latency to simple ones.
"""

import logging
import re
import time

from crewai import Task, Crew, Process
from app.agents.critic import create_critic
from app.firebase_reporter import crew_started, crew_completed, crew_failed
from app.rate_throttle import start_request_tracking, stop_request_tracking

logger = logging.getLogger(__name__)

_LEGACY_CRITICAL = re.compile(
    r"^\s*(?:[-*]\s*)?(?:(?:severity\s*[:=-]\s*)?critical\b|\[critical\])",
    re.IGNORECASE | re.MULTILINE,
)
_CONTRACT = re.compile(
    r"^\s*(PASS|REVISED|BLOCK)\s*(?::[ \t]*(.*))?(?:\n([\s\S]*))?\s*$",
    re.IGNORECASE,
)


def _apply_review_result(crew_output: str, review: str) -> tuple[str, str]:
    """Apply the critic's structured PASS/REVISED/BLOCK contract.

    Returns ``(delivery_text, outcome)``.  Critical findings can no longer be
    logged and then silently discarded.  Malformed non-critical feedback keeps
    the original response so a formatting failure does not become an outage.
    """
    result = (review or "").strip()
    contract = _CONTRACT.fullmatch(result)
    if contract:
        directive = contract.group(1).upper()
        payload = "\n".join(
            part.strip()
            for part in (contract.group(2), contract.group(3))
            if part and part.strip()
        )
        if directive == "PASS":
            if not payload:
                return crew_output, "pass"
            return crew_output, "malformed"
        if directive == "REVISED":
            if len(payload) >= 20:
                return payload, "revised"
            return crew_output, "malformed"
        reason = payload[:800] or "the answer could not be verified safely"
        return (
            "I’m withholding the draft because adversarial review found an "
            f"unresolved critical quality issue: {reason}",
            "blocked",
        )

    if _LEGACY_CRITICAL.search(result):
        reason = result[:800] or "the answer could not be verified safely"
        return (
            "I’m withholding the draft because adversarial review found an "
            f"unresolved critical quality issue: {reason}",
            "blocked",
        )

    return crew_output, "malformed"


class CriticCrew:
    """Adversarial review of agent output before delivery."""

    def review(self, original_task: str, crew_output: str,
               crew_used: str = "", difficulty: int = 5,
               parent_task_id: str | None = None) -> str:
        """Review crew output and return the original, a revision, or a block.

        Args:
            original_task: What the user originally asked
            crew_output: The crew's response to review
            crew_used: Which crew produced the output
            difficulty: Task difficulty (for context)
            parent_task_id: For task tracking hierarchy

        Returns:
            PASS preserves the original; REVISED supplies a complete corrected
            answer; BLOCK withholds a critically unsafe/unverifiable answer.
        """
        _start = time.monotonic()
        from app.conversation_store import estimate_eta

        task_id = crew_started(
            "critic", f"Review: {original_task[:80]}",
            eta_seconds=estimate_eta("critic") or 15,
            parent_task_id=parent_task_id,
        )
        start_request_tracking(task_id)

        try:
            critic = create_critic()

            review_task = Task(
                description=(
                    f"You are reviewing output from the {crew_used or 'unknown'} crew "
                    f"(difficulty: {difficulty}/10).\n\n"
                    f"## Original User Request:\n{original_task[:1000]}\n\n"
                    f"## Crew Output to Review:\n{crew_output[:4000]}\n\n"
                    f"## Your Task:\n"
                    f"Apply your 7-point review checklist:\n"
                    f"1. Logical consistency — contradictions?\n"
                    f"2. Factual accuracy — hallucinated data/URLs?\n"
                    f"3. Source quality — credible, cited?\n"
                    f"4. Completeness — gaps in addressing the request?\n"
                    f"5. Confidence calibration — justified by evidence?\n"
                    f"6. Actionability — can someone act on this?\n"
                    f"7. Productive tension — false clarity on complex topics?\n\n"
                    f"Return exactly one of these contracts:\n"
                    f"PASS\n"
                    f"REVISED\n<the complete corrected answer, ready for the user>\n"
                    f"BLOCK\n<a concise reason that cannot be corrected from the supplied evidence>\n\n"
                    f"Use REVISED when you can safely fix an issue from the supplied "
                    f"material. Use BLOCK only for a critical factual, safety, or "
                    f"evidence defect that cannot be repaired without new information."
                ),
                expected_output=(
                    "Exactly PASS, REVISED followed by a complete corrected answer, "
                    "or BLOCK followed by the unresolved critical reason."
                ),
                agent=critic,
            )

            crew = Crew(
                agents=[critic],
                tasks=[review_task],
                process=Process.sequential,
                verbose=False,
            )

            from app.project_context import agent_scope
            with agent_scope("critic"):
                result = str(crew.kickoff()).strip()
            duration = time.monotonic() - _start

            tracker = stop_request_tracking()
            _tokens = tracker.total_tokens if tracker else 0
            _cost = tracker.total_cost_usd if tracker else 0.0

            crew_completed(
                "critic", task_id, result[:500],
                tokens_used=_tokens, cost_usd=_cost,
            )

            delivery, outcome = _apply_review_result(crew_output, result)
            logger.info(
                "Critic review: %s (%d chars, %.1fs)",
                outcome,
                len(result),
                duration,
            )
            return delivery

        except Exception as exc:
            stop_request_tracking()
            crew_failed("critic", task_id, str(exc)[:200])
            logger.warning(f"Critic review failed: {exc}")
            # On failure, return original output — don't block delivery
            return crew_output
