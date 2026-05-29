"""Prompt-cache ``cache_control`` injection for OpenRouter-routed calls.

Replaces the former ``prompt_cache_hook.py`` litellm monkeypatch. Both LLM
paths call :func:`inject_cache_control` on their messages just before
dispatch, so the injection lives inside our own factory-owned code rather
than in a global ``litellm.completion = patched`` reassignment:

  * agent path  — ``app.llms.budget_aware.BudgetAwareCompletion.call``
  * raw path    — ``app.llm_factory.ChatCompletionHandle.create``

OpenRouter forwards ``cache_control`` breakpoints to the upstream provider
for the Claude / Gemini / DeepSeek families (the ones that support prompt
caching). CrewAI's ``_format_messages_for_provider`` validates only the
``role``/``content`` keys and reorders for provider edge cases — it does
not stringify content blocks — so a system message rewritten into
content-block form survives through to litellm intact.

Caching is a cost optimisation: every function here is failure-soft and
returns the input unchanged on anything unexpected.
"""
from __future__ import annotations

# Anthropic's minimum cacheable prefix is ~1024 tokens (claude-sonnet) /
# ~2048 (claude-haiku). Gate on ~1024 tokens (4 chars/token heuristic,
# same threshold the retired prompt_cache_hook used) so we never mark a
# prefix too short to cache.
_MIN_CACHE_CHARS = 4096


def _supports_cache_control(model: str) -> bool:
    """True for model families whose OpenRouter route honours
    ``cache_control`` breakpoints."""
    m = (model or "").lower()
    return any(k in m for k in ("claude", "gemini", "deepseek"))


def inject_cache_control(messages, model):
    """Return *messages* with a ``cache_control: ephemeral`` marker on the
    (long) system prompt, or the input unchanged.

    No-ops when: messages isn't a list, the model family doesn't support
    caching, there's no system message, the system prompt is too short to
    benefit, or it's already in block form (someone already marked it).
    Non-mutating — builds a new list only when it injects.
    """
    if not isinstance(messages, list) or not _supports_cache_control(model):
        return messages
    out = []
    injected = False
    for msg in messages:
        if not isinstance(msg, dict):
            out.append(msg)
            continue
        role = msg.get("role", "")
        content = msg.get("content", "")
        if (
            role == "system"
            and isinstance(content, str)
            and len(content) >= _MIN_CACHE_CHARS
            and not injected
        ):
            out.append({
                "role": "system",
                "content": [{
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }],
            })
            injected = True
        else:
            out.append(msg)
    return out
