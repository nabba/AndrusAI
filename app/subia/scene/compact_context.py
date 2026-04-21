"""
subia.scene.compact_context — Amendment B.5 compact context block.

The original CIL pre_task context (SubIA Part I §4.2) could approach
~250-300 tokens with scene, homeostasis, prediction, meta, safety,
cascade fields all expanded. Amendment B.5 targets ~120 tokens by:

  - Omitting fields at baseline (never say "safety: all clear")
  - Ultra-compact focal format: F1/F2/... prefix, 1 line each
  - Ultra-compact peripheral: title(section); title(section); ...
  - Only deviations above threshold in H:
  - Prediction only when non-cached
  - Known-unknowns only when > 0

Example output (~120 tokens):

    [SubIA]
    F1: Truepic Series C analysis (s:0.82 [urgency])
    F2: KaiCart TikTok API constraints (s:0.71 [concern])
    F3: Cross-venture API unreliability (s:0.65 [curiosity])
    Periph: PLG Q2 plan(plg); Protect Group(plg); TikTok seller(kaicart)
    ⚠ Peripheral item has deadline: PLG regulatory filing — 2026-05-15
    H: contr:+0.27 prog:-0.18
    Pred: conf=0.70
    Unknown: 3
    [/SubIA]

Infrastructure-level. See PROGRAM.md Phase 5.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from app.subia.config import SUBIA_CONFIG

logger = logging.getLogger(__name__)


# Thresholds
_HOMEOSTATIC_SHOW_ABS = 0.2      # deviations shown inline
_PERIPHERAL_INLINE_MAX = 8       # periph entries to inline
_PERIPHERAL_SUMMARY_CHARS = 30


def build_compact_context(
    *,
    tiers: Any = None,
    homeostasis: Any = None,
    prediction: Any = None,
    meta_state: Any = None,
    safety_signals: Iterable[str] = (),
    cascade_recommendation: str = "maintain",
    dispatch: Any = None,
    kernel: Any = None,
) -> str:
    """Build a token-efficient context injection block.

    Duck-typed on every input so tests can pass dataclasses, dicts,
    or None. None inputs are silently omitted from the block.
    Returns empty string if no content to render.

    When `kernel` is supplied and the kernel carries a SpeciousPresent
    / TemporalContext, the Phase 14 felt-now paragraph is appended via
    render_specious_present_block. This closes the fifth Phase 14
    bridge at the context-injection edge.
    """
    lines: list[str] = ["[SubIA]"]

    # Focal
    focal_list = _list_of(tiers, "focal") if tiers is not None else []
    for i, item in enumerate(focal_list, 1):
        summary = (
            str(getattr(item, "content", "") or getattr(item, "summary", ""))
            [:50]
        )
        salience = float(getattr(item, "salience_score", 0.0)
                         or getattr(item, "salience", 0.0))
        affect = (
            getattr(item, "dominant_affect", "")
            or (_dict_val(getattr(item, "metadata", None), "affect", "") or "")
        )
        affect_tag = f" [{affect}]" if affect and affect != "neutral" else ""
        lines.append(f"F{i}: {summary} (s:{salience:.2f}{affect_tag})")

    # Peripheral (ultra-compact inline)
    periph_list = _list_of(tiers, "peripheral") if tiers is not None else []
    if periph_list:
        summaries = []
        for p in periph_list[:_PERIPHERAL_INLINE_MAX]:
            summary = str(getattr(p, "summary", ""))[:_PERIPHERAL_SUMMARY_CHARS]
            section = str(getattr(p, "section", "") or "unknown")[:12]
            summaries.append(f"{summary}({section})")
        lines.append(f"Periph: {'; '.join(summaries)}")

    # Peripheral alerts (always shown if present)
    alerts = _list_of(tiers, "peripheral_alerts") if tiers is not None else []
    for alert in alerts:
        lines.append(f"⚠ {str(alert)[:100]}")

    # Homeostasis (only deviations above threshold)
    if homeostasis is not None:
        dev = _homeostasis_deviations(homeostasis)
        shown = [(v, d) for v, d in dev.items() if abs(d) > _HOMEOSTATIC_SHOW_ABS]
        shown.sort(key=lambda p: abs(p[1]), reverse=True)
        if shown:
            parts = " ".join(f"{v[:4]}:{d:+.2f}" for v, d in shown[:4])
            lines.append(f"H: {parts}")

    # Prediction (only for non-cached, show confidence)
    if prediction is not None and not _is_cached_prediction(prediction):
        conf = float(getattr(prediction, "confidence", 0.5))
        summary = _dict_val(
            getattr(prediction, "predicted_outcome", None), "summary", "",
        )
        line = f"Pred: conf={conf:.2f}"
        if summary:
            line += f" | {str(summary)[:60]}"
        lines.append(line)

    # Cascade recommendation (silent when maintaining)
    if cascade_recommendation and cascade_recommendation != "maintain":
        lines.append(f"Cascade: {cascade_recommendation}")

    # Meta / known unknowns
    if meta_state is not None:
        unknowns = getattr(meta_state, "known_unknowns", []) or []
        n = len(list(unknowns))
        if n:
            lines.append(f"Unknown: {n}")

    # Dispatch verdict (only when not ALLOW)
    if dispatch is not None:
        verdict = str(getattr(dispatch, "verdict", "") or "")
        if verdict and verdict != "ALLOW":
            reason = str(getattr(dispatch, "reason", ""))[:80]
            line = f"Dispatch: {verdict}"
            if reason:
                line += f" — {reason}"
            lines.append(line)

    # Safety signals
    for signal in (safety_signals or ()):
        lines.append(f"⚠ {str(signal)[:100]}")

    # Phase 14 felt-now paragraph (only when kernel carries populated
    # SpeciousPresent). Silently omitted when temporal state isn't set
    # up yet or the specious present is empty.
    if kernel is not None:
        try:
            from app.subia.connections.temporal_subia_bridge import (
                render_specious_present_block,
            )
            block = render_specious_present_block(kernel)
            if block:
                lines.extend(block.splitlines())
        except Exception:
            logger.debug("compact_context: specious present render failed",
                         exc_info=True)

    lines.append("[/SubIA]")
    return "\n".join(lines)


def estimate_tokens(text: str) -> int:
    """Rough token count (4 chars/token)."""
    return len(text) // 4


# ── Duck-typed helpers ───────────────────────────────────────────

def _list_of(obj, attr: str) -> list:
    """Extract an attribute as a list, tolerant of dict shape."""
    if obj is None:
        return []
    if isinstance(obj, dict):
        value = obj.get(attr, [])
    else:
        value = getattr(obj, attr, [])
    if value is None:
        return []
    try:
        return list(value)
    except Exception:
        return []


def _dict_val(obj, key: str, default):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _homeostasis_deviations(homeostasis) -> dict:
    """Duck-type: either HomeostaticState with .deviations, or a dict."""
    if isinstance(homeostasis, dict):
        dev = homeostasis.get("deviations", {}) or {}
    else:
        dev = getattr(homeostasis, "deviations", {}) or {}
    if not isinstance(dev, dict):
        return {}
    out = {}
    for k, v in dev.items():
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def _is_cached_prediction(prediction) -> bool:
    if isinstance(prediction, dict):
        return bool(prediction.get("cached", False))
    return bool(getattr(prediction, "cached", False))
