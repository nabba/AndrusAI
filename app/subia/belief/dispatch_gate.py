"""
subia.belief.dispatch_gate — HOT-3 closure: consulted beliefs gate
crew dispatch.

Before Phase 2 the metacognitive monitor's consult_beliefs() recorded
which beliefs were consulted and then proceeded to dispatch the crew
regardless of what those beliefs said. Butlin et al. (2023) HOT-3
requires beliefs to be *input* to action selection — recorded-but-
ignored does not qualify. This module closes that half-circuit.

The function decide_dispatch() takes the beliefs a caller retrieved
for the task and produces a structured DispatchDecision:

    ALLOW       — consulted beliefs are sufficient and coherent
    ESCALATE    — beliefs are low-confidence or missing, add a
                  reflexion pass before dispatch
    BLOCK       — a SUSPENDED/RETRACTED belief covers this task,
                  refuse dispatch and surface to the user

The design is deliberately pure-functional: the gate does not call
the belief store or the database. Callers hand in the beliefs they
queried. This keeps the gate testable in isolation and makes the
policy explicit rather than hidden inside a side-effecting method.

Infrastructure-level. Not agent-modifiable. See PROGRAM.md Phase 2.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

logger = logging.getLogger(__name__)


# ── Thresholds (immutable, Tier-3) ─────────────────────────────────

# Consulted ACTIVE beliefs below this confidence trigger escalation
# rather than a clean allow. Below the belief_suspension_threshold in
# the existing consciousness config (typically 0.20), but we want a
# slightly more conservative gate for dispatch.
_LOW_CONFIDENCE_FLOOR = 0.30

# If a SUSPENDED/RETRACTED belief is above this semantic-similarity
# threshold to the task, block dispatch. The caller supplies the
# similarity score along with the belief.
_BLOCKING_SIMILARITY_THRESHOLD = 0.72


# ── Decision ──────────────────────────────────────────────────────

@dataclass
class DispatchDecision:
    """Structured result of a belief-gated dispatch evaluation."""
    verdict: str                       # 'ALLOW' | 'ESCALATE' | 'BLOCK'
    reason: str                        # Human-readable explanation
    consulted_belief_ids: list = field(default_factory=list)
    blocking_belief_ids: list = field(default_factory=list)
    escalation_type: str | None = None  # None | 'reflexion' | 'user_confirmation'
    lowest_confidence: float | None = None
    belief_count: int = 0

    @property
    def allow(self) -> bool:
        return self.verdict == "ALLOW"

    @property
    def blocked(self) -> bool:
        return self.verdict == "BLOCK"

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "consulted_belief_ids": list(self.consulted_belief_ids),
            "blocking_belief_ids": list(self.blocking_belief_ids),
            "escalation_type": self.escalation_type,
            "lowest_confidence": (
                round(self.lowest_confidence, 3)
                if self.lowest_confidence is not None else None
            ),
            "belief_count": self.belief_count,
        }


# ── Core gate ──────────────────────────────────────────────────────

def decide_dispatch(
    consulted_beliefs: Iterable = (),
    suspended_candidates: Iterable = (),
    task_description: str = "",
    crew_name: str = "",
) -> DispatchDecision:
    """Evaluate whether a crew dispatch should proceed given the beliefs
    that were consulted for the task.

    Args:
        consulted_beliefs:
            Iterable of ACTIVE Belief objects the caller retrieved
            via belief_store.query_relevant(). Each must expose
            .belief_id, .confidence, .belief_status attributes.
        suspended_candidates:
            Iterable of (Belief, similarity_score) tuples where the
            Belief is SUSPENDED/RETRACTED and similarity_score is its
            cosine similarity to the task. Only tuples with
            similarity >= _BLOCKING_SIMILARITY_THRESHOLD will actually
            block — the caller may safely pass the full suspended set
            and let the gate filter.
        task_description:
            Used only for the reason string; does not affect the decision.
        crew_name:
            Used only for the reason string.

    Returns:
        DispatchDecision. Callers read .verdict and (.allow / .blocked)
        and act accordingly. Never raises.
    """
    consulted = [b for b in consulted_beliefs if getattr(b, "belief_status", "ACTIVE") == "ACTIVE"]

    # 1) BLOCK — a sufficiently-similar SUSPENDED/RETRACTED belief
    #    covers this task. This is the HOT-3 "belief formation gates
    #    agency" criterion.
    blockers = []
    for entry in suspended_candidates:
        try:
            belief, similarity = entry
        except (TypeError, ValueError):
            continue
        status = getattr(belief, "belief_status", "ACTIVE")
        if status in ("SUSPENDED", "RETRACTED") and similarity >= _BLOCKING_SIMILARITY_THRESHOLD:
            blockers.append(belief)

    if blockers:
        return DispatchDecision(
            verdict="BLOCK",
            reason=(
                f"Suspended belief(s) cover '{task_description[:80]}'. "
                f"Refusing {crew_name} dispatch until belief is revalidated."
            ),
            consulted_belief_ids=[getattr(b, "belief_id", "") for b in consulted],
            blocking_belief_ids=[getattr(b, "belief_id", "") for b in blockers],
            escalation_type="user_confirmation",
            belief_count=len(consulted),
        )

    # 2) ESCALATE — no beliefs at all for this domain (novel task
    #    surface) OR all consulted beliefs are below confidence floor.
    if not consulted:
        return DispatchDecision(
            verdict="ESCALATE",
            reason=(
                f"No ACTIVE beliefs found for '{task_description[:80]}'. "
                f"Novel domain — recommending reflexion pass for {crew_name}."
            ),
            consulted_belief_ids=[],
            escalation_type="reflexion",
            belief_count=0,
        )

    confidences = [getattr(b, "confidence", 0.0) for b in consulted]
    lowest = min(confidences)
    if lowest < _LOW_CONFIDENCE_FLOOR:
        return DispatchDecision(
            verdict="ESCALATE",
            reason=(
                f"Consulted {len(consulted)} belief(s) but lowest confidence "
                f"{lowest:.2f} < floor {_LOW_CONFIDENCE_FLOOR}. "
                f"Recommending reflexion pass for {crew_name}."
            ),
            consulted_belief_ids=[getattr(b, "belief_id", "") for b in consulted],
            escalation_type="reflexion",
            lowest_confidence=lowest,
            belief_count=len(consulted),
        )

    # 3) ALLOW — consulted beliefs exist and all clear the floor.
    return DispatchDecision(
        verdict="ALLOW",
        reason=(
            f"Consulted {len(consulted)} belief(s), lowest confidence "
            f"{lowest:.2f} >= floor {_LOW_CONFIDENCE_FLOOR}."
        ),
        consulted_belief_ids=[getattr(b, "belief_id", "") for b in consulted],
        lowest_confidence=lowest,
        belief_count=len(consulted),
    )
