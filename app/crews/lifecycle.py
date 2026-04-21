"""
lifecycle.py — Unified envelope for every crew run.

Responsibility
--------------
Wrap the crew's actual work (agent building + ``crew.kickoff()`` +
crew-specific post-work like Torrance scoring or ``store_policy``) in
a uniform lifecycle envelope.  The envelope emits three typed events
— started / completed / failed — and carries the usual data
(crew name, agent role, task text, outcome, timing, token/cost
accounting).  **It does not contain the sinks themselves.**  Sinks
subscribe to the events via ``app.crews.events`` — see
``app.crews.event_handlers`` for the default set (belief state,
Firebase, metric, journal, auto-skill).

Result: adding or reshaping a sink is a single handler registration,
not an edit of this module.

What the body of the ``with`` block does
----------------------------------------
Everything the crew does — agent construction, crew.kickoff(),
crew-specific post-work.  The envelope doesn't care about the shape of
that work.  If the body raises, ``fire_crew_failed`` runs and the
exception re-raises to the caller (so outer layers like
``diagnose_and_fix`` can see the real exception).  If the body returns
normally, ``fire_crew_completed`` runs after best-effort token/cost
capture from ``rate_throttle.get_active_tracker``.

Usage
-----
::

    with crew_lifecycle(
        crew_name="research",
        agent_role="researcher",
        task_title="Research: ...",
        task_description=task_description,
        parent_task_id=parent_task_id,
        mode="delegated",   # or None
        model=_model_name,
    ) as ctx:
        # …build agents, Crew(), kickoff()…
        result = str(crew.kickoff())
        ctx.set_outcome(result)
        return result

The ``ctx`` object lets the crew optionally override:
  * ``ctx.set_outcome(result)``             — stored in Firebase + journal
  * ``ctx.set_tool_call_count(n)``          — explicit override for auto-skill gating
  * ``ctx.set_cost(tokens=, model=, cost_usd=)`` — override tracker-derived cost
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, Optional

from app.crews import events as crew_events
from app.crews.events import CrewEventContext

logger = logging.getLogger(__name__)


# ── Runtime handle passed to the body of the `with` block ──


@dataclass
class CrewContext:
    """Thin pass-through to the crew body.

    Separates "data the handlers need" (stored on :class:`CrewEventContext`)
    from "mutator methods the crew body can call" (this class).  The
    lifecycle manager synchronises them into the event context right
    before firing the terminal event.
    """
    _event_ctx: CrewEventContext
    _cost_explicit: bool = field(default=False, init=False)

    @property
    def task_id(self) -> str:
        return self._event_ctx.task_id

    def set_outcome(self, result: str) -> None:
        self._event_ctx.outcome = result or ""

    def set_tool_call_count(self, n: int) -> None:
        self._event_ctx.tool_call_count = n

    def set_cost(self, *, tokens: int, model: str, cost_usd: float) -> None:
        self._event_ctx.tokens_used = tokens
        self._event_ctx.cost_model = model
        self._event_ctx.cost_usd = cost_usd
        self._cost_explicit = True


# ── The context manager ──


@contextmanager
def crew_lifecycle(
    crew_name: str,
    agent_role: str,
    task_title: str,
    *,
    task_description: str = "",
    parent_task_id: Optional[str] = None,
    mode: Optional[str] = None,
    model: str = "",
) -> Iterator[CrewContext]:
    """Wrap the body of a crew run with the standard lifecycle envelope.

    Emits three events, consumed by whatever handlers are registered in
    ``app.crews.events`` (see ``app.crews.event_handlers`` for the
    default set):

        on_crew_started    — before the body runs
        on_crew_completed  — body returned normally
        on_crew_failed     — body raised (exception re-raised to caller)

    Parameters
    ----------
    crew_name : Firebase crew name (e.g. ``"research"``).
    agent_role : belief-state role string (e.g. ``"researcher"``).
    task_title : short label shown on the dashboard.
    task_description : fuller text for the journal / diagnostic layer.
    parent_task_id : id of an enclosing task (nested crew dispatch).
    mode : optional qualifier on the metric label (e.g. ``"delegated"``).
    model : best-effort model id at dispatch time.
    """
    start = time.monotonic()

    event_ctx = CrewEventContext(
        crew_name=crew_name,
        agent_role=agent_role,
        task_title=task_title,
        task_description=task_description,
        task_id="",  # filled in by the firebase_started handler
        mode=mode,
        parent_task_id=parent_task_id,
        model=model,
    )

    crew_events.fire_crew_started(event_ctx)

    ctx = CrewContext(_event_ctx=event_ctx)

    try:
        yield ctx
    except BaseException as exc:
        # Failure path — exceptions propagate after the handlers run.
        event_ctx.duration_s = time.monotonic() - start
        event_ctx.error = exc
        crew_events.fire_crew_failed(event_ctx)
        raise

    # Success path — compute duration + auto-capture cost from the
    # active request tracker if the crew didn't provide it explicitly.
    event_ctx.duration_s = time.monotonic() - start

    if not ctx._cost_explicit:
        try:
            from app.rate_throttle import get_active_tracker
            tracker = get_active_tracker()
            if tracker is not None:
                event_ctx.tokens_used = tracker.total_tokens
                event_ctx.cost_usd = tracker.total_cost_usd
                event_ctx.cost_model = (
                    ", ".join(sorted(tracker.models_used))
                    if tracker.models_used else ""
                )
        except Exception:
            logger.debug("lifecycle: active-tracker read raised",
                         exc_info=True)

    crew_events.fire_crew_completed(event_ctx)
