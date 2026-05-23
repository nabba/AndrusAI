"""Deploy action handler.

Wraps the DevOps agent's ``deploy`` tool. Executes via the host
bridge (same path the original tool used) only after operator
approval.

Data payload shape::

    {
        "project_path":   "/abs/path/to/project",
        "target":         "fly" | "ghpages" | "ssh",
        "host":           "user@example.com",   # ssh only
        "deploy_command": "supervisorctl ...",   # ssh only, optional
    }
"""
from __future__ import annotations

import logging
from typing import Any

from app.action_requests.handlers.base import ActionHandler, ApplyResult
from app.action_requests.models import ActionType

logger = logging.getLogger(__name__)


_VALID_TARGETS = ("fly", "ghpages", "ssh")


class DeployHandler(ActionHandler):
    @property
    def action_type(self):
        return ActionType.DEPLOY

    def validate(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        project_path = data.get("project_path")
        if not isinstance(project_path, str) or not project_path.strip():
            return False, "project_path is required"
        target = data.get("target")
        if target not in _VALID_TARGETS:
            return False, f"target must be one of {_VALID_TARGETS}"
        if target == "ssh":
            host = data.get("host")
            if not isinstance(host, str) or "@" not in host:
                return False, "host (user@host) is required for ssh target"
        return True, None

    def apply(self, data: dict[str, Any]) -> ApplyResult:
        try:
            from app.bridge_client import get_bridge
            bridge = get_bridge("devops")
            if not bridge or not bridge.is_available():
                return ApplyResult(ok=False, error="bridge unavailable")
        except Exception as exc:  # noqa: BLE001
            return ApplyResult(ok=False, error=f"bridge import failed: {exc}")

        project_path = str(data["project_path"])
        target = str(data["target"])
        host = str(data.get("host", ""))
        deploy_command = str(data.get("deploy_command", ""))

        if target == "fly":
            cmd = ["sh", "-c", f"cd {project_path} && fly deploy 2>&1"]
        elif target == "ghpages":
            cmd = ["sh", "-c", f"cd {project_path} && npx gh-pages -d . 2>&1"]
        else:  # ssh, validated above
            project_name = project_path.rstrip("/").split("/")[-1]
            cmds = [f"rsync -avz {project_path}/ {host}:~/{project_name}/"]
            if deploy_command:
                cmds.append(f"ssh {host} '{deploy_command}'")
            cmd = ["sh", "-c", " && ".join(cmds)]

        try:
            result = bridge.execute(cmd)
        except Exception as exc:  # noqa: BLE001
            logger.warning("deploy: bridge.execute raised: %s", exc, exc_info=True)
            return ApplyResult(ok=False, error=f"bridge raised: {exc}")

        if "error" in result:
            return ApplyResult(
                ok=False,
                error=str(result.get("detail", result["error"]))[:500],
            )
        stdout = str(result.get("stdout", ""))[:2000]
        stderr = str(result.get("stderr", ""))[:500]
        return ApplyResult(
            ok=True,
            artifact={"target": target, "stdout_tail": stdout[-400:], "stderr_tail": stderr[-200:]},
        )

    def render_summary(self, data: dict[str, Any]) -> str:
        target = data.get("target", "?")
        host_part = f" → {data.get('host')}" if target == "ssh" else ""
        return f"🚀 deploy {target}{host_part}: {data.get('project_path', '?')}"
