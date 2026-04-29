"""
delegated_coding.py — Design + Coordinator + Execution + Debug crew.

Engaged when delegation_settings.is_enabled("coding") is True. Each
sub-agent stays ≤ 18 tools so Anthropic strict-mode works.

The pipeline runs in two phases:

  Phase 1 (Design)  : Design Specialist produces a technical spec.
                      No code is written — just a contract for Phase 2.
  Phase 2 (Code)    : Coordinator implements against the spec, delegating
                      to Execution Specialist for runs and Debug Specialist
                      on failures.

Splitting design from implementation reduces BadRequestError and TimeoutError
on complex tasks where a single agent otherwise tries to think and code
simultaneously and exceeds the model's effective working memory.

This is the same idea as the rejected exp_202604290007_1172, refactored
into the existing crew rather than a parallel CrewAI-bypassing module.
"""
from __future__ import annotations

import logging

from crewai import Crew, Task, Process

from app.agents.specialists import (
    create_coding_coordinator,
    create_design_specialist,
    create_execution_specialist,
    create_debug_specialist,
)
from app.sanitize import wrap_user_input
from app.crews.lifecycle import crew_lifecycle
from app.llm_selector import difficulty_to_tier

logger = logging.getLogger(__name__)

# Difficulty threshold above which the explicit Design phase runs.
# Trivial tasks (difficulty ≤ this) go straight to implementation to
# avoid spending an LLM round-trip on a one-line spec.
_DESIGN_PHASE_DIFFICULTY_FLOOR = 5

_DESIGN_TASK_TEMPLATE = """\
Produce a TECHNICAL SPECIFICATION for the following coding task.
Do NOT write code yet — only the spec.

{user_input}

Required sections (keep each short):
1. Summary
2. Assumptions / non-goals
3. File-by-file proposed changes
4. Key APIs / interfaces
5. Error handling and validation
6. Testing plan
7. Risks and mitigations

If the task is trivial, say so and produce a one-line spec.
"""

_DELEGATED_CODING_TASK_TEMPLATE = """\
Implement the following coding task.

{user_input}

{design_spec_section}\
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

            # Phase 1 — Design (skipped for trivial tasks)
            design_spec = self._maybe_run_design_phase(
                task_description=task_description,
                difficulty=difficulty,
                force_tier=force_tier,
            )

            # Phase 2 — Coordinator implements, delegates run/debug as needed
            spec_section = (
                f"Design spec to follow (the implementer's contract):\n{design_spec}\n\n"
                if design_spec
                else ""
            )
            task = Task(
                description=_DELEGATED_CODING_TASK_TEMPLATE.format(
                    user_input=wrap_user_input(task_description),
                    design_spec_section=spec_section,
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

    def _maybe_run_design_phase(
        self,
        *,
        task_description: str,
        difficulty: int,
        force_tier: str | None,
    ) -> str:
        """Run the Design Specialist for non-trivial tasks. Return the spec text.

        Returns an empty string when the design phase is skipped (trivial task)
        or when the design call fails — in either case the implementer falls
        back to its previous behaviour.
        """
        if difficulty < _DESIGN_PHASE_DIFFICULTY_FLOOR:
            return ""

        try:
            designer = create_design_specialist(force_tier=force_tier)
            design_task = Task(
                description=_DESIGN_TASK_TEMPLATE.format(
                    user_input=wrap_user_input(task_description),
                ),
                expected_output=(
                    "A concise technical specification covering the seven required sections."
                ),
                agent=designer,
            )
            design_crew = Crew(
                agents=[designer],
                tasks=[design_task],
                process=Process.sequential,
                verbose=False,
            )
            spec = str(design_crew.kickoff()).strip()
            if not spec:
                return ""
            logger.info(f"delegated_coding: design phase produced {len(spec)} chars of spec")
            return spec
        except Exception as exc:
            # Graceful degradation — never let the design phase break the run.
            logger.warning(f"delegated_coding: design phase failed, proceeding without spec: {exc}")
            return ""
