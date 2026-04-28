"""Scoped LLM-mode override for compile-time Learner calls.

Wraps ``app.llm_mode.set_mode()`` with save-and-restore semantics so the
nightly compiler can pin Learner calls to ``llm_mode="free"`` without
permanently mutating runtime state.

This **mutates global state** for the duration of the block — there is
no per-thread scoping in ``llm_mode``. Only safe when the caller can
guarantee no concurrent user-facing work is running. The compiler is
registered as a HEAVY idle-scheduler job, which guarantees
``_active_tasks == 0`` at job start; the compiler additionally checks
``idle_scheduler.should_yield()`` between LLM calls and breaks early
when a user task arrives, restoring the original mode in the ``finally``
block before yielding.

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from app.llm_mode import get_mode, set_mode

logger = logging.getLogger(__name__)


@contextmanager
def force_llm_mode(mode: str = "free") -> Iterator[str]:
    """Temporarily set the global ``llm_mode`` for the duration of the block.

    Yields the previous mode (in case the caller wants to log it).
    Restores on exit even when the block raises.

    Usage::

        with force_llm_mode("free"):
            content = learner_llm.call(prompt)   # uses free-tier cascade

    Failure to restore is logged but never raised — the caller's primary
    work is the LLM call, and a restore-mode failure should not propagate
    upward and obscure the real error.
    """
    old = get_mode()
    try:
        set_mode(mode)
        if old != mode:
            logger.debug("transfer_memory.llm_scope: %s → %s", old, mode)
        yield old
    finally:
        try:
            if get_mode() != old:
                set_mode(old)
        except Exception:
            logger.warning(
                "transfer_memory.llm_scope: failed to restore mode %s",
                old,
                exc_info=True,
            )
