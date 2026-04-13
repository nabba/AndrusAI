"""
Phase 2: HOT-3 half-circuit closure regression tests.

Before Phase 2 the metacognitive monitor's consult_beliefs() recorded
which beliefs were consulted and then dispatched the crew regardless.
After Phase 2 the caller converts that consultation into a structured
DispatchDecision via app.subia.belief.dispatch_gate.decide_dispatch(),
which can ALLOW, ESCALATE (reflexion pass), or BLOCK the dispatch.

These tests prove the decision policy:

  - ALLOW when ACTIVE beliefs are present and all above confidence floor
  - ESCALATE when no beliefs for domain (novel task) — reflexion
  - ESCALATE when any consulted belief is below confidence floor
  - BLOCK when a SUSPENDED/RETRACTED belief is semantically close
  - Never raise, never crash on malformed input

These are the mechanistic checks that move HOT-3 on the Butlin
scorecard from RECORDED (flag-and-forget) to STRONG (beliefs genuinely
gate agency).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.subia.belief.dispatch_gate import (
    _BLOCKING_SIMILARITY_THRESHOLD,
    _LOW_CONFIDENCE_FLOOR,
    DispatchDecision,
    decide_dispatch,
)


# Minimal fake belief type — matches attribute surface used by the gate.
@dataclass
class FakeBelief:
    belief_id: str = ""
    confidence: float = 0.5
    belief_status: str = "ACTIVE"


# ── ALLOW branch ───────────────────────────────────────────────────

class TestAllowBranch:
    def test_single_high_confidence_allows(self):
        d = decide_dispatch(
            consulted_beliefs=[FakeBelief("b1", confidence=0.90)],
            task_description="draft Q2 plan",
            crew_name="writer",
        )
        assert d.verdict == "ALLOW"
        assert d.allow
        assert not d.blocked
        assert d.escalation_type is None
        assert d.lowest_confidence == pytest.approx(0.90)
        assert d.belief_count == 1

    def test_all_above_floor_allows(self):
        beliefs = [
            FakeBelief("b1", confidence=0.85),
            FakeBelief("b2", confidence=0.45),
            FakeBelief("b3", confidence=0.33),
        ]
        d = decide_dispatch(consulted_beliefs=beliefs)
        assert d.verdict == "ALLOW"
        assert d.lowest_confidence == pytest.approx(0.33)
        assert d.belief_count == 3

    def test_exactly_at_floor_allows(self):
        """Inclusive floor: confidence == _LOW_CONFIDENCE_FLOOR allows."""
        d = decide_dispatch(
            consulted_beliefs=[FakeBelief("b1", confidence=_LOW_CONFIDENCE_FLOOR)],
        )
        assert d.verdict == "ALLOW"


# ── ESCALATE branch ────────────────────────────────────────────────

class TestEscalateBranch:
    def test_no_beliefs_escalates_as_novel(self):
        d = decide_dispatch(
            consulted_beliefs=[],
            task_description="unprecedented thing",
            crew_name="researcher",
        )
        assert d.verdict == "ESCALATE"
        assert d.escalation_type == "reflexion"
        assert "Novel domain" in d.reason
        assert d.belief_count == 0

    def test_low_confidence_escalates(self):
        beliefs = [FakeBelief("b1", confidence=0.20)]
        d = decide_dispatch(consulted_beliefs=beliefs)
        assert d.verdict == "ESCALATE"
        assert d.escalation_type == "reflexion"
        assert d.lowest_confidence == pytest.approx(0.20)

    def test_mix_of_confidences_uses_lowest(self):
        """The weakest consulted belief drives the decision."""
        beliefs = [
            FakeBelief("b1", confidence=0.95),
            FakeBelief("b2", confidence=0.15),  # low
            FakeBelief("b3", confidence=0.80),
        ]
        d = decide_dispatch(consulted_beliefs=beliefs)
        assert d.verdict == "ESCALATE"
        assert d.lowest_confidence == pytest.approx(0.15)

    def test_escalate_reason_names_crew(self):
        d = decide_dispatch(consulted_beliefs=[], crew_name="coder")
        assert "coder" in d.reason


# ── BLOCK branch ───────────────────────────────────────────────────

class TestBlockBranch:
    def test_suspended_belief_above_threshold_blocks(self):
        suspended = FakeBelief("s1", confidence=0.0, belief_status="SUSPENDED")
        d = decide_dispatch(
            consulted_beliefs=[FakeBelief("a1", confidence=0.9)],
            suspended_candidates=[(suspended, _BLOCKING_SIMILARITY_THRESHOLD + 0.05)],
            task_description="restart the migration",
            crew_name="coder",
        )
        assert d.verdict == "BLOCK"
        assert d.blocked
        assert d.escalation_type == "user_confirmation"
        assert d.blocking_belief_ids == ["s1"]
        assert "s1" not in d.consulted_belief_ids
        assert "coder" in d.reason

    def test_retracted_belief_also_blocks(self):
        retracted = FakeBelief("r1", belief_status="RETRACTED")
        d = decide_dispatch(
            suspended_candidates=[(retracted, 0.85)],
        )
        assert d.verdict == "BLOCK"
        assert d.blocking_belief_ids == ["r1"]

    def test_suspended_below_threshold_does_not_block(self):
        """Suspended belief exists but isn't semantically close enough."""
        suspended = FakeBelief("s1", belief_status="SUSPENDED")
        d = decide_dispatch(
            consulted_beliefs=[FakeBelief("a1", confidence=0.8)],
            suspended_candidates=[(suspended, _BLOCKING_SIMILARITY_THRESHOLD - 0.10)],
        )
        assert d.verdict == "ALLOW", (
            "low-similarity suspended belief must not block"
        )

    def test_block_wins_over_escalate(self):
        """If a SUSPENDED belief blocks AND no ACTIVE beliefs exist,
        BLOCK wins. Refusing dispatch is stricter than escalation.
        """
        suspended = FakeBelief("s1", belief_status="SUSPENDED")
        d = decide_dispatch(
            consulted_beliefs=[],  # would escalate
            suspended_candidates=[(suspended, 0.95)],  # but blocks
        )
        assert d.verdict == "BLOCK"

    def test_non_suspended_in_suspended_list_is_ignored(self):
        """Defence-in-depth: if the caller mislabels an ACTIVE belief as
        a suspended_candidate, the gate must ignore it.
        """
        active = FakeBelief("a1", belief_status="ACTIVE")
        d = decide_dispatch(
            consulted_beliefs=[FakeBelief("b1", confidence=0.9)],
            suspended_candidates=[(active, 0.99)],  # mislabeled
        )
        assert d.verdict == "ALLOW"
        assert d.blocking_belief_ids == []


# ── Policy robustness ──────────────────────────────────────────────

class TestPolicyRobustness:
    def test_inactive_consulted_beliefs_are_filtered(self):
        """The gate itself filters for ACTIVE status in consulted_beliefs.
        A caller that accidentally includes a SUSPENDED belief in the
        ACTIVE list should not have the gate count it as a consulted
        belief — that would be silently permissive.
        """
        beliefs = [
            FakeBelief("a1", confidence=0.9, belief_status="ACTIVE"),
            FakeBelief("s2", confidence=0.99, belief_status="SUSPENDED"),  # wrong list
        ]
        d = decide_dispatch(consulted_beliefs=beliefs)
        assert d.verdict == "ALLOW"
        assert d.belief_count == 1
        assert d.consulted_belief_ids == ["a1"]

    def test_malformed_suspended_entries_ignored(self):
        """Tuples that don't unpack (belief, sim) are silently skipped."""
        d = decide_dispatch(
            consulted_beliefs=[FakeBelief("b1", confidence=0.8)],
            suspended_candidates=[None, "invalid", (FakeBelief("s1", belief_status="SUSPENDED"),)],
        )
        assert d.verdict == "ALLOW"

    def test_decision_serializes(self):
        d = decide_dispatch(
            consulted_beliefs=[FakeBelief("b1", confidence=0.8)],
            task_description="task",
        )
        payload = d.to_dict()
        assert payload["verdict"] == "ALLOW"
        assert payload["consulted_belief_ids"] == ["b1"]
        assert payload["belief_count"] == 1
        assert isinstance(payload["lowest_confidence"], float)

    def test_never_raises_on_missing_attributes(self):
        """Gate must be permissive about object shape — missing fields
        are treated as safe defaults.
        """
        class Minimal:
            pass  # No belief_id, no confidence, no belief_status.

        minimal = Minimal()
        minimal.belief_status = "ACTIVE"
        minimal.confidence = 0.5
        # No belief_id attribute.
        d = decide_dispatch(consulted_beliefs=[minimal])
        assert d.verdict == "ALLOW"


# ── Butlin HOT-3 acceptance ────────────────────────────────────────

class TestHOT3Acceptance:
    """These tests, taken together, move HOT-3 on the Butlin scorecard
    from RECORDED (consulted beliefs are logged, dispatch proceeds) to
    STRONG (beliefs genuinely gate the dispatch).
    """

    def test_belief_suspension_blocks_action(self):
        """The core HOT-3 criterion: a belief suspended through
        metacognitive monitoring must refuse future action on that
        domain until revalidated.
        """
        s = FakeBelief("suspended-migration",
                       confidence=0.0, belief_status="SUSPENDED")
        d = decide_dispatch(
            suspended_candidates=[(s, 0.90)],
            task_description="run the deprecated migration",
            crew_name="coder",
        )
        assert d.blocked
        assert d.escalation_type == "user_confirmation"

    def test_recorded_but_high_confidence_allows(self):
        """Regression guard: the gate must still allow normal dispatch
        when consulted beliefs are high-confidence. A too-strict gate
        would be just as bad as a too-lax one.
        """
        d = decide_dispatch(
            consulted_beliefs=[
                FakeBelief("b1", confidence=0.85),
                FakeBelief("b2", confidence=0.60),
            ],
        )
        assert d.verdict == "ALLOW"

    def test_verdict_is_structured_not_boolean(self):
        """The gate returns a three-valued verdict, not a bool. Callers
        must be able to distinguish 'approve', 'escalate', and 'refuse'
        because these require different UI behaviour.
        """
        allow = decide_dispatch(consulted_beliefs=[FakeBelief(confidence=0.9)])
        escalate = decide_dispatch(consulted_beliefs=[])
        blocked = decide_dispatch(
            suspended_candidates=[(FakeBelief(belief_status="SUSPENDED"), 0.95)]
        )
        assert isinstance(allow, DispatchDecision)
        assert {allow.verdict, escalate.verdict, blocked.verdict} == {
            "ALLOW", "ESCALATE", "BLOCK",
        }
