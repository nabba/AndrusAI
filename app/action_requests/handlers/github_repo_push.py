"""GitHub create-repo-and-push action handler.

Wraps the DevOps agent's ``github_create_and_push`` tool. Creates a
remote GitHub repository and (optionally) pushes a local project to
it — both external-blast operations.

Data payload shape::

    {
        "name":         "myorg/myrepo" | "myrepo",
        "description":  "...",        # optional
        "private":      false,
        "project_path": "/abs/path",  # optional; empty = just create empty repo
    }
"""
from __future__ import annotations

import logging
import re
from typing import Any

from app.action_requests.handlers.base import ActionHandler, ApplyResult
from app.action_requests.models import ActionType

logger = logging.getLogger(__name__)


_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?$")


class GitHubRepoPushHandler(ActionHandler):
    @property
    def action_type(self):
        return ActionType.GITHUB_REPO_PUSH

    def validate(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            return False, "name is required (repo or org/repo)"
        if not _REPO_NAME_RE.match(name):
            return False, f"invalid repo name: {name!r}"
        private = data.get("private", False)
        if not isinstance(private, bool):
            return False, "private must be boolean"
        project_path = data.get("project_path", "")
        if not isinstance(project_path, str):
            return False, "project_path must be a string"
        return True, None

    def apply(self, data: dict[str, Any]) -> ApplyResult:
        try:
            from app.bridge_client import get_bridge
            bridge = get_bridge("devops")
            if not bridge or not bridge.is_available():
                return ApplyResult(ok=False, error="bridge unavailable")
        except Exception as exc:  # noqa: BLE001
            return ApplyResult(ok=False, error=f"bridge import failed: {exc}")

        name = str(data["name"])
        description = str(data.get("description", ""))
        private = bool(data.get("private", False))
        project_path = str(data.get("project_path", ""))

        cmd = ["gh", "repo", "create", name, "--confirm"]
        cmd.append("--private" if private else "--public")
        if description:
            cmd.extend(["--description", description])

        try:
            result = bridge.execute(cmd)
        except Exception as exc:  # noqa: BLE001
            return ApplyResult(ok=False, error=f"bridge raised: {exc}")
        if "error" in result:
            return ApplyResult(
                ok=False,
                error=f"create failed: {result.get('detail', result['error'])}",
            )
        repo_url = str(result.get("stdout", "")).strip()

        if not project_path:
            return ApplyResult(ok=True, artifact={"repo_url": repo_url})

        commands = [
            f"cd {project_path}",
            "git init 2>/dev/null",
            "git add -A",
            'git commit -m "Initial commit" 2>/dev/null',
            (
                f"git remote add origin https://github.com/{name}.git 2>/dev/null || "
                f"git remote set-url origin https://github.com/{name}.git"
            ),
            "git branch -M main",
            "git push -u origin main",
        ]
        try:
            push_result = bridge.execute(["sh", "-c", " && ".join(commands)])
        except Exception as exc:  # noqa: BLE001
            return ApplyResult(
                ok=False,
                error=f"repo created at {repo_url} but push raised: {exc}",
            )
        if "error" in push_result:
            return ApplyResult(
                ok=False,
                error=(
                    f"repo created at {repo_url} but push failed: "
                    f"{push_result.get('detail', push_result['error'])}"
                ),
            )
        return ApplyResult(
            ok=True,
            artifact={"repo_url": repo_url, "pushed": True},
        )

    def render_summary(self, data: dict[str, Any]) -> str:
        vis = "private" if data.get("private") else "public"
        action = "create+push" if data.get("project_path") else "create"
        return f"🐙 GitHub {action} {vis}: {data.get('name', '?')}"
