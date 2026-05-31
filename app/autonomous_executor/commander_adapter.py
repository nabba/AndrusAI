"""Commander adapter — translates an ExecutorStep into a Commander
dispatch and packs the response into a CommanderResult.

Phase 2 piece 2b, 2026-05-20.

The driver (``advance_one_step``) is shape-complete and injects this
adapter via the ``commander_fn`` parameter. The adapter is the
boundary between the executor's typed state machine and the existing
CommanderOrchestrator. Keeping it in a separate module means:

  * The driver stays untestable-against-Commander (tests inject stubs).
  * The Commander adapter is the only piece that imports the heavy
    orchestrator module; everything else stays lightweight.
  * Future v2 (with cost+token extraction) is a drop-in update here
    without rippling through the driver.

Design notes:

  * ``commander_provider`` is a callable returning a CommanderOrchestrator
    instance. Lazy so the heavy module + ``__init__`` isn't loaded
    until first use. Production callers pass ``default_commander_provider``;
    tests pass a stub.

  * ``sender`` is set to ``"executor:<run_id>"`` so audit logs, recovery
    loop bookkeeping, and per-sender quotas correctly attribute the work
    to the executor (not to the operator who originally typed
    ``/delegate``).

  * v1 returns ``cost_usd=0.0`` + ``tokens_used=0``. The wall-clock
    budget cap is the primary safety bound. Phase 2 piece 2c will
    sample ``app.llm_benchmarks.get_last_request_cost`` to populate
    the real numbers.

  * Exceptions from ``commander.handle`` propagate up; the driver's
    ``_execute_step`` catches them and marks the step ``FAILED``.
    This is the load-bearing failure-isolation property — adapter
    failure stays scoped to the one step.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from app.autonomous_executor.driver import CommanderFn, CommanderResult
from app.autonomous_executor.models import ExecutorRun, ExecutorStep

logger = logging.getLogger(__name__)


# Cache for the production commander_provider singleton — avoids
# building a fresh CommanderOrchestrator instance every scheduler tick.
# Cleared by tests via reset_for_tests.
_PRODUCTION_COMMANDER_CACHE: list = []


def default_commander_provider():
    """Production provider: returns a cached CommanderOrchestrator.

    Cached because instantiation is heavy (souls loaded, tool registry
    populated, LLM clients warmed). A scheduler tick should re-use
    the existing instance, not pay the boot cost each time.
    """
    if _PRODUCTION_COMMANDER_CACHE:
        return _PRODUCTION_COMMANDER_CACHE[0]
    # Lazy import — the orchestrator module pulls in crewai, llm_factory,
    # tools, souls. Keeping the import inside the function means
    # ``app.autonomous_executor`` stays lightweight at module-load.
    # NB: the orchestrator class is ``Commander`` — it was renamed from
    # ``CommanderOrchestrator``. Import the current name; the stale name
    # raised ImportError here, silently FAILED-ing every executor step
    # (research investigate/design_experiment/draft + all standard runs).
    from app.agents.commander.orchestrator import Commander
    commander = Commander()
    _PRODUCTION_COMMANDER_CACHE.append(commander)
    return commander


def reset_for_tests() -> None:
    """Test helper — clears the cached production commander."""
    _PRODUCTION_COMMANDER_CACHE.clear()


def make_commander_adapter(
    commander_provider: Optional[Callable[[], "object"]] = None,
) -> CommanderFn:
    """Build a CommanderFn the driver can use.

    Parameters
    ----------
    commander_provider
        Callable returning a CommanderOrchestrator-like object with a
        ``handle(user_input, sender, attachments=None)`` method. If
        ``None``, the production singleton is used (lazy import).

    Returns
    -------
    CommanderFn
        A function ``(step, run) -> CommanderResult`` the driver
        invokes per step.

    The returned adapter:
      * Calls ``commander_provider()`` lazily on every invocation —
        Python attribute lookup is cheap; the provider itself caches
        the actual orchestrator.
      * Passes ``step.description`` as the user input.
      * Tags the sender as ``"executor:<run_id>"`` so downstream
        bookkeeping (audit log, per-sender quotas, conversation
        memory) attributes the work to the executor, not to the
        operator who typed ``/delegate``.
      * v1 cost stays at zero — the wall-clock cap is the primary
        safety bound.
    """
    provider = commander_provider or default_commander_provider

    def _adapter(step: ExecutorStep, run: ExecutorRun) -> CommanderResult:
        commander = provider()
        sender = f"executor:{run.run_id}"
        # Phase 2 piece 2h (2026-05-20): bind the executor ContextVar
        # for the duration of this Commander call so downstream tools
        # (``coding_session_start``) detect the executor origin and
        # auto-tag sessions for cleanup. Best-effort import — adapter
        # is unblocked if the bridge module is unavailable.
        try:
            from app.autonomous_executor.coding_session_bridge import (
                set_executor_context,
            )
            ctx_mgr = set_executor_context(run.run_id)
        except Exception:
            from contextlib import nullcontext
            ctx_mgr = nullcontext()

        with ctx_mgr:
            # Forward step description to Commander; let any exception
            # propagate (driver catches → step FAILED).
            text = commander.handle(
                user_input=step.description,
                sender=sender,
            )
        # Defensive coerce — orchestrator.handle is documented to
        # return str but we don't want a stringly-typed None
        # crashing serialisation downstream.
        if text is None:
            text = ""
        elif not isinstance(text, str):
            text = str(text)
        return CommanderResult(
            text=text,
            cost_usd=0.0,
            tokens_used=0,
        )

    return _adapter
