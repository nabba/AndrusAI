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
from dataclasses import dataclass

from app.config import get_settings

logger = logging.getLogger(__name__)

# ── Input-aware effective budget (2026-06-19) ────────────────────────────────
# `creative_run_budget_usd` is a *generation* allowance calibrated for short
# brainstorm prompts. But reading the input is unavoidable cost that scales
# with input size: a large document (e.g. a 110k-char attachment ≈ 28k tokens)
# costs more than the $0.10 default just to read once, so the creative run
# aborted in phase 1 before producing any output (and still spent the money).
# A fixed USD cap is structurally input-size-blind. We scale the effective
# budget with input size, capped at a hard ceiling so a runaway input can't
# burn unbounded $, and never below the operator's explicitly-configured value.
_BASELINE_INPUT_TOKENS = 2000        # input size the default budget assumes
_EFFECTIVE_BUDGET_CEILING_USD = 5.0  # hard cap on automatic scaling
_CHARS_PER_TOKEN = 4                 # standard rough chars→tokens heuristic


def estimate_tokens(text: str) -> int:
    """Rough token estimate from character count (4 chars/token heuristic)."""
    return max(0, len(text or "")) // _CHARS_PER_TOKEN

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


@dataclass(frozen=True)
class EffectiveBudget:
    """The budget a creative run should actually enforce for a given input.

    `usd` is what the run caps against; `base_usd` is the operator-configured
    value; `scaled`/`ceiling_hit` let the caller produce an honest message
    when a run still can't fit.
    """
    usd: float
    base_usd: float
    input_tokens: int
    scaled: bool
    ceiling_hit: bool
    ceiling_usd: float = _EFFECTIVE_BUDGET_CEILING_USD


def effective_budget_usd(task_description: str) -> EffectiveBudget:
    """Scale the configured budget by input size.

    Small inputs use the configured value unchanged. Larger inputs scale the
    budget proportionally (so reading the input doesn't consume the entire
    generation allowance), capped at `_EFFECTIVE_BUDGET_CEILING_USD` and never
    reduced below the operator's explicit `creative_run_budget_usd`.
    """
    base = get_budget_usd()
    toks = estimate_tokens(task_description)
    if toks <= _BASELINE_INPUT_TOKENS:
        return EffectiveBudget(
            usd=base, base_usd=base, input_tokens=toks,
            scaled=False, ceiling_hit=False,
        )
    scaled_usd = base * (toks / _BASELINE_INPUT_TOKENS)
    capped = min(scaled_usd, _EFFECTIVE_BUDGET_CEILING_USD)
    usd = max(base, capped)  # never below the operator's explicit choice
    return EffectiveBudget(
        usd=usd, base_usd=base, input_tokens=toks,
        scaled=usd > base,
        ceiling_hit=scaled_usd > _EFFECTIVE_BUDGET_CEILING_USD,
    )


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
