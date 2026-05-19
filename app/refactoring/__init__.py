"""refactoring — Phase 2 of the elegance plan: refactor-proposal producer.

Closes the "see clearly → act" gap left by Phase 1. The Phase 1 monitors
(``elegance_drift`` + ``architectural_drift``) surface drift to Signal +
continuity ledger but never propose code changes. This package is the
4th producer in :mod:`app.proposal_bridge` — alongside ``capability_gap``,
``library_radar``, ``paper_pipeline``, ``dependency_radar`` — that turns
those drift signals into structured refactor proposals.

What lands at each stage
------------------------

1. The proposer reads Phase 1 artefacts:
     * ``workspace/code_quality/elegance_history.json``
     * ``workspace/code_quality/architectural_baseline.json``
2. Three detectors filter candidates from those:
     * ``complexity_hotspot`` — files whose composite is < 0.65 AND
       whose ``complexity_score`` is < 0.40 (the lever that matters
       most: branching density).
     * ``import_cycle`` — actionable cycles (≤20 members) found by
       :mod:`app.healing.monitors.architectural_drift`.
     * ``parallel_capability`` — capabilities owned by ≥3 distinct
       files (likely parallel implementations).
3. For each candidate the proposer constructs:
     * a deterministic ``signature`` so re-runs over the same data
       are idempotent at the bridge level.
     * a markdown ``body`` describing what to refactor, why, and how.
     * a ``coding_session_spec`` scaffold (intent / files / acceptance /
       expected_duration_min) so an agent picking up the proposal has
       a concrete starting point.
4. The proposer stages each candidate via
   :func:`app.proposal_bridge.stage` with a 14-day cooldown. The bridge
   promoter eventually files a CR through the standard operator gate.

Design discipline
-----------------

* **Reads, never writes.** The proposer never touches code; it only
  stages markdown. All code changes flow through the change-request
  lifecycle (operator gate + 60-min auto-revert window).
* **TIER_IMMUTABLE absolute.** The change-request validator forbids
  TIER_IMMUTABLE writes at stage time. Even if a detector somehow
  proposed touching governance.py, ``proposal_bridge.stage`` would
  reject it.
* **Default OFF.** Conservative first ship. Operator reviews docs and
  the Phase 1 baseline, then flips on via ``/cp/settings``.
* **Per-pass cap of 3 candidates per detector.** Backlog of refactor
  candidates spreads over many weeks rather than flooding the operator.
* **Long cooldown (14 days).** Refactors are never urgent — the cooldown
  is also the "did this signal persist?" filter.

Master switch
-------------
``runtime_settings.refactor_proposer_enabled`` (default OFF). Falls back
to ``REFACTOR_PROPOSER_ENABLED`` env var when the runtime settings
module is unavailable.
"""
from __future__ import annotations

from app.refactoring.proposer import (
    RefactorCandidate,
    detect_complexity_hotspots,
    detect_import_cycles,
    detect_parallel_capabilities,
    run_one_pass,
    start,
    stop,
)

__all__ = [
    "RefactorCandidate",
    "detect_complexity_hotspots",
    "detect_import_cycles",
    "detect_parallel_capabilities",
    "run_one_pass",
    "start",
    "stop",
]

# Eager-start at import — same pattern as library_radar and the bridge
# promoter. Anchored from app.healing.__init__ so the daemon runs at
# gateway boot.
start()
