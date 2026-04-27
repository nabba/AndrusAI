"""Per-tool rate limit. Counts invocations in the last hour from forge_invocations.

Cheap because forge_invocations is indexed on (tool_id, created_at DESC).
"""
from __future__ import annotations

from app.control_plane.db import execute_scalar
from app.forge.config import get_forge_config


def calls_in_last_hour(tool_id: str) -> int:
    return int(
        execute_scalar(
            """
            SELECT COUNT(*) FROM forge_invocations
            WHERE tool_id = %s AND created_at > NOW() - INTERVAL '1 hour'
            """,
            (tool_id,),
        ) or 0
    )


def check_budget(tool_id: str) -> tuple[bool, str]:
    cfg = get_forge_config()
    used = calls_in_last_hour(tool_id)
    if used >= cfg.max_calls_per_tool_per_hour:
        return False, (
            f"per-tool budget exhausted: {used}/{cfg.max_calls_per_tool_per_hour} "
            f"calls in the last hour"
        )
    return True, f"{used}/{cfg.max_calls_per_tool_per_hour} used"
