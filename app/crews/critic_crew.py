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
import time

from crewai import Task, Crew, Process
from app.agents.critic import create_critic
from app.firebase_reporter import crew_started, crew_completed, crew_failed
from app.rate_throttle import start_request_tracking, stop_request_tracking

logger = logging.getLogger(__name__)


class CriticCrew:
    """Adversarial review of agent output before delivery."""

    def review(self, original_task: str, crew_output: str,
               crew_used: str = "", difficulty: int = 5,
               parent_task_id: str = None) -> str:
        """Review crew output and return cleaned/annotated version.

        Args:
            original_task: What the user originally asked
            crew_output: The crew's response to review
            crew_used: Which crew produced the output
            difficulty: Task difficulty (for context)
            parent_task_id: For task tracking hierarchy

        Returns:
            The original output with critic annotations appended,
            or the original output unchanged if review finds no issues.
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
                    f"If no major issues: reply with ONLY 'PASS'.\n"
                    f"If issues found: list them concisely with severity "
                    f"(critical/improvement/minor) and specific fixes."
                ),
                expected_output=(
                    "Either 'PASS' if the output is good, or a concise list of "
                    "issues with severity levels and suggested fixes."
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

            # If critic says PASS, return original output unchanged
            if result.upper().startswith("PASS"):
                logger.info(f"Critic review: PASS ({duration:.1f}s)")
                return crew_output

            # Otherwise append critic notes
            logger.info(f"Critic review: {len(result)} chars of feedback ({duration:.1f}s)")
            return crew_output

        except Exception as exc:
            stop_request_tracking()
            crew_failed("critic", task_id, str(exc)[:200])
            logger.warning(f"Critic review failed: {exc}")
            # On failure, return original output — don't block delivery
            return crew_output
