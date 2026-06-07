"""Single source of truth for "which provider owns this model id".

Previously this rule was duplicated across three modules
(:mod:`app.llm_anthropic_budget`, :mod:`app.llm_openrouter_budget`,
``app.llm_cost_advisor.analyzer``).  Adding a new vendor required
updating all three in lockstep, with inevitable drift.

Kept tight: only the patterns we actually observe in production
audit-log rows are matched.  New providers go through ONE edit here
and become visible to every consumer.
"""
from __future__ import annotations

from typing import Optional

# ── Anthropic markers ────────────────────────────────────────────
#
# Model strings that classify as Anthropic.  Substring matches on
# the lowercased model id; the LiteLLM-canonical prefix
# ``anthropic/`` is the canonical form but the patterns also catch
# raw bare ids (``claude-sonnet-4-6``) and OR-routed Claudes
# (``openrouter/anthropic/claude-sonnet-4.6``).
_ANTHROPIC_MARKERS: tuple[str, ...] = (
    "claude-opus",
    "claude-sonnet",
    "claude-haiku",
    "anthropic/",
    "/claude-",
)


# ── OpenRouter vendor prefixes ──────────────────────────────────
#
# Model strings that classify as OpenRouter.  Either the
# ``openrouter/`` LiteLLM prefix OR a known vendor prefix that
# OR exposes.  Kept conservative: a model id we don't recognise
# returns ``None`` rather than guessing.
_OPENROUTER_PREFIXES: tuple[str, ...] = (
    "openrouter/",
    "deepseek/",
    "mistralai/",
    "qwen/",
    "meta-llama/",
    "google/",
    "x-ai/",
    "z-ai/",
)


# ── Ollama markers ──────────────────────────────────────────────


_OLLAMA_PREFIXES: tuple[str, ...] = (
    "ollama_chat/",
    "ollama/",
)


def classify_provider(model_id: str) -> Optional[str]:
    """Return the provider name (``"anthropic"`` / ``"openrouter"`` /
    ``"ollama"``) for a model id, or ``None`` when unrecognised.

    The order of checks matters because an OR-routed Anthropic model
    (``openrouter/anthropic/claude-sonnet-4.6``) matches both
    ``openrouter/`` and ``/claude-``.  We classify by ROUTE (which
    endpoint the call actually hits), so OR wins for that case —
    matches the cost-attribution semantics every reader expects.

    Returns ``None`` rather than a default so callers can choose
    whether to ignore unrecognised rows or aggregate them into a
    ``__unknown__`` bucket.
    """
    if not isinstance(model_id, str) or not model_id:
        return None
    lower = model_id.lower()

    # OR wins over Anthropic substring match — see docstring.
    if any(lower.startswith(p) for p in _OPENROUTER_PREFIXES):
        return "openrouter"

    # Ollama before Anthropic — locally-hosted Claude variants would
    # otherwise be misclassified.  In practice Ollama doesn't host
    # Claude, but the precedence is principled.
    if any(lower.startswith(p) for p in _OLLAMA_PREFIXES):
        return "ollama"

    if any(marker in lower for marker in _ANTHROPIC_MARKERS):
        return "anthropic"

    # OpenRouter+Ollama-only stack (CLAUDE.md): the entire PAID surface is
    # OpenRouter, and local Ollama models surface as bare ``name:tag`` ids
    # (no slash). So any remaining vendor-prefixed id (``vendor/model``) that
    # isn't ollama- or anthropic-native is, by construction, OpenRouter-routed.
    # This catch-all closes the per-provider attribution gap where unlisted
    # vendor prefixes (openai/, stepfun/, minimax/, moonshotai/, xiaomi/,
    # baidu/, nvidia/, inclusionai/, …) returned None and were silently
    # excluded from the OpenRouter daily-cap spend sum (~$61 / 16.5% of paid
    # spend invisible, 2026-06-07 audit). Bare local ids (no slash) still
    # return None — they're free, so the cap doesn't care.
    if "/" in lower:
        return "openrouter"

    return None


__all__ = ["classify_provider"]
