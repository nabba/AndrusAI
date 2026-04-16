"""
ci_cd_tools.py — CI/CD configuration generation tools.

Generates GitHub Actions workflows, Dockerfiles, and Makefiles
by analyzing project structure. Pure code generation — no external deps.

Usage:
    from app.tools.ci_cd_tools import create_ci_cd_tools
    tools = create_ci_cd_tools("devops")
"""

import logging

logger = logging.getLogger(__name__)


def create_ci_cd_tools(agent_id: str) -> list:
    """Create CI/CD configuration generation tools via bridge."""
    try:
        from app.bridge_client import get_bridge
        bridge = get_bridge(agent_id)
        if not bridge:
            return []
        if not bridge.is_available():
            return []
    except Exception:
        return []

    try:
        from crewai.tools import BaseTool
        from pydantic import BaseModel, Field
        from typing import Type
    except ImportError:
        return []

    # ── Tool definitions ──────────────────────────────────────────

    class _GHActionsInput(BaseModel):
        project_path: str = Field(description="Path to the project")
        workflow_type: str = Field(
            default="ci",
            description="Workflow type: 'ci' (test on push), 'cd' (deploy on release), 'full' (both)",
        )

    class GenerateGitHubActionsTool(BaseTool):
        name: str = "generate_github_actions"
        description: str = (
            "Generate a GitHub Actions CI/CD workflow for the project. "
            "Analyzes project type and creates .github/workflows/ci.yml."
        )
        args_schema: Type[BaseModel] = _GHActionsInput

        def _run(self, project_path: str, workflow_type: str = "ci") -> str:
            # Detect project type
            has_pyproject = "error" not in bridge.execute(["test", "-f", f"{project_path}/pyproject.toml"])
            has_package_json = "error" not in bridge.execute(["test", "-f", f"{project_path}/package.json"])
            has_dockerfile = "error" not in bridge.execute(["test", "-f", f"{project_path}/Dockerfile"])

            if has_pyproject:
                workflow = _python_ci_workflow(has_dockerfile, workflow_type)
            elif has_package_json:
                workflow = _node_ci_workflow(has_dockerfile, workflow_type)
            else:
                workflow = _generic_ci_workflow()

            # Write the workflow file
            workflow_dir = f"{project_path}/.github/workflows"
            bridge.execute(["mkdir", "-p", workflow_dir])
            result = bridge.write_file(f"{workflow_dir}/ci.yml", workflow, create_dirs=True)
            if "error" in result:
                return f"Error writing workflow: {result.get('detail', result['error'])}"

            return f"GitHub Actions workflow created at .github/workflows/ci.yml\n\n{workflow}"

    class _DockerfileInput(BaseModel):
        project_path: str = Field(description="Path to the project")

    class GenerateDockerfileTool(BaseTool):
        name: str = "generate_dockerfile"
        description: str = (
            "Generate a Dockerfile for the project based on its tech stack."
        )
        args_schema: Type[BaseModel] = _DockerfileInput

        def _run(self, project_path: str) -> str:
            has_pyproject = "error" not in bridge.execute(["test", "-f", f"{project_path}/pyproject.toml"])
            has_package_json = "error" not in bridge.execute(["test", "-f", f"{project_path}/package.json"])

            if has_pyproject:
                dockerfile = _python_dockerfile()
            elif has_package_json:
                dockerfile = _node_dockerfile()
            else:
                return "Cannot auto-generate Dockerfile: unknown project type."

            result = bridge.write_file(f"{project_path}/Dockerfile", dockerfile, create_dirs=True)
            if "error" in result:
                return f"Error: {result.get('detail', result['error'])}"
            return f"Dockerfile created:\n\n{dockerfile}"

    class _MakefileInput(BaseModel):
        project_path: str = Field(description="Path to the project")

    class GenerateMakefileTool(BaseTool):
        name: str = "generate_makefile"
        description: str = (
            "Generate a Makefile with standard targets (install, test, build, clean, run)."
        )
        args_schema: Type[BaseModel] = _MakefileInput

        def _run(self, project_path: str) -> str:
            has_pyproject = "error" not in bridge.execute(["test", "-f", f"{project_path}/pyproject.toml"])
            has_package_json = "error" not in bridge.execute(["test", "-f", f"{project_path}/package.json"])

            if has_pyproject:
                makefile = _python_makefile()
            elif has_package_json:
                makefile = _node_makefile()
            else:
                makefile = _generic_makefile()

            result = bridge.write_file(f"{project_path}/Makefile", makefile, create_dirs=True)
            if "error" in result:
                return f"Error: {result.get('detail', result['error'])}"
            return f"Makefile created:\n\n{makefile}"

    return [
        GenerateGitHubActionsTool(),
        GenerateDockerfileTool(),
        GenerateMakefileTool(),
    ]


# ── Workflow templates ────────────────────────────────────────────

def _python_ci_workflow(has_docker: bool, wf_type: str) -> str:
    return f"""\
name: CI
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{{{ matrix.python-version }}}}
      - run: pip install -e ".[dev]" 2>/dev/null || pip install -e .
      - run: python -m pytest tests/ -v
"""


def _node_ci_workflow(has_docker: bool, wf_type: str) -> str:
    return f"""\
name: CI
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        node-version: [18, 20]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: ${{{{ matrix.node-version }}}}
      - run: npm ci
      - run: npm test
"""


def _generic_ci_workflow() -> str:
    return """\
name: CI
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo "Add build steps here"
"""


def _python_dockerfile() -> str:
    return """\
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .
CMD ["python", "-m", "your_module"]
"""


def _node_dockerfile() -> str:
    return """\
FROM node:20-slim
WORKDIR /app
COPY package*.json .
RUN npm ci --production
COPY . .
EXPOSE 3000
CMD ["node", "src/index.js"]
"""


def _python_makefile() -> str:
    return """\
.PHONY: install test build clean run

install:
\tpip install -e ".[dev]" 2>/dev/null || pip install -e .

test:
\tpython -m pytest tests/ -v

build:
\tpython -m build

clean:
\trm -rf dist/ *.egg-info/ __pycache__/

run:
\tpython -m $(shell basename $(CURDIR))
"""


def _node_makefile() -> str:
    return """\
.PHONY: install test build clean run

install:
\tnpm install

test:
\tnpm test

build:
\tnpm run build

clean:
\trm -rf node_modules/ dist/

run:
\tnpm start
"""


def _generic_makefile() -> str:
    return """\
.PHONY: build test clean

build:
\t@echo "Add build commands"

test:
\t@echo "Add test commands"

clean:
\t@echo "Add clean commands"
"""
