"""devops_crew.py — Build, test, and deployment crew."""

from app.agents.devops_agent import create_devops_agent
from app.crews.base_crew import run_single_agent_crew

DEVOPS_TASK_TEMPLATE = """\
Complete this DevOps task:

{user_input}

You have access to:
- Project scaffolding: create from templates (python-cli, python-api, node-api, static-site)
- Build operations: install deps, build, test, package
- GitHub: create repos, push code
- Docker: build images
- Deployment: fly.io, GitHub Pages, SSH
- CI/CD: generate GitHub Actions workflows, Dockerfiles, Makefiles

Approach:
1. Understand the project requirements.
2. Scaffold if creating new; analyze if deploying existing.
3. Build and test before deploying.
4. Set up CI/CD if appropriate.
5. Report what was done and any manual steps needed.
"""


class DevOpsCrew:
    def run(self, task_description: str, parent_task_id: str = None, difficulty: int = 5) -> str:
        return run_single_agent_crew(
            crew_name="devops",
            agent_role="devops",
            create_agent_fn=create_devops_agent,
            task_template=DEVOPS_TASK_TEMPLATE,
            task_description=task_description,
            expected_output="DevOps task completed with build/deploy status and next steps.",
            parent_task_id=parent_task_id,
            difficulty=difficulty,
        )
