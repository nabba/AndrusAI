"""
llm_factory.py — Role-based LLM provider with containerized fleet management.

Architecture:
  Commander:     always Claude (routing needs max intelligence)
  Specialists:   local Ollama models via containerized fleet
                 (each model runs in its own Docker container)
  Vetting:       Claude Opus 4.6 (final quality gate for all local output)

The llm_selector picks the best model for each task.
The ollama_fleet spawns/stops Docker containers dynamically.
Falls back to Claude if fleet is unavailable.
"""

import logging
import time
from crewai import LLM
from app.config import get_settings, get_anthropic_api_key

logger = logging.getLogger(__name__)

ROLES = ("coding", "architecture", "research", "writing", "default")

# Track the last model used and its URL for is_using_local()
_last_local_url: str | None = None


def create_commander_llm() -> LLM:
    """Commander always uses Claude for maximum routing intelligence."""
    settings = get_settings()
    return LLM(
        model=f"anthropic/{settings.commander_model}",
        api_key=get_anthropic_api_key(),
        max_tokens=512,
    )


def create_specialist_llm(
    max_tokens: int = 4096,
    role: str = "default",
    task_hint: str = "",
) -> LLM:
    """
    Create an LLM for a specialist role.

    Uses llm_selector to pick the best model, then ollama_fleet to
    spawn a container for it. Falls back to Claude if fleet is unavailable.
    """
    global _last_local_url
    settings = get_settings()

    if not settings.local_llm_enabled:
        return _claude_fallback(role, max_tokens)

    try:
        from app.llm_selector import select_model
        from app.ollama_fleet import spawn_model
        from app.llm_benchmarks import record

        # Select the best model for this task
        model = select_model(role, task_hint)

        # Spawn a container (or reuse existing) — returns API URL
        start = time.monotonic()
        url = spawn_model(model)
        spawn_ms = int((time.monotonic() - start) * 1000)

        if url:
            _last_local_url = url
            model_str = f"ollama_chat/{model}"
            logger.info(
                f"llm_factory: role={role} → {model} at {url} "
                f"(spawn: {spawn_ms}ms)"
            )
            return LLM(
                model=model_str,
                base_url=url,
                max_tokens=max_tokens,
            )

    except Exception as exc:
        logger.warning(f"llm_factory: fleet failed for role={role}: {exc}")

    return _claude_fallback(role, max_tokens)


def _claude_fallback(role: str, max_tokens: int) -> LLM:
    """Fallback to Claude when fleet is unavailable."""
    global _last_local_url
    _last_local_url = None
    settings = get_settings()
    logger.info(f"llm_factory: role={role} → Claude (fleet unavailable)")
    return LLM(
        model=f"anthropic/{settings.specialist_model}",
        api_key=get_anthropic_api_key(),
        max_tokens=max_tokens,
    )


def create_vetting_llm() -> LLM:
    """Vetting uses Claude Opus 4.6 — the final quality gate."""
    settings = get_settings()
    return LLM(
        model=f"anthropic/{settings.vetting_model}",
        api_key=get_anthropic_api_key(),
        max_tokens=4096,
    )


def is_using_local() -> bool:
    """Check if the last specialist LLM call used a local model."""
    return _last_local_url is not None
