"""app.research.binding — bind a research run to a Thread (Phase D).

Phase D is *cross-run learning*: a finished research run should leave a
durable trace that the next run can find, and a closure should distil what
was tried so the lesson outlives the run. The system already has exactly the
primitive for this — ``app.threads`` — and it already does the two things
Phase D wants, *for free*:

  * :func:`app.threads.lifecycle.create_thread` runs ``consult_before_create``
    on every new thread: it queries the ``lessons_learned`` KB for adjacent
    past closures and Signal-notifies the operator when a near-duplicate line
    of inquiry already exists. That is the "have we researched this before?"
    dedup, delivered at *creation* time.
  * :func:`resolve_thread` / :func:`abandon_thread` run
    ``distill_on_closure`` on every terminal transition: the approaches-tried
    summary is written back into the ``lessons_learned`` KB (+ a HOT-1 affect
    snapshot). That is the "what did we learn?" capture, delivered at
    *closure* time.

So Phase D owns no new learning machinery. It is a thin, host-safe, fully
failure-isolated bridge:

    research run created  →  bind_run_to_thread()  →  Thread (OPEN)
    research run terminal →  close_thread_for_run() →  resolve / abandon
                                                       (→ distil → KB)

The back-pointer from run to thread rides in ``run.notes`` (the
:class:`~app.autonomous_executor.models.ExecutorRun` has no metadata dict),
which makes every operation idempotent across scheduler ticks: a run already
bound returns its existing thread id rather than creating a second thread, and
a thread already in a terminal state is left untouched.

Design notes
------------

* **Host-safe.** Module load is pure stdlib + the lightweight executor types.
  ``app.threads`` is itself host-safe stdlib, but it is imported lazily inside
  the functions to match the ``app.research`` package discipline (heavy /
  optional subsystems are never pulled at import time) and to keep this module
  importable by the gateway-side call sites without surprise.
* **Failure-isolated.** A broken thread store must never block a research run
  from being created or from reaching its terminal state — every public
  function swallows its exceptions and degrades to "no binding".
* **Idempotent.** Safe to call ``bind_run_to_thread`` more than once (returns
  the already-bound id) and ``close_thread_for_run`` on every tick (no-ops
  once the thread is terminal, or while the run is still in flight).
* **No new master switch.** The whole research-delegate path is already behind
  ``autonomous_executor_enabled`` (default OFF), and the synchronous
  :func:`app.research.run.run_to_completion` path is opt-in
  (``bind_thread=False``). Binding adds an operator-visible Thread — which *is*
  the desired cross-run-learning artifact — so it is left on within the
  already-gated feature rather than hidden behind a second knob.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.autonomous_executor.models import ExecutorRun, ExecutorStatus

logger = logging.getLogger(__name__)


# The back-pointer lives in ``run.notes``. ``record_note`` prefixes each note
# with a ``[<iso-ts>] `` stamp, so callers must *substring-search* for this
# marker rather than match the start of the note.
_THREAD_NOTE_PREFIX = "research-thread:"

# Thread titles are display-only; keep them readable.
_TITLE_PREFIX = "Research: "
_MAX_TITLE_LEN = 120


__all__ = [
    "bind_run_to_thread",
    "thread_id_for_run",
    "close_thread_for_run",
]


# ── Back-pointer helpers ────────────────────────────────────────────────────


def thread_id_for_run(run: ExecutorRun) -> Optional[str]:
    """Return the thread id bound to ``run``, or ``None`` if unbound.

    Scans ``run.notes`` newest-first for the :data:`_THREAD_NOTE_PREFIX`
    marker and returns the first token after it. Newest-first so a (would-be
    pathological) double-bind resolves to the most recent thread.
    """
    for note in reversed(run.notes or []):
        idx = note.find(_THREAD_NOTE_PREFIX)
        if idx < 0:
            continue
        tail = note[idx + len(_THREAD_NOTE_PREFIX):].strip()
        if not tail:
            continue
        tid = tail.split()[0].strip()
        if tid:
            return tid
    return None


def _thread_title(goal: str) -> str:
    body = (goal or "").strip() or "research run"
    budget = _MAX_TITLE_LEN - len(_TITLE_PREFIX)
    if len(body) > budget:
        body = body[: max(0, budget - 3)] + "..."
    return _TITLE_PREFIX + body


# ── Bind (at run creation) ──────────────────────────────────────────────────


def bind_run_to_thread(
    run: ExecutorRun,
    *,
    description: str = "",
) -> Optional[str]:
    """Create a Thread for ``run`` and stash the back-pointer in its notes.

    Idempotent: if the run already carries a thread back-pointer the existing
    id is returned and no new thread is created. Failure-isolated: any error
    creating or linking the thread degrades to ``None`` (the run is simply
    unbound — research still proceeds).

    Side effect: ``run.notes`` gains a ``research-thread:<id>`` note. The
    caller is responsible for persisting the run afterwards (the delegate API
    saves it right after creation).

    Creating the thread runs ``consult_before_create`` for free — that is the
    Phase-D "have we researched this before?" dedup.
    """
    existing = thread_id_for_run(run)
    if existing:
        return existing

    try:
        from app.threads.lifecycle import create_thread

        thread = create_thread(
            title=_thread_title(run.goal),
            description=(description or "").strip(),
        )
    except Exception:
        logger.debug("research.binding: create_thread failed", exc_info=True)
        return None

    # Record the back-pointer *before* the best-effort cross-link, so a
    # failure there can't make a retry create a second thread.
    run.record_note(f"{_THREAD_NOTE_PREFIX}{thread.id}")

    try:
        from app.threads.lifecycle import link_inquiry

        # ``related_inquiry_slugs`` is the thread's cross-reference list; the
        # run_id is a stable slug pointing back at the executor run.
        link_inquiry(thread.id, run.run_id)
    except Exception:
        logger.debug("research.binding: link_inquiry failed", exc_info=True)

    return thread.id


# ── Close (when the run reaches a terminal / blocked state) ──────────────────


def _closure_summary(run: ExecutorRun) -> str:
    """A one-line research summary for the thread-resolution note."""
    try:
        from app.research.run import summarise_run

        o = summarise_run(run)
    except Exception:
        logger.debug("research.binding: summarise_run failed", exc_info=True)
        return f"Research run {run.run_id} completed."

    line = (
        f"Research run {run.run_id} completed: "
        f"{o.n_literature} literature hit(s), "
        f"{o.n_hypotheses} hypothesis(es)."
    )
    if o.top_hypothesis:
        line += f" Leading hypothesis: {o.top_hypothesis[:200]}"
    if o.gate_action:
        line += f" Evidence gate: {o.gate_action}."
    return line


def close_thread_for_run(
    run: ExecutorRun,
    *,
    thread_id: Optional[str] = None,
) -> Optional[str]:
    """Close the thread bound to ``run`` to match the run's final state.

    Mapping:

      * ``COMPLETED``                          → ``resolve_thread`` (+ distil)
      * ``FAILED`` / ``BUDGET_EXHAUSTED`` / ``ABORTED`` → ``abandon_thread`` (+ distil)
      * ``BLOCKED`` (gate escalation)          → ``mark_blocked`` (stays open)
      * anything else (still in flight)        → no-op

    Returns the thread id on a successful transition (or when the thread is
    already in the target terminal state — the idempotent no-op case), else
    ``None``.

    Safe to call on every scheduler tick: a thread already RESOLVED/ABANDONED
    is left untouched (``resolve_thread`` / ``abandon_thread`` raise on
    terminal threads, so the terminal check happens *before* any transition),
    and a BLOCKED run whose thread already reflects the block is a no-op.

    The closure transition runs ``distill_on_closure`` for free — that is the
    Phase-D "what did we learn?" capture, written back to ``lessons_learned``.
    """
    tid = thread_id or thread_id_for_run(run)
    if not tid:
        return None

    status = run.status
    if not run.is_terminal and status is not ExecutorStatus.BLOCKED:
        # Run still in flight — nothing to close yet.
        return None

    try:
        from app.threads import store
        from app.threads.lifecycle import (
            abandon_thread,
            mark_blocked,
            resolve_thread,
        )
        from app.threads.models import ThreadStatus
    except Exception:
        logger.debug("research.binding: threads import failed", exc_info=True)
        return None

    try:
        thread = store.get(tid)
    except Exception:
        logger.debug("research.binding: thread read failed", exc_info=True)
        return None
    if thread is None:
        return None

    # Idempotent guard — a thread already RESOLVED/ABANDONED must not be
    # transitioned again. Read the status *before* attempting any transition.
    if thread.is_terminal:
        return tid

    try:
        if status is ExecutorStatus.COMPLETED:
            resolve_thread(tid, summary=_closure_summary(run))
        elif status is ExecutorStatus.BLOCKED:
            if thread.status is ThreadStatus.BLOCKED:
                # Thread already reflects the block — don't re-append.
                return tid
            mark_blocked(
                tid,
                run.blocked_reason
                or "research run blocked for operator review",
            )
        else:
            # FAILED / BUDGET_EXHAUSTED / ABORTED.
            reason = (
                run.failure_reason
                or run.abort_reason
                or f"research run ended {status.value}"
            )
            abandon_thread(tid, reason=reason)
    except Exception:
        logger.debug("research.binding: thread closure failed", exc_info=True)
        return None

    return tid
