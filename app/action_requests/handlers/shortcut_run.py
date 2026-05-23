"""Shortcut-run action handler.

Wraps the Desktop agent's ``run_shortcut`` tool. Apple Shortcuts can
chain arbitrary system actions (send messages, post to socials,
execute scripts), so the same gate applies.

Data payload shape::

    {"shortcut_name": "Name as it appears in Shortcuts.app",
     "input":         "<optional input string>"}
"""
from __future__ import annotations

import logging
from typing import Any

from app.action_requests.handlers.base import ActionHandler, ApplyResult
from app.action_requests.models import ActionType

logger = logging.getLogger(__name__)


_MAX_NAME_CHARS = 200
_MAX_INPUT_CHARS = 10_000


class ShortcutRunHandler(ActionHandler):
    @property
    def action_type(self):
        return ActionType.SHORTCUT_RUN

    def validate(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        name = data.get("shortcut_name")
        if not isinstance(name, str) or not name.strip():
            return False, "shortcut_name is required"
        if len(name) > _MAX_NAME_CHARS:
            return False, f"shortcut_name exceeds {_MAX_NAME_CHARS} chars"
        inp = data.get("input", "")
        if not isinstance(inp, str):
            return False, "input must be a string"
        if len(inp) > _MAX_INPUT_CHARS:
            return False, f"input exceeds {_MAX_INPUT_CHARS} chars"
        return True, None

    def apply(self, data: dict[str, Any]) -> ApplyResult:
        try:
            from app.bridge_client import get_bridge
            bridge = get_bridge("desktop")
            if not bridge or not bridge.is_available():
                return ApplyResult(ok=False, error="bridge unavailable")
        except Exception as exc:  # noqa: BLE001
            return ApplyResult(ok=False, error=f"bridge import failed: {exc}")

        name = str(data["shortcut_name"])
        inp = str(data.get("input", ""))
        cmd = ["shortcuts", "run", name]
        if inp:
            cmd.extend(["--input-path", "-"])

        try:
            if inp:
                result = bridge.execute(cmd, stdin=inp)  # type: ignore[arg-type]
            else:
                result = bridge.execute(cmd)
        except TypeError:
            # bridge.execute() may not support stdin in older builds; fall
            # back to "echo input | shortcuts run name".
            shell = f"echo {inp!r} | shortcuts run {name!r}" if inp else f"shortcuts run {name!r}"
            try:
                result = bridge.execute(["sh", "-c", shell])
            except Exception as exc:  # noqa: BLE001
                return ApplyResult(ok=False, error=f"bridge raised: {exc}")
        except Exception as exc:  # noqa: BLE001
            return ApplyResult(ok=False, error=f"bridge raised: {exc}")

        if "error" in result:
            return ApplyResult(
                ok=False,
                error=str(result.get("detail", result["error"]))[:500],
            )
        stdout = str(result.get("stdout", "")).strip()
        return ApplyResult(ok=True, artifact={"stdout": stdout[:2000]})

    def render_summary(self, data: dict[str, Any]) -> str:
        return f"⚡ Shortcut: {data.get('shortcut_name', '?')}"
