"""Trust zones — 8 named zones from FREE to IMMUTABLE.

Each zone bundles the policy knobs that the classifier and validator
together enforce. The mapping from a target path to a zone is a pure
function over the path string + a few well-known prefixes; no I/O
required. This makes the classifier replayable, testable, and safe
to call from inside a hot path.

Zones (in order of restrictiveness):

  1. FREE — ephemeral / scratch / nothing the operator would audit.
        Examples: temporary worktrees under ``coding_session/<id>/``,
        in-memory caches, ephemeral logs.

  2. REVERSIBLE — easy-to-undo writes inside the workspace.
        Examples: ``workspace/notes/``, ``workspace/output/``.
        Auto-permitted with audit, no operator gate.

  3. OBSERVABLE — append-only logs the operator may scan.
        Examples: ``workspace/audit.log``, JSONL ledger appends.
        Auto with audit; never delete or rewrite.

  4. OPERATOR_GATED — the default zone for everything not otherwise
        classified. Standard ``change_requests`` flow.

  5. TWO_PARTY — requires Tier-3 amendment protocol.
        Examples: paths under ``app/governance_amendment/`` or
        ``app/governance_ratchet/`` (those are TIER_IMMUTABLE, but
        the same idea applies for any future paths the operator
        wants doubly-confirmed).

  6. SECURITY_SENSITIVE — auth / vetting / sanitization surface.
        Examples: ``app/security.py``, ``app/sanitize.py``,
        ``app/vetting.py``. Operator-gated even when other criteria
        would suggest auto.

  7. FINANCIAL — anything with real-money side effects.
        Examples: ``app/control_plane/budgets.py``,
        ``deploy/scripts/`` that invoke billable APIs.

  8. IMMUTABLE — TIER_IMMUTABLE files. Always REFUSE.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional


class TrustZone(str, Enum):
    FREE = "free"
    REVERSIBLE = "reversible"
    OBSERVABLE = "observable"
    OPERATOR_GATED = "operator_gated"
    TWO_PARTY = "two_party"
    SECURITY_SENSITIVE = "security_sensitive"
    FINANCIAL = "financial"
    IMMUTABLE = "immutable"


@dataclass(frozen=True)
class ZoneConfig:
    """Policy knobs for a trust zone."""

    zone: TrustZone
    description: str
    auto_eligible: bool                   # may be AUTO when other checks pass
    requires_operator_approval: bool      # standard CR gate
    requires_two_party: bool              # Tier-3 amendment protocol
    audit_required: bool                  # write must be persisted to audit log
    additive_only: bool                   # no deletions permitted
    default_line_cap: Optional[int]       # None ⇒ no cap


# Per-zone configuration. Ordering matches TrustZone enum.
ZONE_CONFIGS: dict[TrustZone, ZoneConfig] = {
    TrustZone.FREE: ZoneConfig(
        zone=TrustZone.FREE,
        description=(
            "Ephemeral writes the operator never audits — scratch worktrees, "
            "in-memory caches, throwaway logs."
        ),
        auto_eligible=True,
        requires_operator_approval=False,
        requires_two_party=False,
        audit_required=False,
        additive_only=False,
        default_line_cap=None,
    ),
    TrustZone.REVERSIBLE: ZoneConfig(
        zone=TrustZone.REVERSIBLE,
        description=(
            "Easy-to-undo writes inside the workspace (notes, output)."
        ),
        auto_eligible=True,
        requires_operator_approval=False,
        requires_two_party=False,
        audit_required=True,
        additive_only=False,
        default_line_cap=None,
    ),
    TrustZone.OBSERVABLE: ZoneConfig(
        zone=TrustZone.OBSERVABLE,
        description=(
            "Append-only logs the operator may scan; never deleted."
        ),
        auto_eligible=True,
        requires_operator_approval=False,
        requires_two_party=False,
        audit_required=True,
        additive_only=True,
        default_line_cap=None,
    ),
    TrustZone.OPERATOR_GATED: ZoneConfig(
        zone=TrustZone.OPERATOR_GATED,
        description=(
            "Default zone — standard change_requests operator gate."
        ),
        auto_eligible=False,
        requires_operator_approval=True,
        requires_two_party=False,
        audit_required=True,
        additive_only=False,
        default_line_cap=None,
    ),
    TrustZone.TWO_PARTY: ZoneConfig(
        zone=TrustZone.TWO_PARTY,
        description=(
            "Requires Tier-3 amendment protocol (two-party / cooldown)."
        ),
        auto_eligible=False,
        requires_operator_approval=True,
        requires_two_party=True,
        audit_required=True,
        additive_only=False,
        default_line_cap=None,
    ),
    TrustZone.SECURITY_SENSITIVE: ZoneConfig(
        zone=TrustZone.SECURITY_SENSITIVE,
        description=(
            "Auth / vetting / sanitization surface; gated even when "
            "other criteria would suggest auto."
        ),
        auto_eligible=False,
        requires_operator_approval=True,
        requires_two_party=False,
        audit_required=True,
        additive_only=False,
        default_line_cap=None,
    ),
    TrustZone.FINANCIAL: ZoneConfig(
        zone=TrustZone.FINANCIAL,
        description=(
            "Real-money side effects (budgets, billable API scripts)."
        ),
        auto_eligible=False,
        requires_operator_approval=True,
        requires_two_party=False,
        audit_required=True,
        additive_only=False,
        default_line_cap=None,
    ),
    TrustZone.IMMUTABLE: ZoneConfig(
        zone=TrustZone.IMMUTABLE,
        description=(
            "TIER_IMMUTABLE files — refuse by default. Only the Tier-3 "
            "amendment protocol can graduate a file out of this zone."
        ),
        auto_eligible=False,
        requires_operator_approval=False,  # refused at validate-time
        requires_two_party=True,
        audit_required=True,
        additive_only=False,
        default_line_cap=None,
    ),
}


# Path-prefix → zone routing tables. Order matters: longer / more
# specific prefixes are checked first by ``zone_for_path``. Each entry
# is ``(prefix, zone)`` — prefix matches when ``path`` is exactly equal
# OR ``path.startswith(prefix)`` when prefix ends with ``/``.
#
# Editing these tables is a deliberate operator decision — the
# classifier's output is only as safe as the routing. The runtime
# overlay through ``app.runtime_settings`` lives at a different layer:
# it widens the AUTO_APPLY allowlist within the FREE / REVERSIBLE /
# OBSERVABLE zones, never reclassifies a path out of SECURITY_SENSITIVE
# or FINANCIAL.

_FINANCIAL_PREFIXES: tuple[str, ...] = (
    "app/control_plane/budgets.py",
    "app/control_plane/audit.py",
    "deploy/scripts/",
    "deploy/terraform/",
)

_SECURITY_SENSITIVE_PREFIXES: tuple[str, ...] = (
    "app/security.py",
    "app/sanitize.py",
    "app/vetting.py",
    "app/rate_throttle.py",
    "app/circuit_breaker.py",
)

_TWO_PARTY_PREFIXES: tuple[str, ...] = (
    "app/governance_amendment/",
    "app/governance_ratchet/",
)

_OBSERVABLE_PREFIXES: tuple[str, ...] = (
    "workspace/audit.log",
    "workspace/audit_journal.json",
    "workspace/healing/",
    "workspace/resilience/",
    "workspace/continuity_ledger.jsonl",
)

_REVERSIBLE_PREFIXES: tuple[str, ...] = (
    "workspace/notes/",
    "workspace/output/",
    "workspace/skills/",
    "workspace/brainstorm/",
)

_FREE_PREFIXES: tuple[str, ...] = (
    "workspace/coding_sessions/",
    "workspace/.tmp/",
    "workspace/inbox/.processed/",
)


def zone_for_path(
    path: str,
    *,
    immutable_paths: Optional[Iterable[str]] = None,
) -> TrustZone:
    """Map a target path to its trust zone.

    Resolution order (first match wins):
      1. ``immutable_paths`` (typically TIER_IMMUTABLE) → IMMUTABLE
      2. _TWO_PARTY_PREFIXES → TWO_PARTY
      3. _SECURITY_SENSITIVE_PREFIXES → SECURITY_SENSITIVE
      4. _FINANCIAL_PREFIXES → FINANCIAL
      5. _OBSERVABLE_PREFIXES → OBSERVABLE
      6. _REVERSIBLE_PREFIXES → REVERSIBLE
      7. _FREE_PREFIXES → FREE
      8. Default → OPERATOR_GATED

    ``immutable_paths`` defaults to None for testability — production
    callers should pass ``app.auto_deployer.TIER_IMMUTABLE`` explicitly.
    """
    if not path:
        return TrustZone.OPERATOR_GATED

    # Step 1: IMMUTABLE (caller-supplied set)
    if immutable_paths is not None and path in immutable_paths:
        return TrustZone.IMMUTABLE

    # Steps 2-7: prefix tables in restrictiveness order
    for table, zone in (
        (_TWO_PARTY_PREFIXES, TrustZone.TWO_PARTY),
        (_SECURITY_SENSITIVE_PREFIXES, TrustZone.SECURITY_SENSITIVE),
        (_FINANCIAL_PREFIXES, TrustZone.FINANCIAL),
        (_OBSERVABLE_PREFIXES, TrustZone.OBSERVABLE),
        (_REVERSIBLE_PREFIXES, TrustZone.REVERSIBLE),
        (_FREE_PREFIXES, TrustZone.FREE),
    ):
        for prefix in table:
            if path == prefix:
                return zone
            if prefix.endswith("/") and path.startswith(prefix):
                return zone

    # Step 8: default
    return TrustZone.OPERATOR_GATED
