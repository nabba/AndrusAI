"""
subia.social.salience_boost — apply social-model-driven salience
boost to workspace candidates.

Per SubIA Part I §11: items that match an entity's inferred_focus
get a salience boost proportional to trust_level. This is how
"Andrus cares about X" feeds the scene bottleneck: items matching
Andrus's inferred focus win competitions they would otherwise lose.

Boost policy:
  - Only positive boosts; nothing is pushed down.
  - Per-entity weight: 1.0 for humans of interest (andrus default),
    0.5 for agents — so human focus dominates agent focus.
  - Damped by trust_level: low-trust models have weaker influence.
  - Capped so no item's salience exceeds 1.0.

Function is pure: mutates items in place but returns a structured
boost report for logging/audit.

Infrastructure-level. Not agent-modifiable. See PROGRAM.md Phase 8.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

from app.subia.config import SUBIA_CONFIG

logger = logging.getLogger(__name__)


# Weight applied to matches by entity_type.
_WEIGHT_HUMAN = 0.12
_WEIGHT_AGENT = 0.05

# Cap: a single item can gain at most this much salience from social
# boosts in one pass — prevents social focus from nuking the
# bottleneck's other signals.
_MAX_BOOST_PER_ITEM = 0.25


@dataclass
class BoostReport:
    """Structured record of a salience-boost pass."""
    items_boosted: int = 0
    total_boost: float = 0.0
    per_entity: dict = field(default_factory=dict)   # entity_id -> total

    def to_dict(self) -> dict:
        return {
            "items_boosted": self.items_boosted,
            "total_boost": round(self.total_boost, 3),
            "per_entity": {
                k: round(v, 3) for k, v in self.per_entity.items()
            },
        }


def apply_salience_boost(
    candidates: Iterable,
    social_models: dict,
) -> BoostReport:
    """Mutate candidates' .salience_score based on social-model matches.

    Args:
        candidates:     iterable of WorkspaceItem-like objects with
                        .content / .summary / .metadata attributes and
                        a mutable .salience_score float.
        social_models:  dict of entity_id -> SocialModelEntry. Usually
                        kernel.social_models.

    Returns a BoostReport. Never raises.
    """
    report = BoostReport()
    if not social_models:
        return report

    humans = set(
        str(h).lower()
        for h in (SUBIA_CONFIG.get("SOCIAL_MODEL_HUMANS", []) or [])
    )

    for item in list(candidates or ()):
        try:
            haystack = _item_haystack(item).lower()
        except Exception:
            continue

        total_item_boost = 0.0
        for entity_id, model in social_models.items():
            focus = getattr(model, "inferred_focus", None) or []
            if not focus:
                continue
            trust = _clamp(getattr(model, "trust_level", 0.7), 0.0, 1.0)
            # Count focus hits against this item.
            hits = sum(
                1 for t in focus
                if t and str(t).strip().lower() in haystack
            )
            if not hits:
                continue
            weight = (
                _WEIGHT_HUMAN
                if str(entity_id).lower() in humans
                else _WEIGHT_AGENT
            )
            # Boost is weight * trust * min(hits, 3)/3 — diminishing
            # returns after 3 matches on the same item.
            boost = weight * trust * min(hits, 3) / 3.0
            total_item_boost += boost
            report.per_entity[entity_id] = (
                report.per_entity.get(entity_id, 0.0) + boost
            )

        if total_item_boost <= 0.0:
            continue
        capped = min(total_item_boost, _MAX_BOOST_PER_ITEM)
        try:
            current = float(getattr(item, "salience_score", 0.0))
        except (TypeError, ValueError):
            current = 0.0
        new = min(1.0, current + capped)
        # Only boosts; never decrease.
        if new > current:
            try:
                item.salience_score = new
            except Exception:
                continue
            report.items_boosted += 1
            report.total_boost += (new - current)

    return report


# ── Helpers ────────────────────────────────────────────────────

def _item_haystack(item) -> str:
    """Build the string we match focus topics against."""
    parts = [
        str(getattr(item, "content", "") or ""),
        str(getattr(item, "summary", "") or ""),
    ]
    metadata = getattr(item, "metadata", {}) or {}
    if isinstance(metadata, dict):
        for k in ("section", "venture", "domain", "project", "topic"):
            v = metadata.get(k)
            if v:
                parts.append(str(v))
    ref = getattr(item, "content_ref", None)
    if ref:
        parts.append(str(ref))
    return " ".join(parts)


def _clamp(v, lo, hi):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return lo
