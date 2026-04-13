"""
subia.social.model — SubIA Theory-of-Mind manager.

Maintains per-entity models of what other minds (humans + agents)
are currently attending to, expecting, prioritizing, and whether
their actual observed behaviour matches our model (divergence).

Design constraints from SubIA Part I:
  - Models are built from BEHAVIORAL EVIDENCE, not claimed intention.
    An entity saying "I care about X" does not update the model;
    an entity repeatedly opening, editing, or asking about X does.
  - Social model influences salience scoring — items Andrus is
    inferred to care about get a boost (see salience_boost.py).
  - Divergence detection: when our model predicts a focus that the
    entity's actual actions contradict, record the divergence so
    retrospection can correct the model.

Storage: the SubIA kernel already carries `social_models: dict` of
entity_id -> SocialModelEntry. This manager mutates that dict
in-place. No DB dependencies.

Infrastructure-level. Not agent-modifiable. See PROGRAM.md Phase 8.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from app.subia.config import SUBIA_CONFIG
from app.subia.kernel import SocialModelEntry, SubjectivityKernel

logger = logging.getLogger(__name__)


# Decay factor applied to inferred_focus when an interaction is
# recorded but the existing focus topic wasn't mentioned — slowly
# drifts away rather than forgetting abruptly.
_FOCUS_DECAY_CAP = 6

# When trust_level climbs past this we don't boost further (avoids
# runaway trust inflation).
_TRUST_MAX = 0.98
_TRUST_MIN = 0.10
_TRUST_DELTA_OK = 0.02
_TRUST_DELTA_BAD = -0.05


class SocialModel:
    """Manager over a kernel's social_models dict.

    Thin wrapper — all state lives in the kernel so persistence
    follows the kernel.persistence round-trip.
    """

    def __init__(self, kernel: SubjectivityKernel) -> None:
        self.kernel = kernel

    # ── CRUD on entries ──────────────────────────────────────────

    def ensure_entry(
        self,
        entity_id: str,
        entity_type: str = "agent",
    ) -> SocialModelEntry:
        """Get-or-create a SocialModelEntry for the given entity."""
        models = self.kernel.social_models
        if entity_id not in models:
            models[entity_id] = SocialModelEntry(
                entity_id=str(entity_id),
                entity_type=str(entity_type),
                trust_level=0.7,
            )
        return models[entity_id]

    def get(self, entity_id: str) -> SocialModelEntry | None:
        return self.kernel.social_models.get(entity_id)

    def all_models(self) -> dict:
        return dict(self.kernel.social_models)

    # ── Updates from behavioral evidence ─────────────────────────

    def update_from_interaction(
        self,
        entity_id: str,
        *,
        topics_touched: Iterable[str] = (),
        expectation: str | None = None,
        priority_signal: str | None = None,
        outcome_ok: bool | None = None,
        entity_type: str = "agent",
    ) -> SocialModelEntry:
        """Update an entity's model from one observed interaction.

        Args:
            entity_id:        'andrus', 'commander', 'researcher', etc.
            topics_touched:   items the entity engaged with during
                              this interaction (strings, anything
                              referenceable as a focus target).
            expectation:      if inferred, an expectation the entity
                              appears to have of us.
            priority_signal:  if inferred, a priority ordering hint.
            outcome_ok:       whether the interaction ended well. True
                              nudges trust up, False nudges down.
            entity_type:      'human'|'agent'. Used only on initial
                              entry creation.
        """
        entry = self.ensure_entry(entity_id, entity_type=entity_type)

        # 1) Inferred focus: MRU-style with recency cap.
        touched = [
            str(t)[:80] for t in (topics_touched or []) if t
        ]
        if touched:
            # Prepend fresh topics, dedupe, cap length.
            seen: set[str] = set()
            new_focus: list[str] = []
            for t in touched:
                if t not in seen:
                    new_focus.append(t)
                    seen.add(t)
            # Followed by old topics not already seen, up to cap.
            for t in entry.inferred_focus:
                if t not in seen:
                    new_focus.append(t)
                    seen.add(t)
                if len(new_focus) >= _FOCUS_DECAY_CAP:
                    break
            entry.inferred_focus = new_focus[:_FOCUS_DECAY_CAP]
        elif entry.inferred_focus:
            # No topics touched this interaction — decay oldest.
            entry.inferred_focus = list(entry.inferred_focus)[:_FOCUS_DECAY_CAP]

        # 2) Inferred expectations + priorities: append if new.
        if expectation:
            exp = str(expectation)[:120]
            if exp not in entry.inferred_expectations:
                entry.inferred_expectations.append(exp)
                if len(entry.inferred_expectations) > 10:
                    del entry.inferred_expectations[:-10]

        if priority_signal:
            pri = str(priority_signal)[:120]
            if pri not in entry.inferred_priorities:
                entry.inferred_priorities.append(pri)
                if len(entry.inferred_priorities) > 10:
                    del entry.inferred_priorities[:-10]

        # 3) Trust update (behavioral only — success/failure signal).
        if outcome_ok is True:
            entry.trust_level = min(
                _TRUST_MAX, entry.trust_level + _TRUST_DELTA_OK,
            )
        elif outcome_ok is False:
            entry.trust_level = max(
                _TRUST_MIN, entry.trust_level + _TRUST_DELTA_BAD,
            )

        # 4) Timestamp.
        entry.last_interaction = datetime.now(timezone.utc).isoformat()

        return entry

    # ── Divergence detection ─────────────────────────────────────

    def check_divergence(
        self,
        entity_id: str,
        *,
        actual_focus: Iterable[str] = (),
        severity_threshold: float = 0.5,
    ) -> dict | None:
        """Compare our inferred focus to observed actual focus.

        If the overlap is below threshold, record a divergence and
        return a structured description. Divergences feed into
        retrospective review — evidence that our mental model needs
        correction.

        Args:
            actual_focus:        observed current focus set for entity
            severity_threshold:  Jaccard similarity below this marks
                                 a divergence

        Returns the divergence dict (also appended to entry.divergences)
        or None if the model aligns well enough.
        """
        entry = self.get(entity_id)
        if entry is None:
            return None
        inferred = set(str(t) for t in entry.inferred_focus)
        actual = set(str(t)[:80] for t in (actual_focus or []))
        if not inferred and not actual:
            return None
        jacc = _jaccard(inferred, actual)
        if jacc >= severity_threshold:
            return None

        divergence = {
            "at": datetime.now(timezone.utc).isoformat(),
            "jaccard": round(jacc, 3),
            "inferred_focus": sorted(inferred),
            "actual_focus": sorted(actual),
            "missing_from_inference": sorted(actual - inferred),
            "inferred_but_absent": sorted(inferred - actual),
        }
        entry.divergences.append(divergence)
        if len(entry.divergences) > 20:
            del entry.divergences[:-20]
        logger.info(
            "social.model: divergence for %s (jaccard %.2f < %.2f)",
            entity_id, jacc, severity_threshold,
        )
        return divergence


# ── Helpers ────────────────────────────────────────────────────

def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def humans_of_interest() -> list[str]:
    """Humans the system models by default, per SUBIA_CONFIG."""
    return list(SUBIA_CONFIG.get("SOCIAL_MODEL_HUMANS", []) or [])


def should_update_this_cycle(loop_count: int) -> bool:
    """True on cycles that are a multiple of the configured frequency."""
    freq = int(SUBIA_CONFIG.get("SOCIAL_MODEL_UPDATE_FREQUENCY", 5) or 5)
    return loop_count > 0 and loop_count % freq == 0
