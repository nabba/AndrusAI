"""Risk classifier — trust zones + decision tree for agent actions.

Plugs into the dormant AUTO_APPLY infrastructure shipped in
PROGRAM §38.3: the lane's allowlists are now operator-managed via
``app.runtime_settings`` (see PROGRAM §61), and this module supplies
the **classification** layer that says, given an action's target +
requestor + size, which trust zone it belongs to and whether it
qualifies for AUTO, GATED, TWO_PARTY, or REFUSE.

v1 ships the deterministic decision tree as a pure library. No
production caller wires it in yet — the natural integration points
(``change_requests.lifecycle.create_request`` and the autonomous
executor's pre-dispatch hook) consume the classifier in Phase 2.

The classifier composes with — does NOT replace — the existing safety
layers:

  * TIER_IMMUTABLE (``app.auto_deployer.TIER_IMMUTABLE``) → always REFUSE
  * ``_AUTO_APPLY_FORBIDDEN_PREFIXES`` (validator.py) → always REFUSE
  * ``_AUTO_APPLY_ALLOWED_REQUESTORS`` / ``_PATHS`` (validator.py) →
    consulted for AUTO eligibility

When the classifier and validator disagree, the more restrictive answer
wins — the classifier proposes, the validator (gated by operator-
managed allowlists + sanity caps) disposes.

Master switch: ``app.runtime_settings.get_risk_classifier_enabled``
(default False — the module ships as a library; the switch reserves
the React toggle slot and gates future widening-proposal emission).
"""
from __future__ import annotations

from app.risk_classifier.classifier import (
    Action,
    Decision,
    classify,
    classify_with_overrides,
)
from app.risk_classifier.zones import (
    TrustZone,
    ZONE_CONFIGS,
    ZoneConfig,
    zone_for_path,
)

__all__ = [
    "Action",
    "Decision",
    "TrustZone",
    "ZONE_CONFIGS",
    "ZoneConfig",
    "classify",
    "classify_with_overrides",
    "zone_for_path",
]
