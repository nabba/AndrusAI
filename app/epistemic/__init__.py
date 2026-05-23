"""
Epistemic Integrity Layer — provenance, calibration, and post-mortem
analysis of agent reasoning.

Real-time gate that distinguishes verified from inferred claims, plus
post-hoc analysis that feeds the Self-Improver's existing 6-stage
pipeline. Full design: see ``crewai-team/docs/EPISTEMIC_INTEGRITY.md``.

Phase 0 (this commit) ships only the foundational data model:
  * the Claim Ledger and its three emission paths (path 1 wired,
    paths 2 and 3 reserved for Phase 1)
  * PostgreSQL persistence into ``control_plane.epistemic_claims``
  * a hook registry so detector subsystems can self-register
    without the Ledger importing them

Off by default — toggle with EPISTEMIC_ENABLED=true.

──────────────────────────────────────────────────────────────────────
Naming note (Phase A5 disambiguation):

    app.epistemic ← THIS PACKAGE: claim ledger, calibration, pushback,
                    overrides. Runtime tracking of what the system
                    CLAIMS and whether those claims hold up.

    app.episteme  ← DIFFERENT PACKAGE: RAG retrieval over a research
                    knowledge base. "What does the literature say
                    about X?" — vector search tool for agents.

The two are intentionally separate concerns; do not conflate them.
──────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import os

# ── Public API ─────────────────────────────────────────────────────────
# Re-export the small surface that callers should depend on. Anything
# not exported here is internal.
from app.epistemic.ledger import (
    LEDGER_MAX_CLAIMS_PER_TASK,
    Claim,
    Evidence,
    Ledger,
    Register,
    VerificationStatus,
    VerifyingAction,
)
from app.epistemic.registry import (
    ClaimHook,
    claim_hooks,
    register as register_claim_hook,
)

__all__ = [
    "Claim",
    "ClaimHook",
    "Evidence",
    "LEDGER_MAX_CLAIMS_PER_TASK",
    "Ledger",
    "Register",
    "VerificationStatus",
    "VerifyingAction",
    "claim_hooks",
    "is_enabled",
    "register_claim_hook",
]


def is_enabled() -> bool:
    """Off by default. Flip EPISTEMIC_ENABLED=true (env) or override
    via runtime_settings to activate.

    Priority: runtime_settings override → env var → False.

    The env var remains canonical for boot-time / test / script
    contexts (and matches the pattern from ``app.recovery.loop``).
    The runtime_settings overlay lets the React ``/cp/settings`` flip
    the gate without a gateway restart. When the override is None
    (default), behaviour is identical to the env-var-only design.
    """
    # Overlay takes precedence when explicitly set (True or False).
    try:
        from app.runtime_settings import get_epistemic_enabled_override
        override = get_epistemic_enabled_override()
        if override is not None:
            return override
    except Exception:
        # runtime_settings may not be importable in stripped-down test
        # contexts — fall through to env-var. Safe by construction.
        pass
    val = os.getenv("EPISTEMIC_ENABLED", "").strip().lower()
    return val in ("1", "true", "yes", "on")


# ── Bootstrap: register detectors via import side-effect ────────────
# Importing this module attaches the realtime meta-hook to the claim
# ledger and registers the post-hoc detectors. Idempotent — re-import
# is a no-op (the registries dedup).
from app.epistemic.detectors import realtime as _realtime  # noqa: E402,F401
from app.epistemic.detectors import posthoc as _posthoc  # noqa: E402,F401
