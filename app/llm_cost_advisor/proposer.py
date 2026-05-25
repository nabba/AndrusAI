"""Cost-advisor proposer — turns observations into CRs.

Reads :class:`ProviderObservation` rows from the analyser, decides
which (if any) warrant a cap-adjustment proposal, and stages the
proposal via :mod:`app.proposal_bridge`.

Proposal rules (one CR per observation that triggers):

  * RAISE: cap hit on ≥3 of 7 days → propose raise by 25%.
    Rationale: repeated cap hits mean the operator's cap is below
    typical legitimate usage; the silent OR failover (for Anthropic)
    is shifting cost off-ledger.  Better to make the cap honest.

  * LOWER: cap > 0 AND max day < 25% of cap on ≥6 of 7 days →
    propose lower by 50%.  Rationale: cap exists but is wildly
    over-sized; tightening it gives the brake earlier warning of
    spend spikes.

  * SET: cap is unset AND mean daily spend > $1/day → propose
    setting a cap at 2× max-day-spend.  Rationale: a provider with
    real spend and no cap is a no-protection scenario; we don't
    set a tight cap (operator's burst usage might exceed mean) but
    DO put a ceiling in place.

The 50%-down / 25%-up asymmetry is deliberate: lowering signals
"you've over-budgeted" which is operationally safe (the cap can
always be raised back).  Raising signals "we keep hitting it" which
is more sensitive — a smaller step gives the operator more agency.
"""
from __future__ import annotations

import logging
from typing import Optional

from .analyzer import (
    ProviderObservation, RoleObservation,
    analyze_provider_caps, analyze_role_budgets,
)

logger = logging.getLogger(__name__)


# ── Proposal decisions ─────────────────────────────────────────────


def _load_thresholds() -> dict[str, float]:
    """Read all operator-tunable thresholds via explicit imports.

    The runtime_settings getters each have a documented default that
    matches the historical hardcoded value — behaviour is unchanged
    on a fresh deploy.  Failure-OPEN: any import / getter error
    returns the hardcoded defaults (the static-import path is
    preferred over reflective ``getattr(..., name)`` lookups so a
    typo in a threshold name fails at lint time, not silently at
    runtime).
    """
    defaults = {
        "set_min_daily": 1.0,
        "set_factor": 2.0,
        "raise_n_days": 3,
        "raise_factor": 1.25,
        "lower_n_days": 6,
        "lower_factor": 0.5,
        "lower_min_7d_spend": 0.50,
    }
    try:
        from app.runtime_settings import (
            get_cost_advisor_set_min_daily_usd,
            get_cost_advisor_set_factor,
            get_cost_advisor_raise_n_days,
            get_cost_advisor_raise_factor,
            get_cost_advisor_lower_n_days,
            get_cost_advisor_lower_factor,
            get_cost_advisor_lower_min_7d_spend_usd,
        )
        return {
            "set_min_daily":   float(get_cost_advisor_set_min_daily_usd()),
            "set_factor":      float(get_cost_advisor_set_factor()),
            "raise_n_days":    int(get_cost_advisor_raise_n_days()),
            "raise_factor":    float(get_cost_advisor_raise_factor()),
            "lower_n_days":    int(get_cost_advisor_lower_n_days()),
            "lower_factor":    float(get_cost_advisor_lower_factor()),
            "lower_min_7d_spend": float(get_cost_advisor_lower_min_7d_spend_usd()),
        }
    except Exception:
        return defaults


def _decide_adjustment(obs: ProviderObservation) -> Optional[dict]:
    """Return ``{"action", "new_cap_usd", "rationale"}`` or ``None``.

    Pure function — no I/O.  Test directly with synthetic
    observations.  Thresholds are read from runtime_settings via
    :func:`_load_thresholds` so operators can tune without code
    edits; defaults match the original hardcoded values.
    """
    thresh = _load_thresholds()
    set_min_daily = thresh["set_min_daily"]
    set_factor = thresh["set_factor"]
    raise_n_days = int(thresh["raise_n_days"])
    raise_factor = thresh["raise_factor"]
    lower_n_days = int(thresh["lower_n_days"])
    lower_factor = thresh["lower_factor"]

    if obs.cap_usd is None or obs.cap_usd <= 0:
        # SET case — no cap; non-zero spend warrants a ceiling.
        if obs.mean_day_spend_usd > set_min_daily:
            return {
                "action": "set",
                "new_cap_usd": round(obs.max_day_spend_usd * set_factor, 2),
                "rationale": (
                    f"{obs.provider} spend is averaging "
                    f"${obs.mean_day_spend_usd:.2f}/day (max-day "
                    f"${obs.max_day_spend_usd:.2f}) with no operator-"
                    f"set cap.  Proposing a cap at {set_factor:.0f}× "
                    f"max-day (${round(obs.max_day_spend_usd * set_factor, 2):.2f}) "
                    f"so accidental cost spikes have a ceiling."
                ),
            }
        return None

    # Existing cap — decide RAISE / LOWER / no-op.
    if obs.n_days_at_or_over_cap >= raise_n_days:
        return {
            "action": "raise",
            "new_cap_usd": round(obs.cap_usd * raise_factor, 2),
            "rationale": (
                f"{obs.provider} daily cap (${obs.cap_usd:.2f}) was "
                f"hit on {obs.n_days_at_or_over_cap} of the last 7 days. "
                f"Mean utilisation ${obs.mean_day_spend_usd:.2f}/day "
                f"({obs.mean_day_spend_usd / obs.cap_usd * 100:.0f}% of cap). "
                f"Repeated hits suggest the cap is below typical "
                f"legitimate usage; the silent failover shifts cost "
                f"off-ledger rather than reducing it.  Proposing a "
                f"{int((raise_factor - 1.0) * 100)}% raise to match observed usage."
            ),
        }

    if obs.n_days_below_25pct_of_cap >= lower_n_days:
        # Min-activity floor — don't propose lowering when there's
        # not enough spend signal to justify the recommendation.
        # During the post-§9 migration window OR for legitimately
        # idle providers the under-25%-utilisation condition would
        # trigger for ANY low spend, generating false positives.
        # Default $0.50 over 7 days = ~$0.07/day average (tiny, but
        # enough that a few real calls have hit the provider).
        lower_min_spend = thresh["lower_min_7d_spend"]
        if obs.total_spend_usd < lower_min_spend:
            logger.debug(
                "llm_cost_advisor: skipping LOWER for %s — 7d spend "
                "$%.4f below floor $%.4f",
                obs.provider, obs.total_spend_usd, lower_min_spend,
            )
            return None
        return {
            "action": "lower",
            "new_cap_usd": round(obs.cap_usd * lower_factor, 2),
            "rationale": (
                f"{obs.provider} daily cap (${obs.cap_usd:.2f}) was "
                f"below 25% utilisation on {obs.n_days_below_25pct_of_cap} "
                f"of the last 7 days (mean "
                f"${obs.mean_day_spend_usd:.2f}/day, max "
                f"${obs.max_day_spend_usd:.2f}, total "
                f"${obs.total_spend_usd:.2f} over 7 days).  Cap is "
                f"wildly over-sized; tightening by "
                f"{int((1.0 - lower_factor) * 100)}% gives earlier "
                f"warning of spend spikes while staying generously "
                f"above observed max."
            ),
        }
    return None


# ── Proposal body composition ──────────────────────────────────────


def _compose_proposal_body(
    obs: ProviderObservation,
    decision: dict,
) -> str:
    daily_lines = "\n".join(
        f"  * {d.day}: ${d.spend_usd:7.4f} ({d.n_calls} calls)"
        for d in obs.days
    )
    return (
        "---\n"
        f"action: adjust_daily_cap\n"
        f"provider: {obs.provider}\n"
        f"current_cap_usd: {obs.cap_usd}\n"
        f"proposed_cap_usd: {decision['new_cap_usd']}\n"
        f"adjustment: {decision['action']}\n"
        "---\n\n"
        f"# Cost-advisor: {decision['action']} {obs.provider} daily cap\n\n"
        f"**Recommendation:** {decision['rationale']}\n\n"
        f"## Last 7 days\n\n"
        f"{daily_lines}\n\n"
        f"**Summary:** mean ${obs.mean_day_spend_usd:.4f}/day, "
        f"max ${obs.max_day_spend_usd:.4f}/day, "
        f"total ${obs.total_spend_usd:.4f} over 7 days.\n\n"
        f"## Operator action\n\n"
    ) + (
        f"If you approve: set `{obs.provider}_daily_cap_usd = "
        f"{decision['new_cap_usd']}` in `/cp/settings`.  If you reject: "
        "the 7-day dedup window starts fresh and the advisor will not "
        "propose the same adjustment again until next week's pass.\n\n"
        f"Filed automatically by `app/llm_cost_advisor/proposer.py`.\n"
    )


# ── Public surface ─────────────────────────────────────────────────


def propose_adjustments(
    observations: list[ProviderObservation],
) -> list[dict]:
    """Stage one proposal per observation that warrants one.

    Returns a list of dicts ``{"provider", "action", "new_cap_usd"}``
    for the proposals that were staged.  Observations that don't
    trigger any rule produce no proposal — no spam.

    Idempotent via :func:`app.proposal_bridge.stage` — re-staging
    the same body for the same signature within the cooldown
    window is a no-op.
    """
    staged: list[dict] = []
    try:
        from app.proposal_bridge.store import stage as stage_proposal
    except Exception:
        logger.warning(
            "llm_cost_advisor: proposal_bridge import failed — "
            "advisor will run but no CRs will be staged",
            exc_info=True,
        )
        stage_proposal = None  # type: ignore

    for obs in observations:
        decision = _decide_adjustment(obs)
        if decision is None:
            continue

        body = _compose_proposal_body(obs, decision)
        # Signature uniquely identifies (provider, action) so the
        # operator only sees one "raise anthropic cap" CR per 7-day
        # cooldown, not one per advisor run.
        signature = f"{obs.provider}__{decision['action']}"
        target_path = f"docs/proposed_cap_adjustments/{signature}.md"

        if stage_proposal is None:
            logger.info(
                "llm_cost_advisor: would have staged %s/%s → $%.2f "
                "(proposal_bridge unavailable)",
                obs.provider, decision["action"], decision["new_cap_usd"],
            )
            continue

        try:
            stage_proposal(
                source="llm_cost_advisor",
                signature=signature,
                title=(
                    f"Cap advisor: {decision['action']} {obs.provider} "
                    f"daily cap → ${decision['new_cap_usd']:.2f}"
                ),
                body_markdown=body,
                target_path=target_path,
                cooldown_days=7,
            )
            staged.append({
                "provider": obs.provider,
                "action": decision["action"],
                "new_cap_usd": decision["new_cap_usd"],
            })
            logger.info(
                "llm_cost_advisor: staged %s/%s → $%.2f",
                obs.provider, decision["action"], decision["new_cap_usd"],
            )
        except Exception:
            logger.warning(
                "llm_cost_advisor: stage failed for %s/%s — skipping",
                obs.provider, decision["action"], exc_info=True,
            )

    return staged


# ── Per-role decision rule ──────────────────────────────────────────


def _decide_role_adjustment(obs: RoleObservation) -> Optional[dict]:
    """Per-role proposal — recommends adjusting the role's profile
    when actual usage diverges materially from the expected hourly
    baseline over a rolling 24h window.

    Triggers:
      * 24h spend > 4× (expected_hourly × 24)  → propose raise of
        ``expected_hourly`` by 2× (the baseline is undersized).
      * 24h spend < 0.1× (expected_hourly × 24) AND expected > 0.01
        → propose lower of ``expected_hourly`` by 0.5× (over-provisioned).

    The 4× / 0.1× boundaries are wide on purpose: this is per-role
    tuning, not per-call gating; a small handful of busy hours
    shouldn't move the profile.  Stable divergence over a full day
    is the signal.
    """
    expected_24h = obs.profile_expected_hourly_usd * 24.0
    if expected_24h <= 0:
        return None
    actual = obs.spend_usd_24h
    if actual > 4.0 * expected_24h:
        new_hourly = obs.profile_expected_hourly_usd * 2.0
        return {
            "action": "raise_expected_hourly",
            "new_expected_hourly_usd": round(new_hourly, 4),
            "rationale": (
                f"Role {obs.role!r} consumed ${actual:.2f} in the last "
                f"24h vs an expected baseline of ~${expected_24h:.2f}. "
                f"Adaptive back-pressure has been tightening this role's "
                f"per-call budget on every call ({actual / expected_24h:.1f}× "
                f"over pace).  If this usage level is legitimate, the "
                f"baseline is undersized — proposing raise to "
                f"${new_hourly:.4f}/h (2× current).  If the usage is a "
                f"runaway / regression, reject this CR and the back-"
                f"pressure will keep tightening."
            ),
        }
    if actual < 0.1 * expected_24h and obs.profile_expected_hourly_usd > 0.01:
        # Min-activity floor — sporadic roles (run once a day or
        # less) will ALWAYS look under-pace because the expected
        # baseline assumes 24/7 usage.  Without this guard the
        # advisor would propose lowering every legitimately-low-
        # traffic role's baseline.  Skip when 24h spend is below
        # the minimum threshold OR when actual is exactly zero (no
        # signal at all — could be migration window or genuine
        # idleness; in either case proposing changes is premature).
        try:
            from app.runtime_settings import (
                get_cost_advisor_role_lower_min_24h_spend_usd as _g,
            )
            role_lower_min = float(_g())
        except Exception:
            role_lower_min = 0.10
        if actual < role_lower_min:
            logger.debug(
                "llm_cost_advisor: skipping role LOWER for %r — "
                "24h spend $%.4f below min-activity floor $%.4f",
                obs.role, actual, role_lower_min,
            )
            return None
        new_hourly = obs.profile_expected_hourly_usd * 0.5
        return {
            "action": "lower_expected_hourly",
            "new_expected_hourly_usd": round(new_hourly, 4),
            "rationale": (
                f"Role {obs.role!r} consumed ${actual:.4f} in the last "
                f"24h vs an expected baseline of ~${expected_24h:.2f}. "
                f"The baseline is far oversized — the adaptive factor "
                f"will never tighten this role.  Proposing lower to "
                f"${new_hourly:.4f}/h (0.5× current) so the back-pressure "
                f"has actual room to fire when usage spikes."
            ),
        }
    return None


def _compose_role_proposal_body(
    obs: RoleObservation, decision: dict,
) -> str:
    return (
        "---\n"
        f"action: adjust_role_baseline\n"
        f"role: {obs.role}\n"
        f"current_expected_hourly_usd: {obs.profile_expected_hourly_usd}\n"
        f"proposed_expected_hourly_usd: {decision['new_expected_hourly_usd']}\n"
        f"adjustment: {decision['action']}\n"
        "---\n\n"
        f"# Cost-advisor: {decision['action']} for role {obs.role!r}\n\n"
        f"**Recommendation:** {decision['rationale']}\n\n"
        f"## Observed (24h)\n\n"
        f"  * Actual spend: ${obs.spend_usd_24h:.4f}\n"
        f"  * Profile budget per call: ${obs.profile_budget_usd:.4f}\n"
        f"  * Profile expected hourly: ${obs.profile_expected_hourly_usd:.4f} "
        f"(= ${obs.profile_expected_hourly_usd * 24:.2f}/day baseline)\n\n"
        f"## Operator action\n\n"
        f"Edit ``_ROLE_PROFILES[{obs.role!r}].expected_hourly_usd`` in "
        f"``app/llm_role_spend.py`` to ${decision['new_expected_hourly_usd']:.4f}. "
        f"Rejecting this CR lets the adaptive back-pressure continue with "
        f"the current baseline; the advisor will re-propose if the "
        f"divergence persists.\n\n"
        f"Filed automatically by `app/llm_cost_advisor/proposer.py`.\n"
    )


def propose_role_adjustments(
    observations: list[RoleObservation],
) -> list[dict]:
    """Sibling to :func:`propose_adjustments` for per-role baselines.

    Rejection-backoff is provided by ``proposal_bridge`` —
    REJECTED proposals stay terminal and re-staging the same
    signature is a silent no-op until the operator manually
    reopens.  No explicit backoff code needed here.
    """
    staged: list[dict] = []
    try:
        from app.proposal_bridge.store import stage as stage_proposal
    except Exception:
        logger.warning(
            "llm_cost_advisor: proposal_bridge import failed (role path)",
            exc_info=True,
        )
        return staged

    for obs in observations:
        decision = _decide_role_adjustment(obs)
        if decision is None:
            continue
        body = _compose_role_proposal_body(obs, decision)
        # Role names may contain dashes / underscores; the signature
        # validator (``_SAFE_SIG_RE``) accepts ``[A-Za-z0-9_.-]+``
        # so the raw role + action joins cleanly.
        signature = f"role__{obs.role}__{decision['action']}"
        target_path = f"docs/proposed_cap_adjustments/{signature}.md"
        try:
            stage_proposal(
                source="llm_cost_advisor",
                signature=signature,
                title=(
                    f"Cap advisor: {decision['action']} for role "
                    f"{obs.role!r} → ${decision['new_expected_hourly_usd']:.4f}/h"
                ),
                body_markdown=body,
                target_path=target_path,
                cooldown_days=7,
            )
            staged.append({
                "role": obs.role,
                "action": decision["action"],
                "new_expected_hourly_usd": decision["new_expected_hourly_usd"],
            })
        except Exception:
            logger.warning(
                "llm_cost_advisor: role-stage failed for %s/%s",
                obs.role, decision["action"], exc_info=True,
            )

    return staged


def run() -> list[dict]:
    """Entry point — invoked by the idle scheduler weekly.

    Runs BOTH provider-cap analysis and role-baseline analysis;
    returns the combined list of staged proposals for telemetry.
    Rejection-backoff is implicit via ``proposal_bridge``'s
    terminal-state guard — REJECTED proposals stay rejected, the
    next weekly pass silently skips re-staging the same signature.
    """
    provider_obs = analyze_provider_caps(window_days=7)
    role_obs = analyze_role_budgets(hours=24.0)
    return (
        propose_adjustments(provider_obs)
        + propose_role_adjustments(role_obs)
    )
