"""Phase 12 hot-path hooks.

Two narrow entry points the CIL loop calls. Together they add ~100
tokens average to the hot path (Value Resonance optional Tier-1
deepening only). Boundary classification is deterministic, zero LLM.

Loop integration:
  - Step 1 (Perceive): call `tag_scene_processing_modes(kernel)`
  - Step 3 (Attend):   call `apply_value_resonance_and_lenses(kernel)`

Both functions are safe to call before any subpackage is fully wired —
they no-op gracefully if the kernel has nothing to process.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.subia.kernel import SubjectivityKernel

logger = logging.getLogger(__name__)


def tag_scene_processing_modes(kernel: SubjectivityKernel) -> int:
    """Step 1 hook (Boundary Sense). Stamp processing_mode on every
    unclassified scene item. Returns count newly classified."""
    try:
        from app.subia.boundary.classifier import classify_scene
        return classify_scene(kernel.scene)
    except Exception as exc:
        logger.debug("phase12: boundary tagging failed: %s", exc)
        return 0


def apply_value_resonance_and_lenses(kernel: SubjectivityKernel) -> dict:
    """Step 3 hook (Value Resonance + Phronesis lenses)."""
    out = {"items_modulated": 0, "lens_aggregate": {}}
    try:
        from app.subia.values.resonance import apply_resonance_to_scene
        out["items_modulated"] = apply_resonance_to_scene(kernel)
    except Exception as exc:
        logger.debug("phase12: value resonance failed: %s", exc)
    try:
        from app.subia.values.perceptual_lens import apply_lenses_to_homeostasis
        out["lens_aggregate"] = apply_lenses_to_homeostasis(kernel)
    except Exception as exc:
        logger.debug("phase12: lenses failed: %s", exc)
    return out


def freeze_decay_predicate(item) -> bool:
    """Used by scene salience-decay logic (Wonder Register)."""
    try:
        from app.subia.wonder.register import freeze_decay_for
        return freeze_decay_for(item)
    except Exception:
        return False


def should_inhibit_completion(kernel: SubjectivityKernel) -> bool:
    """Used by Step 7 (Act) gate (Wonder Register)."""
    try:
        from app.subia.wonder.register import should_inhibit_completion as _f
        return _f(kernel)
    except Exception:
        return False
