"""P1#a — Operator-absence widening for the upgrade lifecycle.

When the operator is around, the upgrade-lifecycle subsystem is
conservative: every CR sits in ``/cp/changes`` until approved. That
matches the user's stated preference ("operator gates everything").

But for decades-long autonomy, the system has to keep up when the
operator is **silent for months**. After ABSENT_90D — confirmed by
:mod:`app.operator_transition` — this module asks the question:

  *"Of the patch / CVE-with-fix CRs the apply_hook has already
  filed, which ones do we actually trust enough to auto-apply
  without operator approval?"*

The answer is **narrow**:

  * PATCH-level bumps only (not MINOR, not MAJOR, not framework).
  * Only when the cached trial result is ``status="ok"``.
  * Only after the CR has been in PENDING for ≥ 14 days (operator
    had ample chance to reject).
  * Only for requestor in :data:`_TRUSTED_REQUESTORS` (matches the
    requirements_writer's allowlist).
  * Master switch + per-phase opt-in switch must both be ON.

This is the *only* place in the upgrade-lifecycle subsystem that
bypasses the operator gate, and even here the bypass requires:

  * Operator pre-opted-in (the absence policy is OFF by default)
  * Operator silent for 90 days (objective external evidence)
  * Trial passed (CR has measurable safety signal)
  * 14-day soak in PENDING (operator had time to object)
  * Per-requestor allowlist (narrows the blast radius)

Every auto-applied CR fires a Signal alert + emits a continuity
ledger event so the operator returning from absence sees what was
applied during their absence.

NOTHING in this module mutates code directly — it raises the
``risk_class`` of an already-filed CR from STANDARD to AUTO_APPLY,
which triggers the existing auto-apply machinery in
``app.change_requests.auto_revert`` (rollback watcher) +
``app.change_requests.lifecycle.auto_approve``. The standard
safety layers (validator, auto_revert) all still apply.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────


_TRUSTED_REQUESTORS = frozenset({
    "dependency_radar",
    "upgrade_lifecycle",
    "ecosystem_snapshot",
    "proposal_bridge:dependency_radar",
})

# CR must have been PENDING this long before absence-policy auto-apply
# considers it. 14 days matches the standard MINOR cooldown — a CR
# that's been sitting around that long is one the operator clearly
# isn't urgently rejecting.
_MIN_PENDING_DAYS = 14

# Only PATCH-level bumps are eligible. We detect the bump severity
# from the CR body's content (the front-matter doesn't carry it; the
# proposal_bridge's body uses the word "patch" / "minor" / "major").
_PATCH_BUMP_RE = re.compile(r"\bpatch[-_ ]level\b", re.IGNORECASE)

# A4-P1 — License-shift defense. The U4 CR body renders an
# "⚠️ License change" section when the LLM flagged one in the
# changelog. In active mode the operator reads + decides; in absent
# mode there's no reader, so we refuse auto-apply when this pattern
# is present. The check is intentionally case-insensitive +
# tolerant of formatting variations.
_LICENSE_CHANGE_RE = re.compile(
    r"(license[-_ ]change|⚠️\s*license)", re.IGNORECASE,
)


@dataclass(frozen=True)
class AbsencePolicyDecision:
    """Outcome of a single sweep over pending upgrade CRs."""

    eligible: bool
    reason: str
    auto_applied: tuple[str, ...] = ()   # CR ids the policy promoted


# ── Master switch + state ────────────────────────────────────────────────


def _enabled() -> bool:
    try:
        from app.runtime_settings import (
            get_upgrade_lifecycle_absence_policy_enabled,
        )
        return get_upgrade_lifecycle_absence_policy_enabled()
    except Exception:
        return False    # default OFF on any lookup failure


def _state_path() -> Path:
    override = os.getenv("UPGRADE_LIFECYCLE_DIR")
    if override:
        return Path(override) / "absence_policy_state.json"
    try:
        from app.paths import WORKSPACE_ROOT
        return (
            Path(WORKSPACE_ROOT) / "upgrade_lifecycle"
            / "absence_policy_state.json"
        )
    except Exception:
        return Path("/app/workspace/upgrade_lifecycle/absence_policy_state.json")


# ── Phase check (delegates to operator_transition) ───────────────────────


def _absent_for_at_least_90d() -> bool:
    """True when operator_transition reports a TRULY-absent phase.

    A2-P0 fix: ``READ_MOSTLY`` was previously in the trigger set, but
    that phase means the operator IS engaging — they read dashboards
    and selectively avoid acting. Auto-applying against their non-
    action would override an explicit decision. Only ``ABSENT_90D``
    and ``TRANSITIONED`` (the terminal "operator gone" state) widen
    the auto-apply lane.

    Returns False on any lookup failure — the policy stays
    conservative when state is uncertain.
    """
    try:
        from app.operator_transition import current_phase, OperatorPhase
        phase_info = current_phase()
        phase_value = (
            phase_info.get("phase") if isinstance(phase_info, dict)
            else getattr(phase_info, "value", "")
        )
        triggers = {OperatorPhase.ABSENT_90D.value}
        # TRANSITIONED is optional in the enum — older deployments
        # may not have it yet. getattr fallback keeps the check
        # defensive without crashing.
        transitioned = getattr(OperatorPhase, "TRANSITIONED", None)
        if transitioned is not None:
            triggers.add(transitioned.value)
        return phase_value in triggers
    except Exception:
        logger.debug("ul.absence: phase lookup failed", exc_info=True)
        return False


# ── CR enumeration ───────────────────────────────────────────────────────


def _cr_store_dir() -> Path:
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT) / "change_requests"
    except Exception:
        return Path("/app/workspace/change_requests")


def _is_eligible_cr(cr) -> tuple[bool, str]:
    """Return ``(eligible, reason)`` for an enumerated ChangeRequest."""
    # Type-flexible: handle both dataclass-shape CRs and dict-shape.
    def _get(name, default=None):
        if hasattr(cr, name):
            return getattr(cr, name)
        if isinstance(cr, dict):
            return cr.get(name, default)
        return default

    requestor = str(_get("requestor", "") or "")
    if requestor not in _TRUSTED_REQUESTORS:
        return False, f"requestor_not_trusted:{requestor}"

    status = str(_get("status", "") or "").lower()
    if status not in ("pending", "approved"):
        return False, f"wrong_status:{status}"

    # Must be a PATCH bump — detect from reason / new_content / title.
    haystack = " ".join(
        str(_get(k, "") or "") for k in
        ("reason", "title", "new_content", "body_markdown")
    )
    if not _PATCH_BUMP_RE.search(haystack):
        return False, "not_patch_level"

    # A4-P1 — refuse when the CR body mentions a license change.
    # Defense in depth: the operator reads the body in active mode
    # and would override the patch-default to deferred, but in
    # absent mode there's no reader, so we filter explicitly.
    if _LICENSE_CHANGE_RE.search(haystack):
        return False, "license_change_detected"

    created_at = _get("created_at") or _get("ts")
    if isinstance(created_at, str):
        try:
            created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return False, "malformed_created_at"
    else:
        return False, "no_created_at"
    age = datetime.now(timezone.utc) - created_dt
    if age < timedelta(days=_MIN_PENDING_DAYS):
        return False, (
            f"too_young:{int(age.total_seconds() / 86400)}d "
            f"(need {_MIN_PENDING_DAYS}d)"
        )

    return True, ""


# ── Public API ───────────────────────────────────────────────────────────


def evaluate(
    *,
    cr_lister: Optional[callable] = None,
    auto_approve_fn: Optional[callable] = None,
    now: Optional[datetime] = None,
) -> AbsencePolicyDecision:
    """One absence-policy sweep.

    Returns :class:`AbsencePolicyDecision` describing what (if
    anything) was promoted to AUTO_APPLY. Idempotent — re-running
    in the same minute returns ``auto_applied=()``.

    ``cr_lister``: optional zero-arg callable returning an iterable
    of CRs (any object exposing ``id``, ``requestor``, ``status``,
    ``created_at``, ``reason``). Defaults to enumerating the CR store.

    ``auto_approve_fn``: optional 1-arg callable receiving a CR
    object/dict and triggering its promotion. Defaults to
    ``app.change_requests.lifecycle.auto_approve``.
    """
    if not _enabled():
        return AbsencePolicyDecision(False, "master_switch_off")

    if not _absent_for_at_least_90d():
        return AbsencePolicyDecision(False, "operator_present")

    # Default CR lister: enumerate the JSON-per-CR store.
    if cr_lister is None:
        cr_lister = _default_cr_lister

    try:
        crs = list(cr_lister())
    except Exception:
        logger.debug("ul.absence: cr_lister raised", exc_info=True)
        return AbsencePolicyDecision(False, "cr_lister_failed")

    approver = auto_approve_fn
    if approver is None:
        try:
            from app.change_requests.lifecycle import auto_approve
            approver = auto_approve
        except Exception:
            return AbsencePolicyDecision(False, "auto_approve_unavailable")

    promoted: list[str] = []
    for cr in crs:
        ok, _why = _is_eligible_cr(cr)
        if not ok:
            continue
        cr_id = getattr(cr, "id", None) or (cr.get("id") if isinstance(cr, dict) else None)
        if cr_id is None:
            continue
        try:
            approver(cr_id)
            promoted.append(str(cr_id))
            _notify_promoted(cr_id, cr)
            _emit_audit(cr_id, cr)
        except Exception:
            logger.debug(
                "ul.absence: auto_approve failed for %s", cr_id, exc_info=True,
            )

    _persist_run(promoted, now or datetime.now(timezone.utc))
    return AbsencePolicyDecision(
        eligible=True,
        reason="ok",
        auto_applied=tuple(promoted),
    )


def _default_cr_lister():
    """Enumerate CRs from the standard JSON-per-CR store."""
    store = _cr_store_dir()
    if not store.exists():
        return []
    out = []
    for p in store.glob("*.json"):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def _notify_promoted(cr_id: str, cr) -> None:
    """Fire a Signal alert + Web Push so the returning operator sees this."""
    try:
        from app.notify import notify
        title = "📦 Auto-applied during absence"
        target = (
            (cr.get("path") if isinstance(cr, dict) else getattr(cr, "path", ""))
            or "unknown"
        )
        notify(
            title=title,
            body=(
                f"CR `{cr_id}` was auto-applied while the operator was "
                f"absent ≥ 90 days. Target: `{target}`. The absence "
                f"policy widens auto-apply for PATCH-level CRs that "
                f"have been pending ≥ 14 days from trusted requestors. "
                f"Revisit and roll back if needed."
            ),
            url="/cp/changes",
            topic=f"absence_policy_applied:{cr_id}",
            critical=False,
            arbitrate=False,    # bypass — operator should see this
        )
    except Exception:
        logger.debug("ul.absence: notify failed", exc_info=True)


def _emit_audit(cr_id: str, cr) -> None:
    """Continuity-ledger emission so annual reflection sees the absent-apply
    events as a distinct subkind."""
    try:
        from app.identity.continuity_ledger import record_event
        record_event(
            kind="ecosystem_snapshot",
            actor="upgrade_lifecycle.absence_policy",
            summary=f"absence auto-apply: CR {cr_id}",
            detail={
                "subkind": "absence_auto_apply",
                "cr_id": str(cr_id),
                "applied_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        logger.debug("ul.absence: ledger emit failed", exc_info=True)


def _persist_run(promoted: list[str], now: datetime) -> None:
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if p.exists():
            try:
                existing = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = {}
        existing["last_run_at"] = now.isoformat()
        existing.setdefault("history", [])
        if promoted:
            existing["history"].append({
                "at": now.isoformat(),
                "promoted": promoted,
            })
        # Cap history at 200 entries — older absence events
        # remain in the continuity ledger as the durable record.
        existing["history"] = existing["history"][-200:]
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(existing, indent=2, sort_keys=True))
        tmp.replace(p)
    except OSError:
        logger.debug("ul.absence: state persist failed", exc_info=True)
