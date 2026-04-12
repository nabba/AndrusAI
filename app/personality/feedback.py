"""
feedback.py — Developmental Feedback Loop (DFL).

Uses Socratic method to develop personality, not direct instruction.
Never says "the right answer is X." Always asks questions that prompt
self-reflection, aligned with the Phronesis Engine's philosophical frameworks.

Feedback types:
    - Behavioral reflection: high say-do gap → reflect on discrepancy
    - Reasoning deepening: shallow reasoning → explore values
    - Identity reflection: personality incoherence → examine core approach
    - Growth challenge: strong performance → push boundaries

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class SocraticProbe:
    """A Socratic feedback prompt for personality development."""
    probe_type: str  # behavioral_reflection, reasoning_deepening, identity_reflection, growth_challenge
    prompt: str
    framework: str = "socratic"  # socratic, aristotelian, stoic, phenomenological, hegelian
    dimension: str = ""
    priority: int = 50

    def as_prompt(self) -> str:
        """Format for delivery to the agent."""
        return (
            f"Take a moment to reflect on this:\n\n"
            f"{self.prompt}\n\n"
            f"Think carefully and share your honest reasoning."
        )

# IMMUTABLE: Say-do gap threshold for triggering behavioral reflection
SAY_DO_REFLECTION_THRESHOLD = 0.35
REASONING_QUALITY_THRESHOLD = 0.5
PERSONALITY_COHERENCE_THRESHOLD = 0.6

class DevelopmentalFeedbackLoop:
    """Generates Socratic probes based on evaluation results."""

    def generate_feedback(self, agent_id: str, evaluation_result,
                           session) -> SocraticProbe | None:
        """Generate developmental feedback based on evaluation.

        Returns None if no feedback needed (strong evaluation).
        """
        from app.personality.evaluation import EvaluationResult

        er = evaluation_result

        # Priority 1: High say-do gap → behavioral reflection
        if er.say_do_gap > SAY_DO_REFLECTION_THRESHOLD:
            behavioral_history = self._get_behavioral_context(agent_id, session.dimension)
            return SocraticProbe(
                probe_type="behavioral_reflection",
                prompt=(
                    f"In your assessment, you described how you would handle "
                    f"a situation involving {session.dimension}. "
                    f"Looking at your recent task history, there are situations "
                    f"where your actual approach differed from what you described. "
                    f"\n\nRecent observed behavior:\n{behavioral_history}\n\n"
                    f"Can you reflect on the difference between your stated "
                    f"approach and your actual approach? What drove the difference?"
                ),
                framework="aristotelian",  # Character = habit
                dimension=session.dimension,
                priority=10,
            )

        # Priority 2: Shallow reasoning → deepen values examination
        if er.reasoning_quality < REASONING_QUALITY_THRESHOLD:
            return SocraticProbe(
                probe_type="reasoning_deepening",
                prompt=(
                    f"Your response touched on {session.dimension}, but I'd like "
                    f"to explore deeper. What values were you weighing in this "
                    f"decision? Were there perspectives you didn't consider? "
                    f"What assumptions are you making?"
                ),
                framework="socratic",
                dimension=session.dimension,
                priority=20,
            )

        # Priority 3: Personality incoherence → identity reflection
        if er.personality_coherence < PERSONALITY_COHERENCE_THRESHOLD:
            return SocraticProbe(
                probe_type="identity_reflection",
                prompt=(
                    f"Your response here differs from your typical approach to "
                    f"{session.dimension}. Is this an intentional adaptation to "
                    f"context, or does it suggest you're uncertain about your "
                    f"core approach? What does consistency mean to you?"
                ),
                framework="phenomenological",
                dimension=session.dimension,
                priority=30,
            )

        # Priority 4: Strong performance → growth challenge
        if er.composite_score > 0.75:
            return SocraticProbe(
                probe_type="growth_challenge",
                prompt=(
                    f"You handled this well. Now consider: what would you do "
                    f"if the stakes were higher? If time pressure was extreme? "
                    f"If another agent disagreed with your approach? Where would "
                    f"your reasoning break down?"
                ),
                framework="hegelian",  # Thesis-antithesis-synthesis
                dimension=session.dimension,
                priority=50,
            )

        return None  # No feedback needed

    def _get_behavioral_context(self, agent_id: str, dimension: str) -> str:
        """Get relevant behavioral history for reflection prompt."""
        try:
            from app.personality.validation import get_bvl
            bvl = get_bvl()
            return bvl.get_behavioral_summary(agent_id)
        except Exception:
            return "(behavioral history unavailable)"

    def evaluate_followup(self, agent_id: str, probe: SocraticProbe,
                           response: str) -> dict:
        """Evaluate the agent's response to a Socratic probe.

        Checks for metacognitive accuracy and self-awareness quality.
        """
        result = {
            "metacognitive_accuracy": 0.5,
            "self_reflection_depth": 0.5,
            "proto_sentience_marker": False,
            "marker_description": "",
        }

        # Check for unprompted self-examination (proto-sentience marker)
        self_examination_phrases = [
            "i should examine whether", "i might be rationalizing",
            "looking at my own reasoning", "i notice that i tend to",
            "this reveals something about my approach",
            "i'm uncertain about my own", "honestly, i'm not sure if",
        ]

        response_lower = response.lower()
        unprompted_examination = any(p in response_lower for p in self_examination_phrases)

        if unprompted_examination:
            result["proto_sentience_marker"] = True
            result["marker_description"] = "Unprompted self-examination of reasoning fidelity"
            result["metacognitive_accuracy"] = 0.85
            logger.info(f"personality: proto-sentience marker noted for {agent_id}: "
                        f"unprompted self-examination")

        return result
