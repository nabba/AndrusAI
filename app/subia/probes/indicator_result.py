"""
subia.probes.indicator_result — shared types for the Phase 9 scorecard.

Every Butlin/RSM/SK indicator evaluator returns a structured
IndicatorResult so the aggregator can produce a single unified
scorecard. The dataclass is deliberately minimal — just enough
evidence to convince a skeptical reviewer that a claim is backed
by a mechanism + a test + a Tier-3 protection, or explicitly not
attempted with a reason.

Status semantics:

  STRONG             Mechanism present, closed-loop wired, regression
                     test exists, Tier-3 protected. Meets the indicator
                     as formulated.
  PARTIAL            Some part of the mechanism is present, but the
                     indicator is not fully realized. E.g. separable
                     signal computed but caller does not gate on it.
  ABSENT             Architecturally cannot be met by an LLM-based
                     system (e.g. RPT-1 algorithmic recurrence).
                     NOT a failure — honesty about ceiling.
  FAIL               The mechanism is claimed to exist but does not
                     pass validation. This is the worst outcome and
                     the one Phase 9 aims to have ZERO of.
  NOT_ATTEMPTED      Evaluator not run (stub), or out-of-scope for
                     this release.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    STRONG = "STRONG"
    PARTIAL = "PARTIAL"
    ABSENT = "ABSENT"
    FAIL = "FAIL"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"


@dataclass
class IndicatorResult:
    """One row of the scorecard."""
    indicator: str                  # 'GWT-2', 'RSM-a', 'SK-ownership', etc.
    theory: str                     # 'GWT', 'RSM', 'SK', 'PP', 'HOT', 'AST', 'RPT', 'AE'
    status: Status = Status.NOT_ATTEMPTED
    mechanism: str = ""             # path or identifier of the implementing module
    closed_loop: bool = False       # does a behaviour-gating consumer exist
    tier3_protected: bool = False   # listed in TIER3_FILES
    test_file: str = ""             # regression test file path
    notes: str = ""                 # 1-2 sentences for human readers
    evidence: list = field(default_factory=list)  # additional file paths

    def to_dict(self) -> dict:
        return {
            "indicator": self.indicator,
            "theory": self.theory,
            "status": self.status.value if isinstance(self.status, Status) else str(self.status),
            "mechanism": self.mechanism,
            "closed_loop": self.closed_loop,
            "tier3_protected": self.tier3_protected,
            "test_file": self.test_file,
            "notes": self.notes,
            "evidence": list(self.evidence),
        }


# ── Helper: repo-aware checks ────────────────────────────────────

from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def module_exists(relpath: str) -> bool:
    """True iff the given repo-relative path is present on disk."""
    return (_repo_root() / relpath).exists()


def is_tier3(relpath: str) -> bool:
    """True iff the given repo-relative path is in TIER3_FILES."""
    try:
        from app.safety_guardian import TIER3_FILES
    except Exception:
        return False
    return str(relpath) in TIER3_FILES


def test_exists(relpath: str) -> bool:
    """True iff the given regression-test path is present on disk."""
    return module_exists(relpath)


# ── Shortcut for "full" indicators ───────────────────────────────

def strong_indicator(
    indicator: str, theory: str,
    mechanism: str, test_file: str,
    notes: str = "", evidence: list | None = None,
    *,
    closed_loop: bool = True,
) -> IndicatorResult:
    """Build a STRONG indicator row after verifying all three
    preconditions (module exists, Tier-3-protected, test exists).
    Downgrades to PARTIAL if the mechanism is present but not Tier-3,
    and to FAIL if the mechanism is missing entirely.
    """
    evidence = list(evidence or [])
    mech_ok = module_exists(mechanism)
    tier3 = is_tier3(mechanism)
    test_ok = test_exists(test_file)

    if not mech_ok:
        return IndicatorResult(
            indicator=indicator, theory=theory,
            status=Status.FAIL,
            mechanism=mechanism, test_file=test_file,
            closed_loop=closed_loop, tier3_protected=tier3,
            notes=(notes + " (missing mechanism)").strip(),
            evidence=evidence,
        )
    if not test_ok:
        return IndicatorResult(
            indicator=indicator, theory=theory,
            status=Status.PARTIAL,
            mechanism=mechanism, test_file=test_file,
            closed_loop=closed_loop, tier3_protected=tier3,
            notes=(notes + " (no regression test)").strip(),
            evidence=evidence,
        )
    if not tier3:
        # Mechanism + tests present but not tier-3-protected: still
        # partial because an agent could neuter the closure.
        return IndicatorResult(
            indicator=indicator, theory=theory,
            status=Status.PARTIAL,
            mechanism=mechanism, test_file=test_file,
            closed_loop=closed_loop, tier3_protected=False,
            notes=(notes + " (not Tier-3 protected)").strip(),
            evidence=evidence,
        )
    return IndicatorResult(
        indicator=indicator, theory=theory,
        status=Status.STRONG,
        mechanism=mechanism, test_file=test_file,
        closed_loop=closed_loop, tier3_protected=True,
        notes=notes,
        evidence=evidence,
    )


def absent_indicator(
    indicator: str, theory: str, notes: str,
) -> IndicatorResult:
    """Build an ABSENT-by-architectural-declaration indicator row.

    This is how we honestly declare that we cannot achieve the
    indicator given the LLM substrate — not a failure, just a
    ceiling. Phase 9 target: 5 such declarations.
    """
    return IndicatorResult(
        indicator=indicator, theory=theory,
        status=Status.ABSENT,
        notes=notes,
    )


def partial_indicator(
    indicator: str, theory: str,
    mechanism: str, test_file: str,
    notes: str, evidence: list | None = None,
) -> IndicatorResult:
    """Build a PARTIAL indicator row — mechanism present but not a
    fully-closed loop (or not separable enough to count as STRONG).
    """
    evidence = list(evidence or [])
    mech_ok = module_exists(mechanism) if mechanism else True
    test_ok = test_exists(test_file) if test_file else True
    tier3 = is_tier3(mechanism) if mechanism else False
    status = Status.PARTIAL if (mech_ok and test_ok) else Status.FAIL
    return IndicatorResult(
        indicator=indicator, theory=theory,
        status=status,
        mechanism=mechanism, test_file=test_file,
        closed_loop=False, tier3_protected=tier3,
        notes=notes, evidence=evidence,
    )
