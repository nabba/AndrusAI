"""
llm_mode.py — Runtime-mutable unified LLM mode state.

The mode is the single axis that controls **both** the candidate pool
(which tiers / providers are reachable) **and** the cost stance (how
aggressively to prefer cheaper models within the allowed set). Prior
to the unification there were two concepts — ``llm_mode`` (runtime
mode: free/hybrid/insane/anthropic) and ``cost_mode`` (budget/balanced/
quality) — but at the extremes they collapsed (``free`` implied
``budget``; ``insane`` implied ``quality``).

The 6-value vocabulary (monotonic cost gradient + one vendor lock):

  free      — Zero cost: local Ollama + OpenRouter free tier only.
  budget    — Cascade local → cheap cloud APIs (~$1.5/M out).
  balanced  — Default. Cascade through every tier, balanced ceiling
              (~$6/M out).
  quality   — Cascade, premium preferred, ceiling ~$30/M.
  insane    — Premium-only, no cost ceiling.
  anthropic — Anthropic-only across its Haiku/Sonnet/Opus line-up.

Legacy input (``hybrid``/``local``/``cloud``) is accepted and normalised
onto the unified set — see :func:`set_mode`.

Separate from ``config.py`` because ``Settings`` is frozen by
``lru_cache``. The mode changes at runtime from Signal commands,
dashboard writes, and API calls without restarting the process.
"""

import logging
import threading
from typing import Literal

from app.llm_catalog import RUNTIME_MODES, _normalize_mode

# ``LLMMode`` is a Literal over the 6 canonical mode names. Legacy
# aliases are accepted at set_mode but stored canonically.
LLMMode = Literal["free", "budget", "balanced", "quality", "insane", "anthropic"]

DEFAULT_MODE: LLMMode = "balanced"

_lock = threading.Lock()
_mode: str = DEFAULT_MODE

logger = logging.getLogger(__name__)

# Exposed tuple for callers that want to enumerate. Mirrors
# ``llm_catalog.RUNTIME_MODES`` — single source of truth.
VALID_MODES: tuple[str, ...] = RUNTIME_MODES


def get_mode() -> str:
    """Return the current runtime LLM mode (canonical name).

    Always returns one of :data:`VALID_MODES`.
    """
    with _lock:
        return _mode


def set_mode(mode: str) -> None:
    """Set the runtime LLM mode.

    Accepts the 6 canonical names plus the legacy ``hybrid``/``local``/
    ``cloud`` aliases (normalised via ``_normalize_mode``). Raises
    ``ValueError`` only for inputs that can't be normalised onto the
    vocabulary (e.g. ``"bogus"``).
    """
    global _mode
    canonical = _normalize_mode(mode)
    raw = (mode or "").strip().lower()
    # _normalize_mode falls through to "balanced" for bad inputs; reject
    # those explicitly so callers don't silently get the default when
    # they mistype.
    if canonical not in RUNTIME_MODES or (
        raw != canonical and raw not in {"hybrid", "local", "cloud", canonical}
    ):
        raise ValueError(
            f"Invalid LLM mode: {mode!r}. Must be one of {VALID_MODES} "
            f"(or legacy aliases hybrid/local/cloud)."
        )
    with _lock:
        old = _mode
        _mode = canonical
    if old != canonical:
        logger.info("llm_mode: switched %s → %s", old, canonical)
