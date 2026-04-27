"""Three-layer kill resolver.

Resolution order (most restrictive wins):
  1. ENV (TOOL_FORGE_ENABLED)              — ceiling
  2. Runtime override (forge_settings)     — can disable, can't enable past env
  3. Per-tool status (KILLED is sticky)    — final gate per invocation

The is_invocation_allowed function is checked at every forged-tool call.
Cached for 5s so high-frequency calls don't hammer the DB.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from app.forge.config import get_forge_config
from app.forge.registry import get_setting, get_tool


_CACHE_TTL_SECONDS = 5.0
_state_cache: dict[str, tuple[float, "ForgeEffectiveState"]] = {}


@dataclass(frozen=True)
class ForgeEffectiveState:
    env_enabled: bool
    runtime_enabled: bool
    effective_enabled: bool
    dry_run: bool
    explanation: str


def get_effective_state() -> ForgeEffectiveState:
    """Resolve the layered toggle. Cached for ``_CACHE_TTL_SECONDS``."""
    cached = _state_cache.get("global")
    now = time.monotonic()
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    cfg = get_forge_config()
    runtime_override = get_setting("forge_runtime_enabled")
    runtime_dry = get_setting("forge_runtime_dry_run")

    runtime_enabled = bool(runtime_override) if runtime_override is not None else cfg.enabled
    effective = cfg.enabled and runtime_enabled
    dry_run = cfg.dry_run or bool(runtime_dry)

    parts = [f"env={'on' if cfg.enabled else 'off'}"]
    parts.append(f"runtime_override={'set' if runtime_override is not None else 'unset'}")
    parts.append(f"runtime={'on' if runtime_enabled else 'off'}")
    parts.append(f"effective={'on' if effective else 'off'}")
    if dry_run:
        parts.append("dry_run=on")
    explanation = " | ".join(parts)

    state = ForgeEffectiveState(
        env_enabled=cfg.enabled,
        runtime_enabled=runtime_enabled,
        effective_enabled=effective,
        dry_run=dry_run,
        explanation=explanation,
    )
    _state_cache["global"] = (now, state)
    return state


def invalidate_cache() -> None:
    """Force the next get_effective_state() to re-read the DB."""
    _state_cache.clear()


def is_invocation_allowed(tool_id: str) -> tuple[bool, str]:
    """Final gate before a tool is invoked.

    Returns (allowed, reason). ``reason`` is human-readable; safe to surface in UI.
    """
    state = get_effective_state()
    if not state.effective_enabled:
        return False, f"forge disabled ({state.explanation})"

    row = get_tool(tool_id)
    if not row:
        return False, "tool not found"
    if row["status"] == "KILLED":
        reason = row.get("killed_reason") or "killed"
        return False, f"tool is KILLED: {reason}"
    if row["status"] in ("DRAFT", "QUARANTINED"):
        return False, f"tool not yet promoted (status={row['status']})"
    return True, f"allowed (status={row['status']}, dry_run={state.dry_run})"
