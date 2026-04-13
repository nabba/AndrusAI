"""Wonder → kernel application (Proposal 4 §4.2 closed-loop).

Three behavioural consequences:
  1. Stamp the triggering scene item with wonder_intensity (freezes
     decay when above WONDER_FREEZE_THRESHOLD).
  2. Bump the homeostasis variable `wonder` toward the signal intensity.
  3. Surface the triggering topic for the reverie scheduler when the
     signal is an "event" (intensity > WONDER_EVENT_THRESHOLD).
"""
from __future__ import annotations

import logging
from typing import Optional

from app.subia.config import SUBIA_CONFIG
from app.subia.kernel import SubjectivityKernel, SceneItem
from .detector import WonderSignal

logger = logging.getLogger(__name__)


def _ema(current: float, target: float, alpha: float = 0.3) -> float:
    return round(current + alpha * (target - current), 4)


def apply_wonder_to_kernel(
    kernel: SubjectivityKernel,
    signal: WonderSignal,
    *,
    item_id: Optional[str] = None,
) -> None:
    """Apply a WonderSignal to the kernel in place.

    This is the closed-loop bridge — without it, wonder would be a
    computed-but-unread value (forbidden by Phase 2 invariants).
    """
    # 1. Update homeostasis variable `wonder`
    h = kernel.homeostasis
    if "wonder" not in h.variables:
        h.variables["wonder"] = 0.5
    h.variables["wonder"] = _ema(h.variables["wonder"], signal.intensity)

    # 2. Stamp the triggering scene item (freezes decay downstream)
    if item_id:
        for item in kernel.scene or []:
            if getattr(item, "id", None) == item_id:
                item.wonder_intensity = max(
                    getattr(item, "wonder_intensity", 0.0),
                    signal.intensity,
                )
                break


def should_inhibit_completion(kernel: SubjectivityKernel) -> bool:
    """Closed-loop check used by the loop's Step 7 (Act) gate.

    Returns True iff the homeostatic wonder variable is above threshold
    OR any focal scene item has individual wonder above threshold.
    """
    threshold = float(SUBIA_CONFIG.get("WONDER_INHIBIT_THRESHOLD", 0.3))
    if kernel.homeostasis.variables.get("wonder", 0.0) > threshold:
        return True
    for item in kernel.focal_scene():
        if getattr(item, "wonder_intensity", 0.0) > threshold:
            return True
    return False


def is_wonder_event(signal: WonderSignal) -> bool:
    """Above WONDER_EVENT_THRESHOLD → curated Mem0 episode."""
    threshold = float(SUBIA_CONFIG.get("WONDER_EVENT_THRESHOLD", 0.7))
    return signal.intensity > threshold


def freeze_decay_for(item: SceneItem) -> bool:
    """Closed-loop check used by scene salience decay."""
    threshold = float(SUBIA_CONFIG.get("WONDER_FREEZE_THRESHOLD", 0.5))
    return getattr(item, "wonder_intensity", 0.0) > threshold
