"""Spend analyzer — pure functions over the audit log.

No I/O outside ``audit_log``; no side effects.  Produces typed
:class:`ProviderObservation` rows that the proposer turns into CRs.
Separating analysis from proposal lets us test analysis without
touching the proposal_bridge.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ── Per-day spend record ────────────────────────────────────────────


@dataclass(frozen=True)
class DailySpend:
    """One row per (provider, UTC day)."""
    provider: str
    day: str            # ISO date, e.g. "2026-05-25"
    spend_usd: float
    n_calls: int


# ── Per-provider 7-day observation ──────────────────────────────────


@dataclass(frozen=True)
class ProviderObservation:
    """Aggregate over 7 daily-spend rows for one provider.

    The observation is the analyser's output and the proposer's
    input.  It carries everything needed to decide whether to
    propose a cap adjustment and what the new value should be.
    """
    provider: str
    cap_usd: Optional[float]    # current operator-set cap, or None
    days: tuple[DailySpend, ...]
    max_day_spend_usd: float
    mean_day_spend_usd: float
    n_days_at_or_over_cap: int  # 0–7
    n_days_below_25pct_of_cap: int  # 0–7

    @property
    def total_spend_usd(self) -> float:
        return sum(d.spend_usd for d in self.days)


# ── Daily spend reader (delegates to canonical ledger) ──────────────


def daily_spend_by_provider(window_days: int = 7) -> dict[str, list[DailySpend]]:
    """Aggregate the last *window_days* of spend by (provider, UTC day).

    Delegates to :func:`app.llm_cost_ledger.daily_spend_by_provider_for_advisor`
    which performs the actual SQLite ``token_usage`` query (the
    canonical cost ledger).  Provider classification lives in
    :mod:`app.llm_provider_classify` so the rule is shared across all
    cost consumers.

    Returns ``{provider: [DailySpend, ...]}`` with one row per UTC
    day in the window — zero-spend days included as explicit zero
    rows so the proposer's "X of 7 days" counts are correct.
    """
    try:
        from app.llm_cost_ledger import daily_spend_by_provider_for_advisor
        raw = daily_spend_by_provider_for_advisor(window_days)
    except Exception:
        logger.debug(
            "llm_cost_advisor.analyzer: ledger read failed", exc_info=True,
        )
        return {"anthropic": [], "openrouter": [], "ollama": []}

    return {
        provider: [
            DailySpend(
                provider=provider,
                day=row["day"],
                spend_usd=row["spend_usd"],
                n_calls=row["n_calls"],
            )
            for row in rows
        ]
        for provider, rows in raw.items()
    }


# ── Per-role observation ────────────────────────────────────────────


@dataclass(frozen=True)
class RoleObservation:
    """Aggregate over the rolling-24h window for one role.

    ``profile_budget_usd`` is the per-role default from
    :data:`app.llm_role_spend._ROLE_PROFILES` — the baseline the
    proposer compares actual usage against.
    """
    role: str
    spend_usd_24h: float
    profile_budget_usd: float
    profile_expected_hourly_usd: float


def analyze_role_budgets(hours: float = 24.0) -> list[RoleObservation]:
    """One :class:`RoleObservation` per role with non-zero spend.

    Roles defined in :data:`app.llm_role_spend._ROLE_PROFILES` ALWAYS
    get an observation (even if zero-spend) so the proposer can
    detect "this role is provisioned for spend but never used".
    Unknown roles in the ledger (rows tagged with a role not in the
    profile table) ARE included with the fallback profile so the
    advisor can propose adding them.
    """
    try:
        from app.llm_cost_ledger import spend_by_role
        from app.llm_role_spend import (
            _ROLE_PROFILES, _FALLBACK_PROFILE,
        )
    except Exception:
        logger.debug(
            "llm_cost_advisor.analyzer: role-spend imports failed",
            exc_info=True,
        )
        return []

    spend = spend_by_role(hours=hours)
    observations: list[RoleObservation] = []
    seen: set[str] = set()
    for role, profile in _ROLE_PROFILES.items():
        observations.append(RoleObservation(
            role=role,
            spend_usd_24h=float(spend.get(role, 0.0)),
            profile_budget_usd=profile.budget_usd,
            profile_expected_hourly_usd=profile.expected_hourly_usd,
        ))
        seen.add(role)
    # Roles in the ledger but not in the profile table — operator
    # has been using a role that isn't tuned.  Surface with fallback
    # profile so the proposer can propose adding it.
    for role, usd in spend.items():
        if role in seen or role.startswith("__"):
            continue
        observations.append(RoleObservation(
            role=role,
            spend_usd_24h=float(usd),
            profile_budget_usd=_FALLBACK_PROFILE.budget_usd,
            profile_expected_hourly_usd=_FALLBACK_PROFILE.expected_hourly_usd,
        ))
    return observations


def _read_cap(provider: str) -> Optional[float]:
    """Read the current operator-set daily cap for *provider*."""
    try:
        if provider == "anthropic":
            from app.llm_anthropic_budget import get_cap
        elif provider == "openrouter":
            from app.llm_openrouter_budget import get_cap
        else:
            return None
        return get_cap()
    except Exception:
        return None


def analyze_provider_caps(window_days: int = 7) -> list[ProviderObservation]:
    """Produce a :class:`ProviderObservation` per paid provider.

    Ollama is excluded (zero-cost; no cap to advise on).  Providers
    with no spend AND no cap configured are excluded — there's nothing
    to advise about.
    """
    daily = daily_spend_by_provider(window_days)
    observations: list[ProviderObservation] = []
    for provider in ("anthropic", "openrouter"):
        days = tuple(daily.get(provider, []))
        cap = _read_cap(provider)
        total = sum(d.spend_usd for d in days)

        # Skip when there's nothing to advise.
        if cap is None and total == 0.0:
            continue

        max_day = max((d.spend_usd for d in days), default=0.0)
        mean_day = (total / len(days)) if days else 0.0
        n_at_cap = 0
        n_under_quarter = 0
        if cap is not None and cap > 0:
            for d in days:
                if d.spend_usd >= cap:
                    n_at_cap += 1
                if d.spend_usd < cap * 0.25:
                    n_under_quarter += 1

        observations.append(
            ProviderObservation(
                provider=provider,
                cap_usd=cap,
                days=days,
                max_day_spend_usd=round(max_day, 6),
                mean_day_spend_usd=round(mean_day, 6),
                n_days_at_or_over_cap=n_at_cap,
                n_days_below_25pct_of_cap=n_under_quarter,
            )
        )
    return observations
