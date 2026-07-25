"""outcome.py — "this crew produced no answer" signal.

Why this exists
---------------
Crews return plain strings.  When a crew *fails to produce an answer* it
has historically returned a string *describing* that failure — e.g.
``"Creative run hit its $0.10 budget before producing output …"`` or
``"I completed the research passes, but the evidence gate did not clear
the draft …"``.  Downstream, nothing could tell those apart from a real
answer, so they flowed into the quality gates and got reviewed *as if
they were answers*.

The 2026-07-24 golden-set run shows the consequence: the critic was handed
a budget-abort notice, correctly observed "there is no content here",
returned ``BLOCK``, and the user received

    "I'm withholding the draft because adversarial review found an
     unresolved critical quality issue: The creative crew failed to
     produce any content due to a budget exhaustion error"

— which blames *review* for an upstream budget bug and buries the one
fact the operator needed.  See
``reports/GATE_DIAGNOSIS_2026-07-25.md``.

The contract
------------
A crew that knows it produced no answer calls :func:`record_no_answer`
with the real cause.  The orchestrator calls :func:`consume_no_answer`
before its quality gates; when a signal is present it skips vetting and
the critic entirely (there is nothing to review) and reports the cause
directly.

Scope note: this is a :class:`~contextvars.ContextVar`, so it is visible
to the thread that ran the crew.  On the single-crew dispatch path — where
both observed failures occurred — the crew and the gate decision share a
thread, so the signal is seen.  Multi-crew dispatch already distinguishes
failures structurally via ``ParallelResult.success``.
"""

from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NoAnswer:
    """A crew ran to completion but produced nothing usable.

    Attributes:
        crew: which crew gave up (``"creative"``, ``"deep_research"``, …).
        cause: operator-facing reason, already human-readable. This is what
            the user is told, so it names the actual lever where one exists.
    """

    crew: str
    cause: str

    def user_message(self) -> str:
        """The honest reply: name the crew and the real cause, nothing else."""
        cause = (self.cause or "").strip() or "no reason was recorded"
        return f"The {self.crew} step didn't produce an answer: {cause}"


_no_answer: contextvars.ContextVar[NoAnswer | None] = contextvars.ContextVar(
    "crew_no_answer", default=None,
)


def record_no_answer(crew: str, cause: str) -> None:
    """Flag that ``crew`` finished without producing a usable answer.

    Last writer wins: if several steps give up, the most recent cause is the
    one reported.  Never raises — a telemetry-shaped helper must not be able
    to break a crew that is already in a degraded path.
    """
    try:
        _no_answer.set(NoAnswer(crew=str(crew or "unknown"), cause=str(cause or "")))
        logger.info("crew outcome: %s produced no answer (%s)", crew, cause)
    except Exception:  # pragma: no cover — defensive
        logger.debug("record_no_answer failed", exc_info=True)


def consume_no_answer() -> NoAnswer | None:
    """Return and clear any pending no-answer signal.

    Clearing on read keeps a stale signal from a previous crew in the same
    request from suppressing the gates for a later crew that *did* answer.
    """
    try:
        pending = _no_answer.get()
        if pending is not None:
            _no_answer.set(None)
        return pending
    except Exception:  # pragma: no cover — defensive
        logger.debug("consume_no_answer failed", exc_info=True)
        return None


def clear_no_answer() -> None:
    """Drop any pending signal.  Called at request boundaries so a signal
    can never leak across requests on a pooled thread."""
    try:
        _no_answer.set(None)
    except Exception:  # pragma: no cover — defensive
        pass
