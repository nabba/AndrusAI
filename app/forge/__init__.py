"""Tool Forge — staged generation pipeline for agent-authored tools.

Architecture:
- Generated tools live in the database, never as Python imports into the gateway.
- Every invocation runs in a sandboxed subprocess with capability-token guards.
- A multi-phase audit pipeline (static, semantic, dynamic, composition, periodic)
  decides what state a tool can occupy.
- Three independent kill switches: env, runtime override, per-tool/per-class.
- Default-off, default-shadow, kill-sticky.

This package is TIER_IMMUTABLE. Agents can produce tools through Forge but
cannot modify Forge itself.

See docs/forge-design.md (planned) for the full design rationale.
"""

from app.forge.config import ForgeConfig, get_forge_config

__all__ = ["ForgeConfig", "get_forge_config"]
