"""selector — pick at most one trial section per briefing + render adopted ones.

Public surface called by the morning briefing composer:

  * :func:`select_trial_for_briefing` → ``(section_id, lines)`` or None
  * :func:`render_adopted_sections`  → list of ``(section_id, display_name, lines)``

Anti-thrash rules:
  * One trial section per briefing (the user reads one new thing at a time).
  * Re-proposes DROPPED candidates whose 90d cooldown has elapsed before
    picking, so the catalog can grow back over time.
  * Selection priority among PROPOSED candidates: oldest ``first_seen_at``
    first (FIFO) — predictable, avoids surprise.
  * A TRIAL section pending feedback re-shows on every briefing until it
    auto-adopts (≥3 shows or ≥7d), satisfying "no answer = keep".
"""
from __future__ import annotations

import logging

from app.life_companion.briefing_evolution import catalog, trial_state
from app.life_companion.briefing_evolution.trial_state import Status

logger = logging.getLogger(__name__)


def render_adopted_sections() -> list[tuple[str, str, list[str]]]:
    """Render every ADOPTED candidate. Each section soft-fails individually:
    if ``gather()`` raises or returns empty, the section is skipped for
    this briefing (but stays ADOPTED so it can recover next pass)."""
    out: list[tuple[str, str, list[str]]] = []
    adopted_ids = trial_state.adopted_section_ids()
    for sid in adopted_ids:
        cand = catalog.get(sid)
        if cand is None:
            continue
        try:
            lines = cand.gather() or []
        except Exception:
            logger.debug("adopted section %s raised", sid, exc_info=True)
            continue
        if not lines:
            continue
        out.append((sid, cand.display_name, lines))
    return out


def _proposed_ids_in_order() -> list[str]:
    """All PROPOSED candidates in FIFO order. The selector walks this
    list and picks the first one whose ``gather()`` returns data —
    so a candidate that has nothing to say this morning doesn't
    starve the queue."""
    rows = [r for r in trial_state.list_sections() if r.status == Status.PROPOSED]
    rows.sort(key=lambda r: r.first_seen_at or "")
    return [r.id for r in rows]


def _pending_trial() -> str | None:
    """The single TRIAL candidate currently being shown (if any). Picked
    by oldest first_shown — there should usually be only one, but if the
    state machine ever falls into a state with multiple in TRIAL, we
    keep showing the oldest until it resolves."""
    rows = [r for r in trial_state.list_sections() if r.status == Status.TRIAL]
    if not rows:
        return None
    rows.sort(key=lambda r: r.first_shown_at or r.first_seen_at or "")
    return rows[0].id


def select_trial_for_briefing() -> tuple[str, str, list[str]] | None:
    """Pick at most one trial section for this briefing.

    Returns ``(section_id, display_name, lines)`` or None when there's
    nothing to trial. The caller is responsible for marking the show
    via :func:`trial_state.record_show` (separated so a briefing that
    fails to send doesn't bump the counter)."""
    # 1. Promote cooled-down DROPPED back to PROPOSED so they re-enter
    # the queue. Idempotent + cheap; runs every selection.
    trial_state.maybe_repropose_dropped()

    # 2. If a TRIAL is already pending feedback, keep showing it until
    # it auto-adopts or is dropped. Matches "no answer = keep".
    pending = _pending_trial()
    if pending is not None:
        cand = catalog.get(pending)
        if cand is None:
            return None
        try:
            lines = cand.gather() or []
        except Exception:
            logger.debug("pending trial %s gather raised", pending, exc_info=True)
            return None
        if not lines:
            return None
        return cand.id, cand.display_name, lines

    # 3. Walk PROPOSED candidates FIFO until one has data. A candidate
    # with nothing to say this morning is skipped (stays PROPOSED for
    # next pass) — without this loop, a single dry candidate at the
    # head of the queue would block every other proposal indefinitely.
    for sid in _proposed_ids_in_order():
        cand = catalog.get(sid)
        if cand is None:
            continue
        try:
            lines = cand.gather() or []
        except Exception:
            logger.debug("proposed candidate %s gather raised", sid, exc_info=True)
            continue
        if not lines:
            continue
        return cand.id, cand.display_name, lines
    return None
