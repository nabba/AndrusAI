"""
subia.memory.retrospective — Amendment C.6 significance discovery.

"This is how the system discovers that something it dismissed three
weeks ago was actually an early signal of something important."

The consolidator's Phase 7 policy writes every experience to the
full tier but only promotes above-threshold ones to curated. Some
experiences that looked insignificant at the time become significant
later — e.g. a routine API update record from 3 weeks ago is now
recognized as an early signal of this week's major outage.

This module scans the full tier for below-curated-threshold records
and asks: would this be significant NOW? Two signals drive the
re-evaluation:

  1. Wiki presence: if the current wiki contains content on the
     record's topic that didn't exist at consolidation time, the
     record just became relevant.

  2. Sustained prediction error: if the accuracy tracker reports
     sustained error in the record's domain, any experience in that
     domain deserves a second look.

Candidates that pass re-evaluation are promoted to curated via
DualTierMemoryAccess.promote_to_curated().

Scheduling: called periodically by the Self-Improver (weekly or
after every major prediction error). Not in the hot path.

Infrastructure-level. Not agent-modifiable. See PROGRAM.md Phase 7.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# Only promote at most this many candidates per scan, so a big
# re-evaluation burst doesn't blow the curated tier's size budget.
_MAX_PROMOTIONS_PER_SCAN = 10


@dataclass
class RetrospectiveReport:
    """What a single retrospective scan found and did."""
    candidates_reviewed: int = 0
    promoted: int = 0
    promoted_ids: list = field(default_factory=list)
    reasons: dict = field(default_factory=dict)   # record_id -> reason

    def to_dict(self) -> dict:
        return {
            "candidates_reviewed": self.candidates_reviewed,
            "promoted": self.promoted,
            "promoted_ids": list(self.promoted_ids),
            "reasons": dict(self.reasons),
        }


def retrospective_review(
    *,
    memory_access: Any,
    wiki_search: Any | None = None,
    accuracy_tracker: Any | None = None,
    recent_days: int = 21,
    significance_threshold: float = 0.3,
    max_promotions: int = _MAX_PROMOTIONS_PER_SCAN,
) -> RetrospectiveReport:
    """Scan full tier; promote records that have become significant.

    Args:
        memory_access:         DualTierMemoryAccess (or duck-typed).
        wiki_search:           optional callable(topic) -> bool. If the
                               current wiki contains related content,
                               that's a promotion signal.
        accuracy_tracker:      optional object exposing
                               has_sustained_error(domain). Domains
                               with sustained errors get their
                               experiences promoted.
        recent_days:           passed to memory_access.find_overlooked.
        significance_threshold: lower bound for candidates.
        max_promotions:        cap to avoid size blow-ups.

    Returns a RetrospectiveReport describing the scan.
    """
    report = RetrospectiveReport()

    find_fn = getattr(memory_access, "find_overlooked", None)
    if not callable(find_fn):
        return report

    try:
        candidates = list(find_fn(
            recent_days=recent_days,
            significance_threshold=significance_threshold,
        ))
    except Exception:
        logger.exception("retrospective: find_overlooked failed")
        return report

    report.candidates_reviewed = len(candidates)

    for candidate in candidates:
        if report.promoted >= max_promotions:
            break
        if not isinstance(candidate, dict):
            continue
        rec_id = str(
            candidate.get("id") or candidate.get("record_id") or ""
        )
        if not rec_id:
            continue

        reason = _re_evaluate(candidate, wiki_search, accuracy_tracker)
        if not reason:
            continue

        promoted = False
        promote_fn = getattr(memory_access, "promote_to_curated", None)
        if callable(promote_fn):
            try:
                promoted = bool(promote_fn(rec_id, reason))
            except Exception:
                logger.debug("retrospective: promote failed", exc_info=True)

        if promoted:
            report.promoted += 1
            report.promoted_ids.append(rec_id)
            report.reasons[rec_id] = reason

    return report


def _re_evaluate(
    candidate: dict,
    wiki_search: Any | None,
    accuracy_tracker: Any | None,
) -> str | None:
    """Return a promotion reason string if the candidate now qualifies,
    else None.

    Defensive and duck-typed — any signal source may be None.
    """
    topic = _topic_of(candidate)

    # Signal 1: wiki now contains related content.
    if wiki_search is not None and topic:
        try:
            hit = bool(wiki_search(topic))
        except Exception:
            hit = False
        if hit:
            return (
                f"Retrospectively relevant: wiki contains related "
                f"content ('{topic[:60]}')"
            )

    # Signal 2: sustained error in the candidate's domain.
    if accuracy_tracker is not None:
        domain = _domain_of(candidate)
        if domain:
            try:
                sustained = bool(
                    accuracy_tracker.has_sustained_error(domain)
                )
            except Exception:
                sustained = False
            if sustained:
                return (
                    f"Retrospectively relevant: domain '{domain}' has "
                    f"sustained prediction error"
                )

    return None


def _topic_of(candidate: dict) -> str:
    """Extract a short topic string from a candidate record."""
    for key in ("result_summary", "summary", "content", "title"):
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            return value[:120]
    # Fall back to first scene_topic if present
    topics = candidate.get("scene_topics") or []
    if isinstance(topics, list) and topics:
        return str(topics[0])[:120]
    return ""


def _domain_of(candidate: dict) -> str:
    """Build the accuracy_tracker domain key from a candidate."""
    agent = str(candidate.get("agent") or "")
    op = str(candidate.get("operation") or "")
    if not agent or not op:
        return ""
    # Match accuracy_tracker.domain_key canonical form.
    return f"{agent.strip().lower()}:{op.strip().lower()}"
