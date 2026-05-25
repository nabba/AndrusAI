"""Neutral home for cost-related exception base classes.

Previously :class:`CapExceededError` lived inside
:mod:`app.llm_anthropic_budget` — the first per-provider budget
module to need a typed exception became the de-facto owner.  When
OpenRouter (and any future provider) needed to subclass the base,
they had to cross-import from a sibling provider's module, which
is a structural smell.

This module is provider-neutral.  Per-provider budget modules
subclass from here; generic wrappers (:class:`app.llms.budget_aware.BudgetAwareCompletion`,
the orchestrator's catch arm, the per-call observer in
:mod:`app.llm_factory_probe`) import the base.
"""
from __future__ import annotations


class CapExceededError(Exception):
    """Base class for per-provider daily-cap-exceeded exceptions.

    Per-provider subclasses (``AnthropicDailyCapExceeded``,
    ``OpenRouterDailyCapExceeded``, future providers) inherit from
    this.  Generic wrappers — notably
    :class:`app.llms.budget_aware.BudgetAwareCompletion` and the
    orchestrator's typed-catch arm — match on this base so adding
    a new provider's cap exception is a one-line subclass with no
    plumbing.

    Attributes
    ----------
    provider
        Class-level provider identifier (``"Anthropic"``,
        ``"OpenRouter"``).  Surfaced in the alert / user-reply
        text so a new provider's exception inherits a correct
        message without overriding ``__init__``.
    today_spent_usd
        Spend in the rolling 24h window prior to this call.
    daily_cap_usd
        The configured ceiling.
    estimated_cost_usd
        The next call's estimate that triggered the refusal.
    """

    provider: str = "<unknown>"

    def __init__(
        self,
        today_spent_usd: float,
        daily_cap_usd: float,
        estimated_cost_usd: float,
    ) -> None:
        self.today_spent_usd = today_spent_usd
        self.daily_cap_usd = daily_cap_usd
        self.estimated_cost_usd = estimated_cost_usd
        super().__init__(
            f"{self.provider} daily cap ${daily_cap_usd:.2f} would be "
            f"exceeded — already spent ${today_spent_usd:.4f} in "
            f"rolling 24h; next call estimated ${estimated_cost_usd:.4f}"
        )


__all__ = ["CapExceededError"]
