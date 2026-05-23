"""briefing_evolution — dynamically-growing morning briefing.

The user's morning briefing has a fixed-shape core (calendar / unread /
tickets / workstream news / system status) plus a *growing tail* of
sections discovered by trialling candidates and keeping the ones the
operator doesn't 👎 on Signal.

Subsystem layout::

    briefing_evolution/
      catalog.py        — registry of candidate section modules
      trial_state.py    — JSON state machine (proposed → trial → adopted | dropped)
      selector.py       — picks ≤1 trial section per briefing
      feedback_bridge.py — signal_ts ↔ trial_id map

    briefing_sections/  — the candidate modules themselves

Each candidate module exposes ``id`` / ``display_name`` / ``gather()``
and is wired up by the catalog at boot. Adopted sections are appended
to every subsequent briefing; the at-most-one trial section is marked
with a 👎-to-drop hint.

Composes with :mod:`app.agreement_self_model.agreement_ledger`
(``category="proactive_briefing"``) and the identity continuity
ledger (``briefing_section_decision`` event kind) — no parallel
Goodhart-guard plumbing.
"""
from __future__ import annotations

__all__ = ["select_trial_for_briefing", "render_adopted_sections"]


def select_trial_for_briefing():
    """Lazy re-export so callers can do
    ``from app.life_companion.briefing_evolution import select_trial_for_briefing``
    without importing the whole subsystem at module load."""
    from app.life_companion.briefing_evolution.selector import select_trial_for_briefing as _impl
    return _impl()


def render_adopted_sections():
    """Lazy re-export — same rationale as above."""
    from app.life_companion.briefing_evolution.selector import render_adopted_sections as _impl
    return _impl()
