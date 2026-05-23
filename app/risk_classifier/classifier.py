"""Deterministic decision tree: Action → Decision.

Pure function over (action, zone-routing, runtime allowlists). No I/O,
no LLM, no DB. Replayable, testable, safe for hot-path use.

Decision lattice — least-to-most permissive:
    REFUSE < TWO_PARTY < GATED < AUTO

The classifier proposes; the validator (gated by source-pinned
allowlists + sanity caps) disposes. When this module returns AUTO,
the actual change-request flow still routes through
``app.change_requests.validator.validate_auto_apply`` which enforces
TIER_IMMUTABLE + forbidden prefixes + line cap + additive-only + the
allowlists. The classifier exists to give CALLERS (executor,
lifecycle, dashboard) a single answer about how restrictive an
action's gate will be, without each one re-implementing the rules.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional

from app.risk_classifier.zones import (
    TrustZone,
    ZONE_CONFIGS,
    zone_for_path,
)


class Decision(str, Enum):
    """The outcome the classifier proposes for an action."""

    AUTO = "auto"             # may proceed without operator gate
    GATED = "gated"           # standard change_requests operator gate
    TWO_PARTY = "two_party"   # Tier-3 amendment protocol
    REFUSE = "refuse"          # categorical refusal


@dataclass(frozen=True)
class Action:
    """An agent's proposed action, classified by ``classify``.

    All fields are caller-supplied — the classifier never inspects
    files or runs network calls. Path traversal and size estimation
    happen in the caller (typically inside ``change_requests.lifecycle``
    or the autonomous executor's pre-dispatch hook).
    """

    action_type: str                       # e.g. "write_file" / "exec_shell"
    target_path: Optional[str] = None      # workspace-relative
    requestor: str = ""                    # agent_id / sender
    change_size_lines: Optional[int] = None  # net delta if applicable
    additive_only: bool = True              # True = no deletions
    has_deletions: bool = False             # convenience for OBSERVABLE check
    rationale: str = ""                    # caller-supplied context


@dataclass(frozen=True)
class ClassificationResult:
    """Result + diagnostic chain. Returned by
    :func:`classify_with_overrides`. ``classify`` returns only the
    Decision for the common case."""

    decision: Decision
    zone: TrustZone
    rationale: str


def classify(
    action: Action,
    *,
    immutable_paths: Optional[Iterable[str]] = None,
    allowed_requestors: Optional[Iterable[str]] = None,
    allowed_paths: Optional[Iterable[str]] = None,
) -> Decision:
    """Convenience wrapper around :func:`classify_with_overrides` that
    returns just the Decision. Most callers want this shape."""
    return classify_with_overrides(
        action,
        immutable_paths=immutable_paths,
        allowed_requestors=allowed_requestors,
        allowed_paths=allowed_paths,
    ).decision


def classify_with_overrides(
    action: Action,
    *,
    immutable_paths: Optional[Iterable[str]] = None,
    allowed_requestors: Optional[Iterable[str]] = None,
    allowed_paths: Optional[Iterable[str]] = None,
) -> ClassificationResult:
    """Full classifier with rationale + zone surfaced.

    Decision tree (in priority order):

      1. No target_path → OPERATOR_GATED zone → GATED.
      2. zone == IMMUTABLE → REFUSE with TIER_IMMUTABLE rationale.
      3. zone == TWO_PARTY → TWO_PARTY.
      4. zone == FINANCIAL → GATED (financial never auto in v1).
      5. zone == SECURITY_SENSITIVE → GATED.
      6. zone in (FREE, REVERSIBLE, OBSERVABLE):
            a. requestor not in allowed_requestors → GATED.
            b. path not allowlisted → GATED.
            c. OBSERVABLE + has_deletions → REFUSE (append-only).
            d. line cap exceeded → GATED.
            otherwise → AUTO.
      7. zone == OPERATOR_GATED → GATED (the default).

    Override parameters:
      * ``immutable_paths`` — typically ``app.auto_deployer.TIER_IMMUTABLE``.
        None → IMMUTABLE never matched (test-only mode).
      * ``allowed_requestors`` — typically
        ``app.runtime_settings.get_auto_apply_allowed_requestors()``
        UNION the source-pinned baseline from validator.py.
      * ``allowed_paths`` — typically the runtime UNION baseline.
    """
    # Step 1: no target path → fall through to default zone.
    if not action.target_path:
        rationale = (
            "action has no target_path → operator-gated by default"
        )
        return ClassificationResult(
            decision=Decision.GATED,
            zone=TrustZone.OPERATOR_GATED,
            rationale=rationale,
        )

    zone = zone_for_path(
        action.target_path,
        immutable_paths=immutable_paths,
    )
    config = ZONE_CONFIGS[zone]

    # Step 2: IMMUTABLE → REFUSE
    if zone is TrustZone.IMMUTABLE:
        return ClassificationResult(
            decision=Decision.REFUSE,
            zone=zone,
            rationale=(
                f"path {action.target_path!r} is TIER_IMMUTABLE — only "
                f"the Tier-3 amendment protocol can graduate it"
            ),
        )

    # Step 3: TWO_PARTY
    if zone is TrustZone.TWO_PARTY:
        return ClassificationResult(
            decision=Decision.TWO_PARTY,
            zone=zone,
            rationale=(
                f"path {action.target_path!r} requires Tier-3 amendment "
                f"protocol (two-party)"
            ),
        )

    # Step 4-5: FINANCIAL / SECURITY_SENSITIVE → GATED (never auto in v1)
    if zone is TrustZone.FINANCIAL:
        return ClassificationResult(
            decision=Decision.GATED,
            zone=zone,
            rationale=(
                f"path {action.target_path!r} has real-money side effects "
                f"— operator-gated regardless of allowlist"
            ),
        )
    if zone is TrustZone.SECURITY_SENSITIVE:
        return ClassificationResult(
            decision=Decision.GATED,
            zone=zone,
            rationale=(
                f"path {action.target_path!r} is on the security surface "
                f"— operator-gated regardless of allowlist"
            ),
        )

    # Step 6: auto-eligible zones
    if config.auto_eligible:
        # 6a. requestor allowlist
        req_set = (
            frozenset(allowed_requestors) if allowed_requestors else frozenset()
        )
        if action.requestor not in req_set:
            return ClassificationResult(
                decision=Decision.GATED,
                zone=zone,
                rationale=(
                    f"requestor {action.requestor!r} not in auto-apply "
                    f"allowlist for zone {zone.value!r} → operator gate"
                ),
            )
        # 6b. path allowlist
        if not _path_in_allowlist(action.target_path, allowed_paths):
            return ClassificationResult(
                decision=Decision.GATED,
                zone=zone,
                rationale=(
                    f"path {action.target_path!r} not in auto-apply "
                    f"allowlist → operator gate"
                ),
            )
        # 6c. additive-only enforcement (OBSERVABLE zone is append-only)
        if config.additive_only and action.has_deletions:
            return ClassificationResult(
                decision=Decision.REFUSE,
                zone=zone,
                rationale=(
                    f"zone {zone.value!r} is append-only; action has "
                    f"deletions → refused"
                ),
            )
        # 6d. line cap (when zone declares one)
        if (
            config.default_line_cap is not None
            and action.change_size_lines is not None
            and action.change_size_lines > config.default_line_cap
        ):
            return ClassificationResult(
                decision=Decision.GATED,
                zone=zone,
                rationale=(
                    f"change size {action.change_size_lines} exceeds "
                    f"zone {zone.value!r} cap {config.default_line_cap} "
                    f"→ operator gate"
                ),
            )
        return ClassificationResult(
            decision=Decision.AUTO,
            zone=zone,
            rationale=(
                f"all auto-apply gates passed for zone {zone.value!r}"
            ),
        )

    # Step 7: OPERATOR_GATED default
    return ClassificationResult(
        decision=Decision.GATED,
        zone=zone,
        rationale=(
            f"zone {zone.value!r} requires standard operator approval"
        ),
    )


def _path_in_allowlist(
    path: str,
    allowed_paths: Optional[Iterable[str]],
) -> bool:
    """Exact match OR prefix match (when entry ends with ``/``).
    Empty allowlist ⇒ never matches."""
    if not allowed_paths:
        return False
    for allowed in allowed_paths:
        if path == allowed:
            return True
        if allowed.endswith("/") and path.startswith(allowed):
            return True
    return False
