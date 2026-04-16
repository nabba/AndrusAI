"""
deployment_tools.py — Cloud deployment and GitHub operations via bridge.

All operations execute on the host via bridge CLIs (gh, docker, fly, ssh).

Usage:
    from app.tools.deployment_tools import create_deployment_tools
    tools = create_deployment_tools("devops")
"""

import logging

logger = logging.getLogger(__name__)


def create_deployment_tools(agent_id: str) -> list:
    """Create deployment tools via bridge CLIs.

    Returns empty list if bridge is unavailable.
    """
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

    class _GitHubRepoInput(BaseModel):
        name: str = Field(description="Repository name")
        description: str = Field(default="", description="Repository description")
        private: bool = Field(default=False, description="Create as private repository")
        project_path: str = Field(
            default="",
            description="Local project path to push (if provided, initializes and pushes)",
        )

    class GitHubCreateRepoPushTool(BaseTool):
        name: str = "github_create_and_push"
        description: str = (
            "Create a new GitHub repository and optionally push a local project to it. "
            "Requires 'gh' CLI authenticated on the host."
        )
        args_schema: Type[BaseModel] = _GitHubRepoInput

        def _run(self, name: str, description: str = "", private: bool = False, project_path: str = "") -> str:
            # Create repo
            cmd = ["gh", "repo", "create", name, "--confirm"]
            if private:
                cmd.append("--private")
            else:
                cmd.append("--public")
            if description:
                cmd.extend(["--description", description])

            result = bridge.execute(cmd)
            if "error" in result:
                return f"Error creating repo: {result.get('detail', result['error'])}"

            repo_url = result.get("stdout", "").strip()

            # Push local project if provided
            if project_path:
                commands = [
                    f"cd {project_path}",
                    "git init 2>/dev/null",
                    "git add -A",
                    'git commit -m "Initial commit" 2>/dev/null',
                    f"git remote add origin https://github.com/{name}.git 2>/dev/null || git remote set-url origin https://github.com/{name}.git",
                    "git branch -M main",
                    "git push -u origin main",
                ]
                push_result = bridge.execute(["sh", "-c", " && ".join(commands)])
                if "error" in push_result:
                    return f"Repo created but push failed: {push_result.get('detail', push_result['error'])}"
                return f"Repository created and code pushed: {repo_url}"

            return f"Repository created: {repo_url}"

    class _DockerBuildInput(BaseModel):
        project_path: str = Field(description="Path to directory containing Dockerfile")
        image_name: str = Field(description="Docker image name (e.g. 'myapp:latest')")

    class DockerBuildTool(BaseTool):
        name: str = "docker_build"
        description: str = (
            "Build a Docker image from a Dockerfile in the project directory."
        )
        args_schema: Type[BaseModel] = _DockerBuildInput

        def _run(self, project_path: str, image_name: str) -> str:
            result = bridge.execute(
                ["sh", "-c", f"cd {project_path} && docker build -t {image_name} . 2>&1"]
            )
            if "error" in result:
                return f"Error: {result.get('detail', result['error'])}"
            output = result.get("stdout", "")
            if "Successfully built" in output or "Successfully tagged" in output:
                return f"Docker image built: {image_name}"
            return output[:2000]

    class _DeployInput(BaseModel):
        project_path: str = Field(description="Path to the project to deploy")
        target: str = Field(
            description="Deployment target: 'fly' (fly.io), 'ghpages' (GitHub Pages), "
            "'ssh' (SSH to server)"
        )
        host: str = Field(
            default="",
            description="For SSH: user@host. For others: leave empty.",
        )
        deploy_command: str = Field(
            default="",
            description="For SSH: command to run after uploading. Leave empty for auto-detect.",
        )

    class DeployTool(BaseTool):
        name: str = "deploy"
        description: str = (
            "Deploy a project to a cloud target. Supports: fly.io, GitHub Pages, SSH."
        )
        args_schema: Type[BaseModel] = _DeployInput

        def _run(
            self,
            project_path: str,
            target: str,
            host: str = "",
            deploy_command: str = "",
        ) -> str:
            if target == "fly":
                result = bridge.execute(
                    ["sh", "-c", f"cd {project_path} && fly deploy 2>&1"]
                )
            elif target == "ghpages":
                result = bridge.execute(
                    ["sh", "-c", f"cd {project_path} && npx gh-pages -d . 2>&1"]
                )
            elif target == "ssh" and host:
                project_name = project_path.split("/")[-1]
                # Upload via rsync then run deploy command
                cmds = [f"rsync -avz {project_path}/ {host}:~/{project_name}/"]
                if deploy_command:
                    cmds.append(f"ssh {host} '{deploy_command}'")
                result = bridge.execute(["sh", "-c", " && ".join(cmds)])
            else:
                return f"Unknown target: {target}. Use: fly, ghpages, ssh."

            if "error" in result:
                return f"Deployment error: {result.get('detail', result['error'])}"
            output = result.get("stdout", "")
            stderr = result.get("stderr", "")
            return (output + ("\n" + stderr if stderr else ""))[:2000]

    return [
        GitHubCreateRepoPushTool(),
        DockerBuildTool(),
        DeployTool(),
    ]
