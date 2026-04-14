"""
creative_mode.py — Runtime-adjustable settings for the creative MAS pipeline.

Follows the same pattern as `app.llm_mode`: a small mutable module that holds
per-process state initialized from Settings defaults, so the dashboard can
adjust values without restarting the app or mutating the pydantic singleton.

Exposed values:
    creative_run_budget_usd — hard cap per creative run (default 0.10)
    originality_wiki_weight — wiki vs Mem0 blend for originality scoring

Thread-safety: values are simple floats; assignments are atomic in CPython.
Callers should read once per run to avoid mid-run drift if the dashboard
updates during execution.
"""
from __future__ import annotations

import logging
import threading

from app.config import get_settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_budget_usd: float | None = None
_originality_wiki_weight: float | None = None


def _ensure_initialized() -> None:
    global _budget_usd, _originality_wiki_weight
    if _budget_usd is not None:
        return
    with _lock:
        if _budget_usd is not None:
            return
        s = get_settings()
        _budget_usd = float(s.creative_run_budget_usd)
        _originality_wiki_weight = float(s.creative_originality_wiki_weight)


def get_budget_usd() -> float:
    _ensure_initialized()
    return _budget_usd  # type: ignore[return-value]


def set_budget_usd(value: float) -> None:
    global _budget_usd
    _ensure_initialized()
    if value < 0.0:
        raise ValueError("creative_run_budget_usd must be non-negative")
    if value > 100.0:
        raise ValueError("creative_run_budget_usd exceeds sanity cap of $100/run")
    _budget_usd = float(value)
    logger.info(f"creative_mode: budget_usd set to ${value:.2f}")


def get_originality_wiki_weight() -> float:
    _ensure_initialized()
    return _originality_wiki_weight  # type: ignore[return-value]


def set_originality_wiki_weight(value: float) -> None:
    global _originality_wiki_weight
    _ensure_initialized()
    if not (0.0 <= value <= 1.0):
        raise ValueError("originality_wiki_weight must be in [0, 1]")
    _originality_wiki_weight = float(value)
    logger.info(f"creative_mode: originality_wiki_weight set to {value:.2f}")


def snapshot() -> dict:
    """Return a plain-dict view for dashboard GET."""
    _ensure_initialized()
    return {
        "creative_run_budget_usd": _budget_usd,
        "originality_wiki_weight": _originality_wiki_weight,
        "mem0_weight": round(1.0 - (_originality_wiki_weight or 0.0), 3),
    }
