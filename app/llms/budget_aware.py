"""Provider-agnostic budget-gate LLM wrapper.

Subclass of CrewAI's top-level :class:`crewai.LLM` that injects a
per-call pre-check against an injected "budget module" (typically
:mod:`app.llm_anthropic_budget` or :mod:`app.llm_openrouter_budget`).

Why a subclass and not a decorator?
-----------------------------------

CrewAI Agent validation accepts ``Agent(llm=...)`` only when ``llm``
is a string or a :class:`crewai.llms.base_llm.BaseLLM` subclass.  A
plain function-decorator or composition wrapper fails Pydantic
validation with "Input should be a valid string".  This is the same
constraint that drove :class:`app.llms.credit_aware_anthropic.CreditAwareAnthropicCompletion`
to subclass rather than wrap; we follow the same pattern.

Why parameterised by ``budget_module`` and not per-provider classes?
--------------------------------------------------------------------

The pre-check logic is identical across providers: read cap, read
spend, compare, raise typed exception.  Only the **identity** of the
cap and exception differs.  Parameterising by module gives one
subclass with two configurations rather than two near-identical
subclasses — fewer code paths, fewer regression surfaces.

The Anthropic path still uses :class:`CreditAwareAnthropicCompletion`
rather than this wrapper because Anthropic has *additional* layers
(credit-exhausted failover, wall-clock timeout, OR-failover handling)
that don't apply to OpenRouter.  When those layers are extracted into
mixins, ``BudgetAwareCompletion`` and ``CreditAwareAnthropicCompletion``
can compose them — but that's a future refactor, not a current
hack.

Estimated cost
--------------

The pre-check needs a worst-case USD estimate.  Callers provide a
factory function ``estimated_cost_fn() -> float`` that the wrapper
calls right before each pre-check.  This keeps the estimate fresh
(if the per-call cost varies with prompt size) without coupling the
wrapper to any particular cost-derivation strategy.  When omitted,
defaults to ``0.0`` — the cap then catches only "already over"
cases, not "next call would push over".  The factory wires a real
estimator at construction time (max_tokens × cost_output_per_m).
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from pydantic import PrivateAttr

logger = logging.getLogger(__name__)


def _make_class():
    """Build the BudgetAwareCompletion class lazily so importing this
    module doesn't pay the cost of importing crewai.LLM (which pulls
    in litellm and the full CrewAI framework — ~1.9s).

    The class is constructed on first attribute access via
    ``__getattr__`` below.  Subsequent accesses use the cached
    instance.
    """
    from crewai import LLM as _LLM

    class BudgetAwareCompletion(_LLM):
        """``crewai.LLM`` subclass with per-call budget pre-check.

        Composition contract — :meth:`set_budget_module` (or the
        keyword arg at construction) injects the budget module.  The
        module must expose:

          * ``pre_check(estimated_cost_usd: float) -> None`` — raises
            a typed exception when the cap would be exceeded.

        :meth:`set_estimated_cost_fn` injects the per-call estimator.
        Defaults to a constant 0.0 if not set.

        Both setters return ``self`` so the factory's wiring stays a
        single expression.
        """

        _budget_module: Optional[Any] = PrivateAttr(default=None)
        _estimated_cost_fn: Optional[Callable[[], float]] = PrivateAttr(
            default=None,
        )

        def set_budget_module(self, module: Any) -> "BudgetAwareCompletion":
            """Inject the budget module to consult on each call."""
            self._budget_module = module
            return self

        def set_estimated_cost_fn(
            self,
            fn: Callable[[], float],
        ) -> "BudgetAwareCompletion":
            """Inject the per-call worst-case-cost estimator."""
            self._estimated_cost_fn = fn
            return self

        def _run_pre_check(self) -> None:
            """Consult the budget module's pre_check, if injected.

            Failure-OPEN: if the budget module isn't set or its
            pre_check raises a non-cap exception, the call proceeds.
            Typed cap-exceeded exceptions inherit from
            :class:`app.llm_anthropic_budget.CapExceededError` and are
            propagated — callers catch the base or a subclass with
            intent.
            """
            if self._budget_module is None:
                return
            try:
                est = (self._estimated_cost_fn or (lambda: 0.0))()
            except Exception:
                est = 0.0
            try:
                self._budget_module.pre_check(estimated_cost_usd=est)
            except Exception as exc:
                # Typed cap-exceeded propagates.  Anything else is a
                # budget-module bug — failure-OPEN: log and let the
                # call proceed.  The base class lives in the neutral
                # ``app.llm_cost_exceptions`` module; every per-
                # provider cap subclasses from it.
                from app.llm_cost_exceptions import CapExceededError
                if isinstance(exc, CapExceededError):
                    raise
                logger.debug(
                    "BudgetAwareCompletion: budget pre_check raised "
                    "unexpectedly — letting call proceed",
                    exc_info=True,
                )

        def _inject_cache_control(self, args, kwargs):
            """Mark the long system prompt for OpenRouter prompt caching.

            Replaces the retired ``prompt_cache_hook`` litellm monkeypatch
            — injection now lives in our own subclass.  ``messages`` may be
            positional (``call(messages, …)``) or a kwarg.  Failure-soft.
            """
            try:
                from app.llm_cache_control import inject_cache_control
                model = getattr(self, "model", "")
                if args:
                    return (inject_cache_control(args[0], model), *args[1:]), kwargs
                if "messages" in kwargs:
                    kwargs["messages"] = inject_cache_control(
                        kwargs["messages"], model,
                    )
            except Exception:
                pass
            return args, kwargs

        def call(self, *args, **kwargs):  # type: ignore[override]
            self._run_pre_check()
            args, kwargs = self._inject_cache_control(args, kwargs)
            return super().call(*args, **kwargs)

        async def acall(self, *args, **kwargs):  # type: ignore[override]
            self._run_pre_check()
            args, kwargs = self._inject_cache_control(args, kwargs)
            return await super().acall(*args, **kwargs)

    return BudgetAwareCompletion


_cached_class: Optional[type] = None


def __getattr__(name: str):
    """Lazy class-construction.

    Importing crewai.LLM is expensive (~1.9s for the full framework).
    By exposing ``BudgetAwareCompletion`` only on attribute access,
    modules can safely import this file at boot without paying the
    crewai-import cost.  Once accessed, the class is cached for
    subsequent uses.
    """
    global _cached_class
    if name == "BudgetAwareCompletion":
        if _cached_class is None:
            _cached_class = _make_class()
        return _cached_class
    raise AttributeError(name)


__all__ = ["BudgetAwareCompletion"]
