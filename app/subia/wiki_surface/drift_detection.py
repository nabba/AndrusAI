"""
subia.wiki_surface.drift_detection — narrative-audit drift checks.

Phase 8 closes the loop on SubIA Part I §0.4 invariant #3: the
self-narrative audit log (Phase 3 committed the append-only store;
this module adds the comparison logic that PUTS findings into it).

Three drift signals:

  1. CAPABILITY CLAIM vs PREDICTION ACCURACY
     Self-state claims "good at X" but the accuracy tracker shows
     sustained error in that domain. This is the forensic-analysis
     scenario: a self-model that over-represents its own competence.

  2. COMMITMENT FULFILLMENT RATE
     If > 30% of active commitments are in the 'broken' state, the
     system is making commitments it cannot keep. Flag as drift so
     reflection is forced.

  3. STALE SELF-DESCRIPTION
     If the agency_log has accumulated N entries since the last
     self-state update, the self-model has lagged behind behaviour.
     Less urgent than 1 or 2 but still worth surfacing.

The detection is pure functional over kernel + tracker state. Actual
writing goes through the existing append-only narrative_audit
module (Phase 3), which enforces immutability.

Infrastructure-level. Not agent-modifiable. See PROGRAM.md Phase 8.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# Thresholds
_COMMITMENT_BROKEN_RATIO = 0.30
_STALE_AGENCY_LOG_ENTRIES = 20


@dataclass
class DriftFinding:
    """One drift observation ready to be appended to the audit."""
    kind: str                       # 'capability_claim' | 'commitment' | 'stale'
    severity: str = "info"          # 'info' | 'warn' | 'drift'
    finding: str = ""
    sources: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "finding": self.finding,
            "sources": list(self.sources),
        }


@dataclass
class DriftReport:
    """Result of a single drift scan."""
    findings: list = field(default_factory=list)
    capability_mismatches: int = 0
    commitment_drift: bool = False
    stale_self_description: bool = False

    @property
    def has_drift(self) -> bool:
        return any(
            f.severity in ("warn", "drift") for f in self.findings
        )

    def to_dict(self) -> dict:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "has_drift": self.has_drift,
            "capability_mismatches": self.capability_mismatches,
            "commitment_drift": self.commitment_drift,
            "stale_self_description": self.stale_self_description,
        }


# ── Pure detection ────────────────────────────────────────────────

def detect_drift(
    kernel: Any,
    *,
    accuracy_tracker: Any = None,
) -> DriftReport:
    """Scan for the three drift signals. Never raises.

    Args:
        kernel:           SubjectivityKernel with .self_state +
                          .homeostasis + .predictions.
        accuracy_tracker: optional AccuracyTracker; if provided, used
                          for signal #1 (capability-claim mismatch).
    """
    report = DriftReport()

    # Signal 1: capability claim vs prediction accuracy
    try:
        mismatches = _check_capability_claims(kernel, accuracy_tracker)
    except Exception:
        logger.debug("drift_detection: capability check failed",
                     exc_info=True)
        mismatches = []
    for m in mismatches:
        report.findings.append(DriftFinding(
            kind="capability_claim",
            severity="drift",
            finding=m,
            sources=["self_state.capabilities",
                     "prediction.accuracy_tracker"],
        ))
    report.capability_mismatches = len(mismatches)

    # Signal 2: commitment fulfillment rate
    try:
        finding = _check_commitment_rate(kernel)
    except Exception:
        logger.debug("drift_detection: commitment check failed",
                     exc_info=True)
        finding = None
    if finding:
        report.findings.append(finding)
        report.commitment_drift = True

    # Signal 3: stale self-description
    try:
        finding = _check_stale_self_description(kernel)
    except Exception:
        logger.debug("drift_detection: staleness check failed",
                     exc_info=True)
        finding = None
    if finding:
        report.findings.append(finding)
        report.stale_self_description = True

    return report


def append_findings_to_audit(
    report: DriftReport,
    loop_count: int,
    *,
    append_fn: Any | None = None,
) -> int:
    """Forward every finding in the report to the append-only narrative
    audit log. Returns the number of entries written.

    Args:
        append_fn:  override the live `subia.safety.narrative_audit.
                    append_audit` — used in tests.
    """
    if append_fn is None:
        try:
            from app.subia.safety.narrative_audit import append_audit
            append_fn = append_audit
        except Exception:
            logger.debug("drift_detection: narrative_audit unavailable",
                         exc_info=True)
            return 0

    written = 0
    for finding in report.findings:
        try:
            append_fn(
                finding=finding.finding,
                loop_count=int(loop_count),
                sources=finding.sources,
                severity=finding.severity,
            )
            written += 1
        except Exception:
            logger.debug("drift_detection: append failed", exc_info=True)
    return written


# ── Signal implementations ────────────────────────────────────────

def _check_capability_claims(kernel, accuracy_tracker) -> list[str]:
    """For each claimed capability 'high'/'medium'/'low', if the
    accuracy tracker has sustained error in that domain, record a
    mismatch.

    Duck-typed: kernel.self_state.capabilities → dict. We match
    capability names against the prediction-tracker's domain strings
    heuristically.
    """
    if accuracy_tracker is None:
        return []
    self_state = getattr(kernel, "self_state", None)
    if self_state is None:
        return []
    caps = getattr(self_state, "capabilities", {}) or {}
    if not isinstance(caps, dict) or not caps:
        return []

    has_sustained = getattr(accuracy_tracker, "has_sustained_error", None)
    all_domains = getattr(accuracy_tracker, "all_domains_summary", None)
    domain_names: list[str] = []
    if callable(all_domains):
        try:
            summary = all_domains() or {}
            domain_names = [
                d["domain"] for d in summary.get("domains", [])
                if isinstance(d, dict) and "domain" in d
            ]
        except Exception:
            domain_names = []

    mismatches: list[str] = []
    for cap, level in caps.items():
        if str(level).lower() not in ("high", "medium"):
            continue
        # Any domain whose key contains the capability name
        # (case-insensitive) counts as a domain we claim competence over.
        cap_l = str(cap).lower()
        hit_domains = [
            d for d in domain_names
            if cap_l and cap_l in d.lower()
        ]
        for dom in hit_domains:
            sustained = False
            try:
                if callable(has_sustained):
                    sustained = bool(has_sustained(dom))
            except Exception:
                sustained = False
            if sustained:
                mismatches.append(
                    f"Capability claim '{cap}: {level}' contradicted "
                    f"by sustained prediction error in domain '{dom}'"
                )
    return mismatches


def _check_commitment_rate(kernel) -> DriftFinding | None:
    self_state = getattr(kernel, "self_state", None)
    if self_state is None:
        return None
    commitments = list(getattr(self_state, "active_commitments", []) or [])
    if len(commitments) < 5:
        # Too small a sample to draw conclusions.
        return None
    broken = [
        c for c in commitments
        if getattr(c, "status", "active") == "broken"
    ]
    ratio = len(broken) / len(commitments)
    if ratio <= _COMMITMENT_BROKEN_RATIO:
        return None
    return DriftFinding(
        kind="commitment",
        severity="drift",
        finding=(
            f"Commitment drift: {len(broken)}/{len(commitments)} "
            f"commitments broken ({ratio * 100:.0f}%)"
        ),
        sources=["self_state.active_commitments"],
    )


def _check_stale_self_description(kernel) -> DriftFinding | None:
    self_state = getattr(kernel, "self_state", None)
    if self_state is None:
        return None
    log = list(getattr(self_state, "agency_log", []) or [])
    if len(log) < _STALE_AGENCY_LOG_ENTRIES:
        return None
    # Heuristic: if we have >= N agency-log entries but no
    # capabilities / limitations / goals updates in the last N entries,
    # the self-description hasn't kept up. Without per-update
    # timestamps we fall back to a size-based trigger that fires
    # periodically so the audit sees the signal.
    return DriftFinding(
        kind="stale",
        severity="warn",
        finding=(
            f"Stale self-description: agency log has {len(log)} entries "
            f"but the self-state snapshot has not been refreshed; "
            f"consider re-deriving capabilities/limitations."
        ),
        sources=["self_state.agency_log"],
    )
