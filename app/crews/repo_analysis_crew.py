"""repo_analysis_crew.py — Repository analysis and reporting crew."""

from app.agents.repo_analyst import create_repo_analyst
from app.crews.base_crew import run_single_agent_crew

REPO_ANALYSIS_TASK_TEMPLATE = """\
Analyze this repository:

{user_input}

You have access to:
- Clone repos (git clone via bridge)
- Analyze structure: file tree, tech stack, language breakdown
- Compute metrics: LOC, dependencies, project health indicators
- Generate architecture diagrams (DOT format)
- GitHub CLI: repos, PRs, issues, releases

Approach:
1. Clone the repository (shallow clone for speed).
2. Analyze project structure and tech stack.
3. Compute key metrics.
4. Generate an architecture diagram if appropriate.
5. Summarize findings with specific numbers and observations.
"""


class RepoAnalysisCrew:
    def run(self, task_description: str, parent_task_id: str = None, difficulty: int = 5) -> str:
        return run_single_agent_crew(
            crew_name="repo_analysis",
            agent_role="repo_analyst",
            create_agent_fn=create_repo_analyst,
            task_template=REPO_ANALYSIS_TASK_TEMPLATE,
            task_description=task_description,
            expected_output="Repository analysis with structure, tech stack, metrics, and diagram.",
            parent_task_id=parent_task_id,
            difficulty=difficulty,
        )
