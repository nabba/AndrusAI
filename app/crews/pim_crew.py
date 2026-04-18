"""pim_crew.py — Personal Information Management crew (email, calendar, tasks)."""

from app.agents.pim_agent import create_pim_agent
from app.crews.base_crew import run_single_agent_crew

PIM_TASK_TEMPLATE = """\
Handle this personal information management task:

{user_input}

You have access to:
- Email (IMAP/SMTP): check inbox, read, send, search, organize.
  The check_email tool supports hours_back, days_back, and count_only
  parameters — use them for time-windowed queries like "emails from last
  3 hours" or count-only questions like "how many emails today".
- Calendar (macOS Calendar): list, create, search, delete events
- Tasks (local database): create, list, update, complete, search

CRITICAL: You DO have email/calendar/task tools — they are loaded in your
tool list.  If a tool call fails, report the ACTUAL error message from the
tool.  NEVER respond with "integration is not connected" or "tool not
available" without first calling the tool and getting an error response.
If you are asked about emails/calendar/tasks, USE the tools before
answering.

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
