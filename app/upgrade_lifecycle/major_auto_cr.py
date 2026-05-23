"""U4 — MAJOR auto-CR gate.

PROGRAM §63. Composes U1 (capability extraction), U2 (impact analysis),
and U3 (trial harness) into the five-condition gate that decides
whether a MAJOR version bump goes through the standard CR review path
instead of falling through to Signal-only.

Five-condition gate (ALL must pass; fail-closed):

  1. U3 trial returned ``ok``
  2. PyPI ``upload_time`` for the new version is ≥ 30 d ago
  3. U2 ``breaking_hits == 0``
  4. U2 ``tier_immutable_touched is False``
  5. Package not in ``FRAMEWORK_PACKAGES`` (defined in
     :mod:`app.upgrade_lifecycle.changelog_fetcher`)

When the gate passes the function files a CR via
:mod:`app.proposal_bridge` with a 14-day cooldown (matching MINOR
cadence). When the gate fails, the caller is expected to fall back to
the existing Signal-only behavior — this module returns
:class:`GateOutcome` carrying the reason so the caller can include it
in the alert body if useful.

LLM-free at the gate itself — the gate is pure boolean logic over
upstream signals. Heavy lifting (capability extraction + trial run)
happens BEFORE the gate, so the gate's decision can be fast.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from app.upgrade_lifecycle.changelog_fetcher import FRAMEWORK_PACKAGES
from app.upgrade_lifecycle.protocol import Capability, ImpactReport, TrialResult

logger = logging.getLogger(__name__)


_POST_RELEASE_WINDOW_DAYS = 30
_AUTO_CR_COOLDOWN_DAYS = 14
_PROPOSAL_SOURCE = "dependency_radar"


@dataclass
class GateOutcome:
    """Why the gate did (or did not) authorize an auto-CR.

    Attributes:
      passed: True iff all five conditions hold.
      reason: short machine-readable label for the FIRST failing
        condition; "ok" when ``passed``.
      details: free-form dict describing each condition's input —
        included in the CR body when ``passed`` so the operator sees
        the evidence chain at a glance.
    """

    passed: bool
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


def _enabled() -> bool:
    try:
        from app.runtime_settings import get_upgrade_lifecycle_major_auto_cr_enabled
        return get_upgrade_lifecycle_major_auto_cr_enabled()
    except Exception:
        return True


# ── Condition primitives (each one cheap + injectable for tests) ─────────


def _condition_master_switch(*, now: datetime, override: Optional[bool] = None) -> bool:
    """Top condition — master switch + parent subsystem switch."""
    if override is not None:
        return bool(override)
    return _enabled()


def _condition_framework_exclusion(package: str) -> tuple[bool, str]:
    """Refuse framework-level packages. Returns ``(passed, reason)``."""
    norm = package.lower().replace("_", "-")
    if norm in FRAMEWORK_PACKAGES:
        return False, f"framework_exclusion:{norm}"
    return True, ""


def _condition_post_release_window(
    *,
    pypi_metadata: Optional[dict[str, Any]],
    to_version: str,
    now: datetime,
    min_days: int = _POST_RELEASE_WINDOW_DAYS,
) -> tuple[bool, str, Optional[int]]:
    """Latest version must have been released at least *min_days* ago.

    Returns ``(passed, reason, days_since_release)``. When the upload
    time can't be parsed we fail closed (returns ``False, ...``,
    ``None``) so the gate doesn't accidentally fire on misparsed data.
    """
    if not pypi_metadata:
        return False, "no_pypi_metadata", None
    releases = pypi_metadata.get("releases") or {}
    artifacts = releases.get(to_version) or releases.get(to_version.lstrip("vV")) or []
    if not artifacts:
        return False, "version_not_in_pypi_releases", None
    times: list[datetime] = []
    for art in artifacts:
        raw = art.get("upload_time") or art.get("upload_time_iso_8601")
        if not raw:
            continue
        try:
            # PyPI returns naive ISO format like "2025-02-01T11:22:33"
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            times.append(dt)
        except ValueError:
            continue
    if not times:
        return False, "upload_time_unparseable", None
    earliest = min(times)
    age = now - earliest
    days = int(age.total_seconds() // 86400)
    if days < min_days:
        return False, f"post_release_too_short:{days}d", days
    return True, "", days


def _condition_trial(trial: Optional[TrialResult]) -> tuple[bool, str]:
    if trial is None:
        return False, "trial_not_run"
    if trial.status != "ok":
        return False, f"trial_status:{trial.status}"
    return True, ""


def _condition_impact(impact: Optional[ImpactReport]) -> tuple[bool, str]:
    if impact is None:
        return False, "impact_not_run"
    if impact.tier_immutable_touched:
        return False, "tier_immutable_touched"
    if impact.breaking_hits > 0:
        return False, f"breaking_hits:{impact.breaking_hits}"
    return True, ""


# ── Public API ───────────────────────────────────────────────────────────


def evaluate_gate(
    *,
    package: str,
    from_version: str,
    to_version: str,
    capability: Optional[Capability],
    impact: Optional[ImpactReport],
    trial: Optional[TrialResult],
    pypi_metadata: Optional[dict[str, Any]],
    now: Optional[datetime] = None,
    master_switch_override: Optional[bool] = None,
) -> GateOutcome:
    """Run the five-condition gate.

    All four upstream artefacts (capability, impact, trial,
    pypi_metadata) MUST be supplied for a passing gate; passing
    ``None`` for any of them is shorthand for "this stage didn't run"
    and trips the corresponding condition.

    ``now`` is injectable so tests can pin the clock against a
    fixture's release date.
    """
    now_dt = now or datetime.now(timezone.utc)

    if not _condition_master_switch(now=now_dt, override=master_switch_override):
        return GateOutcome(passed=False, reason="master_switch_off")

    ok_fw, reason_fw = _condition_framework_exclusion(package)
    if not ok_fw:
        return GateOutcome(passed=False, reason=reason_fw)

    # U9 — consult Goodhart throttle for the dynamic post-release window.
    # Defaults to the constant if the throttle state file is missing.
    try:
        from app.upgrade_lifecycle.goodhart import current_major_window
        window = current_major_window()
    except Exception:
        window = _POST_RELEASE_WINDOW_DAYS

    ok_release, reason_release, days_since = _condition_post_release_window(
        pypi_metadata=pypi_metadata, to_version=to_version, now=now_dt,
        min_days=window,
    )
    if not ok_release:
        return GateOutcome(passed=False, reason=reason_release,
                          details={"days_since_release": days_since})

    ok_impact, reason_impact = _condition_impact(impact)
    if not ok_impact:
        details = {}
        if impact is not None:
            details["impact"] = impact.to_dict()
        return GateOutcome(passed=False, reason=reason_impact, details=details)

    ok_trial, reason_trial = _condition_trial(trial)
    if not ok_trial:
        details = {}
        if trial is not None:
            details["trial"] = trial.to_dict()
        return GateOutcome(passed=False, reason=reason_trial, details=details)

    # All five conditions pass — assemble evidence for the CR body.
    return GateOutcome(
        passed=True,
        reason="ok",
        details={
            "days_since_release": days_since,
            "trial": trial.to_dict() if trial is not None else None,
            "impact": impact.to_dict() if impact is not None else None,
            "capability": capability.to_payload() if capability is not None else None,
        },
    )


# ── CR body composition + filing ─────────────────────────────────────────


def _format_capability_block(capability: Optional[Capability]) -> str:
    if capability is None:
        return ""
    parts: list[str] = []
    def _list(label: str, items: tuple[str, ...]) -> None:
        if not items:
            return
        parts.append(f"\n### {label}")
        for it in items:
            parts.append(f"- {it}")
    _list("New features", capability.new_features)
    _list("Deprecations", capability.deprecations)
    _list("Breaking changes", capability.breaking_changes)
    _list("Security fixes", capability.security_fixes)
    _list("Performance notes", capability.perf_notes)
    # P2#c — license-change surfacing. When the LLM flagged a
    # license change in the changelog, render it prominently so the
    # operator sees the legal/licensing risk before approving.
    if capability.license_change:
        parts.append(
            f"\n### ⚠️ License change\n"
            f"**{capability.license_change}** — review legal "
            f"implications before approving."
        )
    if capability.notes:
        parts.append(f"\n### Notes\n{capability.notes}")
    return "\n".join(parts)


def _format_impact_block(impact: Optional[ImpactReport]) -> str:
    if impact is None or not impact.call_sites:
        return "\n### Impact\nNo call sites match this capability."
    lines = ["\n### Impact"]
    lines.append(
        f"- {impact.deprecation_hits} deprecation hit(s), "
        f"{impact.breaking_hits} breaking-change hit(s) across "
        f"{len(impact.call_sites)} call site(s)."
    )
    # Show top 10 sites; the full list is in the impact report payload.
    for site in impact.call_sites[:10]:
        lines.append(
            f"- `{site.file_path}:{site.line}` — `{site.symbol}` "
            f"({site.kind}; matched `{site.matched_capability}`)"
        )
    if len(impact.call_sites) > 10:
        lines.append(f"- … and {len(impact.call_sites) - 10} more")
    return "\n".join(lines)


def _format_trial_block(trial: Optional[TrialResult]) -> str:
    if trial is None:
        return "\n### Trial\nTrial was not run."
    lines = ["\n### Trial"]
    lines.append(f"- Status: **{trial.status}**")
    lines.append(f"- Tests: {trial.pass_count} passed, {trial.fail_count} failed")
    lines.append(f"- Elapsed: {trial.elapsed_s:.1f}s")
    return "\n".join(lines)


def _front_matter(
    *,
    package: str, from_version: str, to_version: str,
) -> str:
    """Structured YAML the apply_hook parses to drive requirements_writer.

    The block is intentionally minimal — apply_hook treats unknown
    keys as undefined behavior. ``action: bump_requirement`` is the
    single recognised action today; future actions (e.g.
    ``upgrade_python``) would route through a different writer.
    """
    return (
        "---\n"
        f"action: bump_requirement\n"
        f"package: {package}\n"
        f"from_version: {from_version}\n"
        f"to_version: {to_version}\n"
        "---\n"
    )


def compose_cr_body(
    *,
    package: str,
    from_version: str,
    to_version: str,
    capability: Optional[Capability],
    impact: Optional[ImpactReport],
    trial: Optional[TrialResult],
    gate: GateOutcome,
    days_since_release: Optional[int] = None,
) -> str:
    """Build the markdown body the operator will see in /cp/changes.

    Starts with a YAML front-matter block consumed by the apply hook.
    Operator-facing markdown follows.
    """
    title = f"# Upgrade `{package}` {from_version} → {to_version} (MAJOR)"
    intro = (
        "\nThe upgrade-lifecycle pipeline (PROGRAM §63) auto-filed this "
        "CR because the five gate conditions for MAJOR bumps held:\n"
        "\n"
        f"- Trial passed ({trial.status if trial else 'n/a'}, "
        f"{trial.pass_count if trial else 0}p/"
        f"{trial.fail_count if trial else 0}f)\n"
        f"- {days_since_release or '?'}d since "
        f"{to_version} released on PyPI (≥{_POST_RELEASE_WINDOW_DAYS}d required)\n"
        f"- {impact.breaking_hits if impact else 0} breaking-change "
        f"call site(s) detected in our codebase (must be 0)\n"
        f"- {'No' if impact and not impact.tier_immutable_touched else 'TIER_IMMUTABLE'} "
        f"protected files touched\n"
        f"- `{package}` not in the framework exclusion list\n"
    )
    return (
        _front_matter(
            package=package, from_version=from_version, to_version=to_version,
        )
        + title
        + intro
        + _format_capability_block(capability)
        + _format_impact_block(impact)
        + _format_trial_block(trial)
    )


def _safe_signature(package: str, to_version: str) -> str:
    """Filesystem-safe signature usable as a docs/proposed_upgrades filename."""
    safe_pkg = package.lower().replace("-", "_").replace(".", "_")
    safe_ver = to_version.replace(".", "_").replace("-", "_")
    return f"upgrade_{safe_pkg}_{safe_ver}"


def file_major_auto_cr(
    *,
    package: str,
    from_version: str,
    to_version: str,
    capability: Optional[Capability],
    impact: Optional[ImpactReport],
    trial: Optional[TrialResult],
    pypi_metadata: Optional[dict[str, Any]],
    now: Optional[datetime] = None,
    stage_fn: Optional[Callable] = None,
) -> Optional[GateOutcome]:
    """Run the gate and, on pass, stage the CR via ``proposal_bridge``.

    Returns the :class:`GateOutcome` either way (None on hard error).
    Caller uses ``outcome.passed`` to decide whether to skip the
    Signal-only fall-through path.

    ``stage_fn`` is injectable for tests; defaults to
    ``app.proposal_bridge.store.stage``.
    """
    outcome = evaluate_gate(
        package=package, from_version=from_version, to_version=to_version,
        capability=capability, impact=impact, trial=trial,
        pypi_metadata=pypi_metadata, now=now,
    )
    if not outcome.passed:
        return outcome

    days_since = outcome.details.get("days_since_release")
    body = compose_cr_body(
        package=package, from_version=from_version, to_version=to_version,
        capability=capability, impact=impact, trial=trial,
        gate=outcome, days_since_release=days_since,
    )
    signature = _safe_signature(package, to_version)
    # Stage at docs/proposed_upgrades/<sig>.md — under validator's
    # allowed roots so the CR actually files. The body's YAML
    # front-matter carries the bump intent; the apply_hook reads it
    # post-approval and calls requirements_writer to do the real
    # mutation. requirements.txt is NEVER directly targeted by a CR.
    target_path = f"docs/proposed_upgrades/{signature}.md"

    try:
        if stage_fn is None:
            from app.proposal_bridge.store import stage as stage_fn  # type: ignore[assignment]
        stage_fn(   # type: ignore[misc]
            source=_PROPOSAL_SOURCE,
            signature=signature,
            title=f"Upgrade {package} {from_version} → {to_version} (MAJOR)",
            body_markdown=body,
            target_path=target_path,
            cooldown_days=_AUTO_CR_COOLDOWN_DAYS,
        )
    except Exception:
        logger.debug("ul.u4: stage failed for %s", package, exc_info=True)
        return None
    return outcome
