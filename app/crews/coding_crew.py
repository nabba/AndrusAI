"""coding_crew.py — Code generation and sandbox execution crew."""

import logging

from app.agents.coder import create_coder
from app.crews.base_crew import run_single_agent_crew

logger = logging.getLogger(__name__)

CODING_TASK_TEMPLATE = """\
Complete the following coding task:

{user_input}

Write clean, well-documented code. Test it by executing it in the Docker sandbox.
If the code fails, debug and fix it. Save the final working code to a file using
the file_manager tool.

Return the working code along with its output.
"""


class CodingCrew:
    def run(self, task_description: str, parent_task_id: str = None, difficulty: int = 5) -> str:
        # Delegation-mode switch: Org Chart toggle.  When ON, route to
        # Coordinator + Execution + Debug specialists instead of a single
        # monolithic coder.  Any error falls back silently to single-agent.
        try:
            from app.crews.delegation_settings import is_enabled
            if is_enabled("coding"):
                from app.crews.delegated_coding import DelegatedCodingCrew
                return DelegatedCodingCrew().run(
                    task_description,
                    parent_task_id=parent_task_id,
                    difficulty=difficulty,
                )
        except Exception:
            logger.warning(
                "Delegated coding crew failed; falling back to single-agent",
                exc_info=True,
            )
        return run_single_agent_crew(
            crew_name="coding",
            agent_role="coder",
            create_agent_fn=create_coder,
            task_template=CODING_TASK_TEMPLATE,
            task_description=task_description,
            expected_output="Working code with execution output, saved to a file.",
            parent_task_id=parent_task_id,
            difficulty=difficulty,
        )
