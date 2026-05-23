"""JXA-exec action handler.

Wraps the Desktop agent's ``run_jxa`` tool. JXA (JavaScript for
Automation) is Apple's JavaScript-based equivalent to AppleScript;
identical blast radius (Mail send, Finder file ops, System Events).

Data payload shape::

    {"script": "<jxa source>"}
"""
from __future__ import annotations

import logging
from typing import Any

from app.action_requests.handlers.base import ActionHandler, ApplyResult
from app.action_requests.models import ActionType

logger = logging.getLogger(__name__)


_MAX_SCRIPT_CHARS = 20_000


class JxaExecHandler(ActionHandler):
    @property
    def action_type(self):
        return ActionType.JXA_EXEC

    def validate(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        script = data.get("script")
        if not isinstance(script, str) or not script.strip():
            return False, "script is required and must be a non-empty string"
        if len(script) > _MAX_SCRIPT_CHARS:
            return False, f"script exceeds {_MAX_SCRIPT_CHARS} chars"
        return True, None

    def apply(self, data: dict[str, Any]) -> ApplyResult:
        try:
            from app.bridge_client import get_bridge
            bridge = get_bridge("desktop")
            if not bridge or not bridge.is_available():
                return ApplyResult(ok=False, error="bridge unavailable")
        except Exception as exc:  # noqa: BLE001
            return ApplyResult(ok=False, error=f"bridge import failed: {exc}")

        script = str(data["script"])
        try:
            result = bridge.execute(["osascript", "-l", "JavaScript", "-e", script])
        except Exception as exc:  # noqa: BLE001
            return ApplyResult(ok=False, error=f"bridge raised: {exc}")
        if "error" in result:
            return ApplyResult(
                ok=False,
                error=str(result.get("detail", result["error"]))[:500],
            )
        stdout = str(result.get("stdout", "")).strip()
        stderr = str(result.get("stderr", "")).strip()
        if stderr and not stdout:
            return ApplyResult(ok=False, error=f"JXA error: {stderr[:500]}")
        return ApplyResult(ok=True, artifact={"stdout": stdout[:2000]})

    def render_summary(self, data: dict[str, Any]) -> str:
        first_line = (data.get("script") or "").strip().split("\n", 1)[0][:80]
        return f"🍎 JXA: {first_line}"
