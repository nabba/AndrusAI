"""
delegated_coding.py — Coordinator + Execution + Debug specialist crew.

Engaged when delegation_settings.is_enabled("coding") is True.  Each
sub-agent stays ≤ 18 tools so Anthropic strict-mode works, and the
coordinator can loop through write → execute → debug → fix without any
single agent carrying the full 40+ tool palette.
"""
from __future__ import annotations

import logging

from crewai import Crew, Task, Process

from app.agents.specialists import (
    create_coding_coordinator,
    create_execution_specialist,
    create_debug_specialist,
)
from app.sanitize import wrap_user_input
from app.crews.lifecycle import crew_lifecycle
from app.llm_selector import difficulty_to_tier

logger = logging.getLogger(__name__)


_DELEGATED_CODING_TASK_TEMPLATE = """\
Complete the following coding task:

{user_input}

Process:
1. Write the code yourself (you have file_manager + memory tools).
2. Delegate to the Execution Specialist to RUN the code and capture real output.
3. If the run fails, delegate to the Debug Specialist for diagnosis.
4. Apply the fix and re-delegate execution.
5. When it runs clean, return the final working code + its real output.

OUTPUT RULES:
 - Return ONLY the final deliverable — working code plus actual execution output.
 - Do NOT narrate your delegation steps.
 - Save the final code to a file via file_manager if appropriate.
"""


class DelegatedCodingCrew:
    def run(
        self,
        task_description: str,
        parent_task_id: str | None = None,
        difficulty: int = 5,
    ) -> str:
        from app.llm_mode import get_mode
        force_tier = difficulty_to_tier(difficulty, get_mode())

        with crew_lifecycle(
            crew_name="coding",
            agent_role="coder",
            task_title=f"Coding (delegated): {task_description[:100]}",
            task_description=task_description,
            parent_task_id=parent_task_id,
            mode="delegated",
        ) as ctx:
            coordinator = create_coding_coordinator(force_tier=force_tier)
            executor = create_execution_specialist(force_tier=force_tier)
            debugger = create_debug_specialist(force_tier=force_tier)

            task = Task(
                description=_DELEGATED_CODING_TASK_TEMPLATE.format(
                    user_input=wrap_user_input(task_description),
                ),
                expected_output=(
                    "Working code with real execution output, saved to a file if appropriate."
                ),
                agent=coordinator,
            )

            crew = Crew(
                agents=[coordinator, executor, debugger],
                tasks=[task],
                process=Process.hierarchical,
                manager_llm=coordinator.llm,
                verbose=False,
            )

            result_str = str(crew.kickoff())
            ctx.set_outcome(result_str)
            return result_str
