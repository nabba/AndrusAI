"""
llm_mode.py — Runtime-mutable LLM mode state.

The mode controls which model tiers are available for specialist agents:
  "local"  — Only Ollama models on Metal GPU, Claude fallback if Ollama fails
  "cloud"  — Only API models (OpenRouter + Anthropic), skip Ollama entirely
  "hybrid" — Try local first, cascade to API tier, then Claude fallback

Separate from config.py because Settings is frozen by lru_cache.
The mode needs to change at runtime from Signal commands, dashboard writes,
and API calls without restarting the process.
"""

import logging
import threading
from typing import Literal

LLMMode = Literal["local", "cloud", "hybrid"]

_lock = threading.Lock()
_mode: str = "hybrid"

logger = logging.getLogger(__name__)

VALID_MODES = ("local", "cloud", "hybrid")


def get_mode() -> str:
    """Return the current LLM mode."""
    with _lock:
        return _mode


def set_mode(mode: str) -> None:
    """Set the LLM mode. Validates input."""
    global _mode
    mode = mode.strip().lower()
    if mode not in VALID_MODES:
        raise ValueError(f"Invalid LLM mode: {mode!r}. Must be one of {VALID_MODES}")
    with _lock:
        old = _mode
        _mode = mode
    if old != mode:
        logger.info(f"llm_mode: switched {old} → {mode}")
