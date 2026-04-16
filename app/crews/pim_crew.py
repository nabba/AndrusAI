"""pim_crew.py — Personal Information Management crew (email, calendar, tasks)."""

from app.agents.pim_agent import create_pim_agent
from app.crews.base_crew import run_single_agent_crew

PIM_TASK_TEMPLATE = """\
Handle this personal information management task:

{user_input}

You have access to:
- Email (IMAP/SMTP): check inbox, read, send, search, organize
- Calendar (macOS Calendar): list, create, search, delete events
- Tasks (local database): create, list, update, complete, search

Determine which tools are needed. Summarize findings concisely.
If the task involves sending email or creating events, confirm the details
in your response before executing.
"""


class PIMCrew:
    def run(self, task_description: str, parent_task_id: str = None, difficulty: int = 5) -> str:
        return run_single_agent_crew(
            crew_name="pim",
            agent_role="pim",
            create_agent_fn=create_pim_agent,
            task_template=PIM_TASK_TEMPLATE,
            task_description=task_description,
            expected_output="Completed PIM task with clear summary of actions taken.",
            parent_task_id=parent_task_id,
            difficulty=difficulty,
        )
