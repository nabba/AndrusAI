"""Forge config: env vars are the ceiling, runtime override is more restrictive.

Reads TOOL_FORGE_* env vars directly — does not extend the central Settings
model, because app/config.py is TIER_IMMUTABLE and Forge wants to be added
to that tier without forcing edits there. The config is re-read per request
(cheap, env doesn't change at runtime; runtime override is queried from DB).
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _bool_env(key: str, default: bool) -> bool:
    raw = os.environ.get(key, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _int_env(key: str, default: int) -> int:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(key: str, default: float) -> float:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _list_env(key: str, default: list[str] | None = None) -> list[str]:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return list(default or [])
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class ForgeConfig:
    """Resolved Forge configuration.

    `enabled` here reflects ENV only. The effective enabled state is
    ``enabled AND runtime_override_enabled`` — env can disable, override
    cannot enable past env.
    """
    enabled: bool
    require_human_promotion: bool
    auto_promote_allowed_classes: list[str]
    max_tools: int
    max_calls_per_tool_per_hour: int
    max_tools_per_plan: int
    audit_llm: str
    shadow_runs_required: int
    dry_run: bool
    composition_risk_threshold: float
    blocked_domains: list[str]
    allowed_domains: list[str]


def get_forge_config() -> ForgeConfig:
    """Snapshot current env-derived config. Cheap; safe to call per request."""
    return ForgeConfig(
        enabled=_bool_env("TOOL_FORGE_ENABLED", False),
        require_human_promotion=_bool_env("TOOL_FORGE_REQUIRE_HUMAN_PROMOTION", True),
        auto_promote_allowed_classes=_list_env("TOOL_FORGE_AUTO_PROMOTE_ALLOWED_CLASSES"),
        max_tools=_int_env("TOOL_FORGE_MAX_TOOLS", 50),
        max_calls_per_tool_per_hour=_int_env("TOOL_FORGE_MAX_CALLS_PER_TOOL_PER_HOUR", 100),
        max_tools_per_plan=_int_env("TOOL_FORGE_MAX_TOOLS_PER_PLAN", 3),
        audit_llm=os.environ.get("TOOL_FORGE_AUDIT_LLM", "claude-sonnet-4-6"),
        shadow_runs_required=_int_env("TOOL_FORGE_SHADOW_RUNS_REQUIRED", 5),
        dry_run=_bool_env("TOOL_FORGE_DRY_RUN", False),
        composition_risk_threshold=_float_env("TOOL_FORGE_COMPOSITION_RISK_THRESHOLD", 4.0),
        blocked_domains=_list_env("TOOL_FORGE_BLOCKED_DOMAINS"),
        allowed_domains=_list_env("TOOL_FORGE_ALLOWED_DOMAINS"),
    )
