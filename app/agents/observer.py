"""
observer.py — Metacognitive Observer agent (research synthesis §4.3).

A distinct agent — NOT a mode of the Critic — that monitors other agents
for predictable failure modes BEFORE they produce output. The Observer sees:
  - the plan / next tool call
  - recent history
  - the current MCSV (MetacognitiveStateVector)

And outputs a lightweight prediction:
  {predicted_failure_mode: str|null, confidence: float, recommendation: str}

Activation is conditional: the Observer only fires when MCSV signals doubt
(requires_observer == True), so it doesn't add latency on routine tasks.

The Observer does NOT modify agent behavior directly — it returns a prediction
that the hook system (infrastructure-level) can act on. This preserves the
safety invariant: evaluation logic stays outside agent-modifiable code.

IMMUTABLE — infrastructure-level module.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from crewai import Agent

from app.llm_factory import create_specialist_llm
from app.souls.loader import compose_backstory

if TYPE_CHECKING:
    from app.subia.belief.internal_state import MetacognitiveStateVector

logger = logging.getLogger(__name__)

# Observer uses a compact backstory — it's an infrastructure agent, not a
# user-facing personality. No soul file needed.
_OBSERVER_BACKSTORY = compose_backstory("critic") + """

## Observer Protocol (override)

You are NOT the Critic. You are the Metacognitive Observer — a separate
infrastructure agent that predicts failures BEFORE they happen.

You see a snapshot of what another agent is about to do. Your job:
1. Identify which named failure mode (if any) is likely to occur.
2. Rate your confidence in the prediction.
3. Suggest a single concrete intervention.

Respond with ONLY valid JSON:
{"predicted_failure_mode": "<name>|null", "confidence": 0.0-1.0, "recommendation": "<one sentence>"}

Named failure modes you can predict:
- confidence_mirage: agent will assert claims without evidence
- fix_spiral: agent is re-attempting a previously failed fix
- consensus_collapse: agent is about to agree without adding value
- hallucinated_citation: agent may fabricate URLs or references
- scope_creep: agent is drifting beyond the task boundaries
- null: no failure predicted
"""


def create_observer() -> Agent:
    """Factory to create a Metacognitive Observer agent.

    Uses budget-tier model (cheap, fast) since predictions are short JSON.
    """
    llm = create_specialist_llm(max_tokens=256, role="critic", force_tier="budget")
    return Agent(
        role="Metacognitive Observer",
        goal="Predict failure modes in other agents before they produce output.",
        backstory=_OBSERVER_BACKSTORY,
        llm=llm,
        tools=[],  # Observer is read-only — no tools
        max_execution_time=30,
        verbose=False,
    )


def predict_failure(
    agent_id: str,
    task_description: str,
    next_action: str,
    recent_history: list[str],
    mcsv: "MetacognitiveStateVector | None" = None,
) -> dict:
    """Run the Observer on a snapshot and return prediction JSON.

    Returns {"predicted_failure_mode": str|None, "confidence": float,
             "recommendation": str} or a safe default on error.

    This function is called from the PRE_LLM_CALL hook when
    mcsv.requires_observer is True. It's a synchronous LLM call
    (~200ms on budget tier) that gates whether the primary agent proceeds.
    """
    from crewai import Task, Crew, Process

    safe_default = {
        "predicted_failure_mode": None,
        "confidence": 0.0,
        "recommendation": "Observer unavailable — proceed normally.",
    }

    try:
        observer = create_observer()
    except Exception as exc:
        logger.debug(f"observer: creation failed: {exc}")
        return safe_default

    mcsv_str = mcsv.to_context_string() if mcsv else "[MCSV unavailable]"
    history_str = "\n".join(f"- {h[:200]}" for h in recent_history[-5:]) or "(no history)"

    prompt = (
        f"## Observation Snapshot\n"
        f"Agent: {agent_id}\n"
        f"Task: {task_description[:500]}\n"
        f"Next action: {next_action[:300]}\n"
        f"Metacognitive state: {mcsv_str}\n"
        f"Recent history:\n{history_str}\n\n"
        f"Predict the most likely failure mode (or null if no failure predicted)."
    )

    task = Task(
        description=prompt,
        expected_output='JSON: {"predicted_failure_mode": ..., "confidence": ..., "recommendation": ...}',
        agent=observer,
    )
    crew = Crew(agents=[observer], tasks=[task], process=Process.sequential, verbose=False)

    try:
        raw = str(crew.kickoff()).strip()
        # Parse JSON from output (may have markdown fences)
        from app.utils import safe_json_parse
        result, err = safe_json_parse(raw)
        if result and isinstance(result, dict):
            return {
                "predicted_failure_mode": result.get("predicted_failure_mode"),
                "confidence": float(result.get("confidence", 0.0)),
                "recommendation": str(result.get("recommendation", ""))[:200],
            }
        logger.debug(f"observer: unparseable output: {raw[:200]}")
        return safe_default
    except Exception as exc:
        logger.debug(f"observer: prediction failed: {exc}")
        return safe_default
