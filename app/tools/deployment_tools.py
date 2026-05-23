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
            "Routed through the operator gate (app.external_action_gate): "
            "creating an external GitHub repo is an irreversible external action "
            "and is queued as a pending action_request until the operator approves."
        )
        args_schema: Type[BaseModel] = _GitHubRepoInput

        def _run(self, name: str, description: str = "", private: bool = False, project_path: str = "") -> str:
            from app.action_requests.models import ActionType
            from app.external_action_gate import request_external_action

            action = "create+push" if project_path else "create"
            visibility = "private" if private else "public"
            return request_external_action(
                requestor=f"devops:{agent_id}",
                action_type=ActionType.GITHUB_REPO_PUSH,
                summary=f"🐙 GitHub {action} {visibility}: {name}",
                data={
                    "name": name,
                    "description": description,
                    "private": bool(private),
                    "project_path": project_path,
                },
                reason=(
                    "DevOps github_create_and_push — creating an external "
                    "GitHub repo (and optionally pushing code) is an "
                    "irreversible external action requiring operator approval."
                ),
            )

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
            "Deploy a project to a cloud target (fly.io, GitHub Pages, or SSH). "
            "Routed through the operator gate (app.external_action_gate): "
            "deployments transmit data externally and are queued as a pending "
            "action_request until the operator approves."
        )
        args_schema: Type[BaseModel] = _DeployInput

        def _run(
            self,
            project_path: str,
            target: str,
            host: str = "",
            deploy_command: str = "",
        ) -> str:
            from app.action_requests.models import ActionType
            from app.external_action_gate import request_external_action

            if target not in ("fly", "ghpages", "ssh"):
                return f"Unknown target: {target}. Use: fly, ghpages, ssh."
            if target == "ssh" and not host:
                return "host (user@host) is required for ssh target"

            host_part = f" → {host}" if target == "ssh" else ""
            return request_external_action(
                requestor=f"devops:{agent_id}",
                action_type=ActionType.DEPLOY,
                summary=f"🚀 deploy {target}{host_part}: {project_path}",
                data={
                    "project_path": project_path,
                    "target": target,
                    "host": host,
                    "deploy_command": deploy_command,
                },
                reason=(
                    f"DevOps deploy to {target} — external transmission "
                    "of code/build artifact requires operator approval."
                ),
            )

    return [
        GitHubCreateRepoPushTool(),
        DockerBuildTool(),
        DeployTool(),
    ]
