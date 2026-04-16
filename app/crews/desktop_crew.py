"""desktop_crew.py — macOS desktop automation crew."""

from app.agents.desktop_agent import create_desktop_agent
from app.crews.base_crew import run_single_agent_crew

DESKTOP_TASK_TEMPLATE = """\
Complete this macOS desktop automation task:

{user_input}

You have access to:
- AppleScript: control any scriptable macOS application
- JXA (JavaScript for Automation): complex automation with modern syntax
- Screenshots: capture desktop state for verification
- Clipboard: read/write macOS clipboard
- Apple Shortcuts: run pre-built automation workflows
- Application control: open, close, focus, manage windows

Approach:
1. Plan the automation steps before executing.
2. Execute one step at a time.
3. Use screen_capture to verify results when needed.
4. Report what was done and the current state.
"""


class DesktopCrew:
    def run(self, task_description: str, parent_task_id: str = None, difficulty: int = 5) -> str:
        return run_single_agent_crew(
            crew_name="desktop",
            agent_role="desktop",
            create_agent_fn=create_desktop_agent,
            task_template=DESKTOP_TASK_TEMPLATE,
            task_description=task_description,
            expected_output="Automation task completed with description of actions taken.",
            parent_task_id=parent_task_id,
            difficulty=difficulty,
        )
