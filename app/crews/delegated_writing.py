"""
delegated_writing.py — Coordinator + Research + Synthesis specialist crew.

Engaged when delegation_settings.is_enabled("writing") is True.  The
coordinator delegates research to the Writing Research Specialist
(web + KB + philosophy), then delegates final-draft synthesis to the
shared Synthesis Specialist (same one used by the research crew —
reuses its philosophy/dialectics/tensions toolkit).
"""
from __future__ import annotations

import logging
import time as _time

from crewai import Crew, Task, Process

from app.agents.specialists import (
    create_writing_coordinator,
    create_writing_research_specialist,
    create_synthesis_specialist,
)
from app.sanitize import wrap_user_input
from app.firebase_reporter import crew_started, crew_completed, crew_failed
from app.memory.belief_state import update_belief
from app.benchmarks import record_metric
from app.conversation_store import estimate_eta
from app.llm_selector import difficulty_to_tier

logger = logging.getLogger(__name__)


_DELEGATED_WRITING_TASK_TEMPLATE = """\
Complete the following writing task:

{user_input}

Process:
1. Classify: short/simple (handle yourself) or substantive (delegate).
2. For substantive pieces:
   a. Delegate research brief to the Writing Research Specialist.
   b. Delegate final-draft synthesis to the Synthesis Specialist, passing the brief.
3. Polish, format for the destination (Signal = concise, file = markdown), and
   save via file_manager if it's a document.

OUTPUT RULES:
 - Return ONLY the finished piece — no delegation commentary.
 - Adapt length to destination: Signal messages ≤ 1500 chars, files can be longer.
 - For reports/essays, cite sources inline.
"""


class DelegatedWritingCrew:
    def run(
        self,
        task_description: str,
        parent_task_id: str | None = None,
        difficulty: int = 5,
    ) -> str:
        task_id = crew_started(
            "writing",
            f"Writing (delegated): {task_description[:100]}",
            eta_seconds=estimate_eta("writing"),
            parent_task_id=parent_task_id,
        )
        start = _time.monotonic()

        from app.llm_mode import get_mode
        force_tier = difficulty_to_tier(difficulty, get_mode())

        update_belief("writer", "working", current_task=task_description[:100])

        try:
            coordinator = create_writing_coordinator(force_tier=force_tier)
            researcher = create_writing_research_specialist(force_tier=force_tier)
            synth = create_synthesis_specialist(force_tier=force_tier)

            task = Task(
                description=_DELEGATED_WRITING_TASK_TEMPLATE.format(
                    user_input=wrap_user_input(task_description),
                ),
                expected_output=(
                    "Finished written piece, appropriately formatted for the destination."
                ),
                agent=coordinator,
            )

            crew = Crew(
                agents=[coordinator, researcher, synth],
                tasks=[task],
                process=Process.hierarchical,
                manager_llm=coordinator.llm,
                verbose=False,
            )

            result = crew.kickoff()
            result_str = str(result)

            update_belief("writer", "completed", current_task=task_description[:100])
            record_metric(
                "task_completion_time",
                _time.monotonic() - start,
                {"crew": "writing", "mode": "delegated"},
            )
            crew_completed("writing", task_id, result_str[:2000])
            return result_str

        except Exception as exc:
            update_belief("writer", "failed", current_task=task_description[:100])
            crew_failed("writing", task_id, str(exc)[:200])
            logger.exception("Delegated writing crew failed")
            raise
