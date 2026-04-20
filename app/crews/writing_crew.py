"""writing_crew.py — Content creation crew for summaries, reports, emails."""

import logging

from app.agents.writer import create_writer
from app.crews.base_crew import run_single_agent_crew

logger = logging.getLogger(__name__)

WRITING_TASK_TEMPLATE = """\
Complete the following writing task:

{user_input}

First, check team memory for any relevant research or context. Then write clear,
well-structured content. Adapt the length and format based on the destination:
- Signal messages: concise, under 1500 characters
- Files: can be longer, use Markdown formatting

If the output is a document or report, save it using the file_manager tool.
"""


class WritingCrew:
    def run(self, task_description: str, parent_task_id: str = None, difficulty: int = 5) -> str:
        # Delegation-mode switch: Org Chart toggle.  When ON, route to
        # Coordinator + Research + Synthesis specialists.  Silent fallback
        # to single-agent on any error.
        try:
            from app.crews.delegation_settings import is_enabled
            if is_enabled("writing"):
                from app.crews.delegated_writing import DelegatedWritingCrew
                return DelegatedWritingCrew().run(
                    task_description,
                    parent_task_id=parent_task_id,
                    difficulty=difficulty,
                )
        except Exception:
            logger.warning(
                "Delegated writing crew failed; falling back to single-agent",
                exc_info=True,
            )
        return run_single_agent_crew(
            crew_name="writing",
            agent_role="writer",
            create_agent_fn=create_writer,
            task_template=WRITING_TASK_TEMPLATE,
            task_description=task_description,
            expected_output="Well-written content appropriate for the destination format.",
            parent_task_id=parent_task_id,
            difficulty=difficulty,
        )
