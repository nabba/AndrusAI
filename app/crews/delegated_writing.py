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

from crewai import Crew, Task, Process

from app.agents.specialists import (
    create_writing_coordinator,
    create_writing_research_specialist,
    create_synthesis_specialist,
)
from app.sanitize import wrap_user_input
from app.crews.lifecycle import crew_lifecycle
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
        from app.llm_mode import get_mode
        force_tier = difficulty_to_tier(difficulty, get_mode())

        with crew_lifecycle(
            crew_name="writing",
            agent_role="writer",
            task_title=f"Writing (delegated): {task_description[:100]}",
            task_description=task_description,
            parent_task_id=parent_task_id,
            mode="delegated",
        ) as ctx:
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

            result_str = str(crew.kickoff())
            ctx.set_outcome(result_str)
            return result_str
