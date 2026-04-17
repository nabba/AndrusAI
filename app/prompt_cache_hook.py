"""
prompt_cache_hook.py — Inject Anthropic cache_control markers on long system prompts.

Activated once at startup via install_cache_hook(). Monkey-patches
litellm.completion() so that, for Anthropic models, the system prompt is
converted from a plain string to the content-blocks form with a
cache_control={"type": "ephemeral"} marker on the trailing block.

Anthropic's prompt caching (2024-07-31 beta) only activates on tokens
explicitly marked with cache_control — without this hook the beta header set
in llm_factory is a no-op. Anthropic's minimum cacheable size is 1024 tokens
for claude-sonnet / 2048 for claude-haiku. We gate on ~1024 estimated tokens
(4 chars/token heuristic, same as history_compression).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_MIN_CACHE_CHARS = 4096  # ~1024 tokens — skip caching for short prompts
_installed = False


def _is_anthropic(model: str) -> bool:
    m = (model or "").lower()
    return any(k in m for k in ("claude-opus", "claude-sonnet", "claude-haiku", "anthropic/claude"))


def _inject_cache_control(messages: list) -> list:
    """Rewrite the system message to use block form with cache_control.

    Leaves messages untouched when:
      - there's no system message, or
      - the system prompt is too short to benefit from caching, or
      - it's already in block form (someone already marked it).
    """
    if not messages:
        return messages
    out = []
    injected = False
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "system" and isinstance(content, str) and len(content) >= _MIN_CACHE_CHARS and not injected:
            out.append({
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": content,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            })
            injected = True
        else:
            out.append(msg)
    return out


def install_cache_hook() -> None:
    """Idempotent: patch litellm.completion once."""
    global _installed
    if _installed:
        return
    try:
        import litellm
    except ImportError:
        return

    original = litellm.completion

    def patched(*args, **kwargs):
        try:
            model = kwargs.get("model") or (args[0] if args else "")
            if _is_anthropic(model) and "messages" in kwargs:
                kwargs["messages"] = _inject_cache_control(kwargs["messages"])
        except Exception:
            logger.debug("prompt_cache_hook: injection failed (non-fatal)", exc_info=True)
        return original(*args, **kwargs)

    litellm.completion = patched

    # Also patch acompletion if present
    if hasattr(litellm, "acompletion"):
        original_async = litellm.acompletion

        async def patched_async(*args, **kwargs):
            try:
                model = kwargs.get("model") or (args[0] if args else "")
                if _is_anthropic(model) and "messages" in kwargs:
                    kwargs["messages"] = _inject_cache_control(kwargs["messages"])
            except Exception:
                logger.debug("prompt_cache_hook: async injection failed (non-fatal)", exc_info=True)
            return await original_async(*args, **kwargs)

        litellm.acompletion = patched_async

    _installed = True
    logger.info("prompt_cache_hook: Anthropic cache_control injection enabled")
