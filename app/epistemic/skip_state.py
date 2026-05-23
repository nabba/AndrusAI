"""Per-request skip-verification state (Verified Implementation Plan §7 item 3, 2026-05-23).

The Reflexive Verification Layer (``epistemic.orchestrator_hook.
gate_output``) is the last-mile claim-validation gate for every
Commander reply. For *trivial* fast-route patterns — those that
produce a structured data lookup with no factual claims (today's
calendar, file list, ticket count, open threads) — running the gate
is pure overhead: there's nothing for the evaluator to verify.

This module provides a per-request flag that the routing layer sets
when it matches a structurally-trivial pattern, and that the gate
reads to short-circuit.

Design notes
────────────

  * ``ContextVar`` so it's task-local — distinct concurrent requests
    can carry different flags without leaking.
  * Default ``False`` — when no one sets it, the gate runs as before.
  * ``skip_scope()`` context manager so callers can opt in for the
    scope of a single request without having to remember to reset.
  * The flag is *advisory* — the gate is free to honour it or ignore
    it. v1 honours it; if future Goodhart-style failures emerge the
    gate can be tightened independently.

Pinning
───────

Tests in ``tests/test_skip_verification.py`` pin that:
  * trivial patterns set the flag,
  * non-trivial patterns leave it default,
  * the gate respects the flag,
  * the flag never persists across requests (ContextVar discipline).
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

logger = logging.getLogger(__name__)


# Per-task skip flag. Default False — the gate runs unless the
# routing layer explicitly opts the request out.
_skip_verification: ContextVar[bool] = ContextVar(
    "epistemic_skip_verification", default=False,
)


def is_skip_set() -> bool:
    """Read the current scope's skip flag.

    Returns ``False`` when no scope has set it (the canonical case).
    Failure-isolated: any ContextVar lookup error degrades to False
    (the safe default — the gate runs).
    """
    try:
        return bool(_skip_verification.get())
    except LookupError:
        return False
    except Exception:
        logger.debug(
            "skip_state: get raised; degrading to False", exc_info=True,
        )
        return False


def set_skip(value: bool) -> object:
    """Set the skip flag for the current scope.

    Returns a ContextVar ``Token`` the caller can pass to
    :func:`reset_skip` to undo this set. Most callers should prefer
    :func:`skip_scope` instead — the context-manager form makes the
    reset automatic.
    """
    return _skip_verification.set(bool(value))


def reset_skip(token: object) -> None:
    """Undo a previous :func:`set_skip` using its token.

    Best-effort — a stale token from a different ContextVar generation
    raises ``ValueError``; we swallow it (the scope is exiting anyway).
    """
    try:
        _skip_verification.reset(token)  # type: ignore[arg-type]
    except (ValueError, LookupError):
        pass
    except Exception:
        logger.debug(
            "skip_state: reset raised", exc_info=True,
        )


@contextmanager
def skip_scope(value: bool = True) -> Iterator[None]:
    """Context manager that sets ``skip_verification`` for the with-block.

    Usage::

        with skip_scope(True):
            reply = run_crew_and_return()
            # gate_output() will short-circuit while inside this block

    The flag resets automatically on exit, including on exception.
    Composes with ContextVar semantics — nested scopes stack and
    unwind correctly.
    """
    token = _skip_verification.set(bool(value))
    try:
        yield
    finally:
        try:
            _skip_verification.reset(token)
        except Exception:
            # Edge case: ContextVar generation mismatch (e.g., the
            # scope was forked into a different asyncio task and the
            # reset arrives in the wrong context). Fall back to a
            # direct set-False so the next is_skip_set() returns the
            # safe default.
            try:
                _skip_verification.set(False)
            except Exception:
                pass


__all__ = ["is_skip_set", "set_skip", "reset_skip", "skip_scope"]
