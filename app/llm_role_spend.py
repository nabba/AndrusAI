"""Per-role rolling-window LLM spend + adaptive back-pressure.

Composes with — does not replace — the per-provider daily caps in
:mod:`app.llm_anthropic_budget` and :mod:`app.llm_openrouter_budget`.
Those are HARD ceilings (refuse new calls when breached); this module
is SOFT pressure (tighten the next call's per-role ``budget_usd`` so
the selector demotes to a cheaper alternative).

Why per-role?
-------------

A runaway brainstorm session calling 50× per minute on ``role=creative``
shouldn't slow the commander's per-message routing.  Per-provider
caps don't distinguish.  Per-role tracking + adaptive tightening lets
the costly role degrade itself without affecting other paths.

The mechanism is purely *observational*: the spend ledger is read from
the existing audit log via the ``agent_role`` ContextVar tag that
:func:`app.project_context.agent_scope` already places on every
LLM-bearing dispatch.  No new infrastructure, no new write site.

Adaptive contract
-----------------

:func:`adaptive_budget_factor` returns a multiplier in [0.25, 1.0]:

  * 1.0 — no tightening, role is at or below expected pace
  * 0.8 — mild tightening, role is 1–2× over pace
  * 0.5 — significant tightening, role is 2–4× over pace
  * 0.25 — aggressive tightening, role is >4× over pace

The factor is applied via :mod:`app.llm_factory._resolved_budget_usd`
when ``select_model`` is invoked.  It only TIGHTENS, never loosens —
a role under its expected pace gets the base budget, not a free pass
to spend more.  This is a deliberate asymmetry: under-pace is the
healthy default; over-pace deserves intervention.

Posture
-------

Failure-OPEN.  Any error reading the audit log degrades to
"factor=1.0" (no tightening).  A broken ledger must never starve
legitimate calls.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ── Per-role cost profile (single source of truth) ──────────────────
#
# Previously two parallel tables existed: ``_DEFAULT_BUDGET_USD_BY_ROLE``
# in llm_factory.py (per-call ceiling) and ``_EXPECTED_HOURLY_USD_BY_ROLE``
# in this module (adaptive baseline).  Both keyed by role; adding a
# new role required two edits in lockstep with inevitable drift.
# Consolidated into a single :class:`RoleCostProfile` dataclass — one
# place to add a role, one place to tune its cost envelope.


@dataclass(frozen=True)
class RoleCostProfile:
    """Per-role cost envelope: per-call ceiling + hourly baseline.

    ``budget_usd``
        Per-call USD ceiling fed to ``select_model(budget_usd=...)``.
        Engages the selector's Pareto demote when the default-estimated
        cost would exceed this.

    ``expected_hourly_usd``
        Adaptive back-pressure baseline.  When actual rolling-1h
        spend for the role exceeds ``ratio × expected_hourly_usd``
        (ratio thresholds in :func:`adaptive_budget_factor`), the
        factor tightens for the next call.
    """
    budget_usd: float
    expected_hourly_usd: float


# Per-role profiles.  Adding a new role: add one row here.  Removing:
# remove the row.  Both consumers (llm_factory._resolved_budget_usd
# and adaptive_budget_factor below) read from this single source.
_ROLE_PROFILES: dict[str, RoleCostProfile] = {
    "commander":     RoleCostProfile(budget_usd=0.05,  expected_hourly_usd=0.20),
    "vetting":       RoleCostProfile(budget_usd=0.05,  expected_hourly_usd=0.10),
    "cheap-vetting": RoleCostProfile(budget_usd=0.005, expected_hourly_usd=0.05),
    "research":      RoleCostProfile(budget_usd=0.50,  expected_hourly_usd=2.00),
    "coding":        RoleCostProfile(budget_usd=0.50,  expected_hourly_usd=2.00),
    "self_improve":  RoleCostProfile(budget_usd=0.50,  expected_hourly_usd=1.00),
    "writing":       RoleCostProfile(budget_usd=0.25,  expected_hourly_usd=1.00),
    "creative":      RoleCostProfile(budget_usd=0.25,  expected_hourly_usd=1.00),
    "media":         RoleCostProfile(budget_usd=0.25,  expected_hourly_usd=0.50),
}

# Fallback profile when a role isn't in the table.  Sentinel — not a
# magic dict key, so a role literally named "default" doesn't shadow.
_FALLBACK_PROFILE: RoleCostProfile = RoleCostProfile(
    budget_usd=0.20, expected_hourly_usd=0.50,
)


def profile_for(role: str) -> RoleCostProfile:
    """Return the per-role cost envelope.  Falls back to a generic
    profile for unknown roles.  Single read-site shared by the factory's
    ``_resolved_budget_usd`` and this module's adaptive logic.
    """
    return _ROLE_PROFILES.get(role, _FALLBACK_PROFILE)


def all_role_profiles() -> dict[str, RoleCostProfile]:
    """Operator-facing view of the role table — a plain copy of the
    explicit per-role profiles.

    The fallback profile is NOT included here.  Callers that need it
    use :data:`_FALLBACK_PROFILE` directly or call
    :func:`profile_for` with the unknown role.  Mixing the fallback
    into a dict keyed by role would surprise consumers iterating
    over real roles.
    """
    return dict(_ROLE_PROFILES)


# ── Reads now delegate to llm_cost_ledger ───────────────────────────
#
# Previously this module read JSONL from a non-existent ``app.audit_log``
# (broken since creation — silent failure made the adaptive factor a
# no-op).  Reads now route through :mod:`app.llm_cost_ledger`, which
# queries the canonical SQLite ``token_usage`` table.  The cache lives
# in the ledger (one TTL across all cost consumers).


def _invalidate_for_tests() -> None:
    """Test-only — wipes the canonical ledger's TTL cache."""
    try:
        from app.llm_cost_ledger import _invalidate_for_tests as _inv
        _inv()
    except Exception:
        pass


# ── Public reads ────────────────────────────────────────────────────


def spent_in_window(role: str, hours: float = 1.0) -> float:
    """Return rolling-window USD spend for *role*.

    Defaults to 1-hour window — the adaptive back-pressure operates
    on near-real-time pace, not daily totals.  Pass a larger window
    for dashboards / operator views.

    Failure-isolated: returns 0.0 on any error (broken ledger must
    never starve calls via the adaptive factor).
    """
    try:
        from app.llm_cost_ledger import spend_for_role
        return spend_for_role(role, hours=hours)
    except Exception:
        logger.debug(
            "llm_role_spend: ledger read failed for role=%r", role,
            exc_info=True,
        )
        return 0.0


def all_roles_summary(hours: float = 24.0) -> dict[str, float]:
    """``{role: usd}`` for the operator dashboard."""
    try:
        from app.llm_cost_ledger import spend_by_role
        return spend_by_role(hours=hours)
    except Exception:
        return {}


# ── Adaptive back-pressure ──────────────────────────────────────────
#
# Expected hourly pace per role.  Derived from observation: a busy
# The expected-hourly numbers are conservative — observed:
# commander runs ~10 calls/hour during a Signal-conversational
# evening at ~$0.01/call → ~$0.10/hour expected.  Heavy roles
# (research, coding) burst at higher rates.  The factor only
# tightens when actual pace exceeds expected by 1.5× or more.
# These live in ``_ROLE_PROFILES.expected_hourly_usd`` above so
# the per-role envelope is one structure, not two parallel tables.


def adaptive_budget_factor(role: str) -> float:
    """Return a multiplier in [0.25, 1.0] for per-call budget tightening.

    The factor is applied by :func:`app.llm_factory._resolved_budget_usd`
    when the selector picks the next call's model.  Tightening biases
    the selector's Pareto demotion toward cheaper alternatives.

    Strictly observational and non-loosening: a role under pace gets
    1.0 (base budget); a role over pace gets < 1.0 (tighter budget).

    Failure-OPEN — any error returns 1.0.
    """
    try:
        actual = spent_in_window(role, hours=1.0)
        expected = profile_for(role).expected_hourly_usd
        if expected <= 0:
            return 1.0
        ratio = actual / expected
    except Exception:
        logger.debug(
            "llm_role_spend: adaptive factor read failed for role=%r",
            role, exc_info=True,
        )
        return 1.0
    # Tightening ladder.  Boundaries chosen so a typical busy hour
    # (1.5× expected) doesn't trigger tightening, but a runaway
    # session (3× or 5×) is throttled progressively.
    if ratio < 1.5:
        return 1.0
    if ratio < 2.5:
        return 0.8
    if ratio < 4.0:
        return 0.5
    return 0.25


__all__ = [
    "RoleCostProfile",
    "profile_for",
    "all_role_profiles",
    "spent_in_window",
    "all_roles_summary",
    "adaptive_budget_factor",
]
