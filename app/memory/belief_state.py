"""
belief_state.py — ProAgent-style belief state tracking.

Maintains explicit beliefs about each agent's current state, updated
after every observation.  This implements Theory of Mind / intention
inference from ProAgent (Zhang et al. AAAI 2024).

Beliefs are stored in the 'scope_beliefs' ChromaDB collection as JSON
documents with per-agent metadata for filtering.
"""

import json
import logging
from datetime import datetime, timezone
from app.memory.chromadb_manager import store, retrieve_with_metadata

logger = logging.getLogger(__name__)

BELIEFS_COLLECTION = "scope_beliefs"


def update_belief(
    agent_name: str,
    state: str,
    current_task: str = "",
    confidence: str = "medium",
    observations: list[str] = None,
    needs: list[str] = None,
) -> None:
    """Store or update a belief about an agent's current state.

    Args:
        agent_name: The agent being observed (e.g. "researcher", "coder")
        state: One of "idle", "working", "blocked", "completed", "failed"
        current_task: What the agent is currently doing
        confidence: The agent's self-reported confidence level
        observations: Recent observations about this agent
        needs: What this agent needs from teammates
    """
    belief = {
        "agent": agent_name,
        "state": state,
        "current_task": current_task[:300],
        "confidence": confidence,
        "observations": (observations or [])[-5:],  # Keep last 5
        "needs": (needs or [])[-5:],
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }

    belief_text = json.dumps(belief)
    metadata = {
        "agent": agent_name,
        "state": state,
        "type": "belief_state",
        "ts": belief["last_updated"],
    }

    try:
        store(BELIEFS_COLLECTION, belief_text, metadata)
    except Exception:
        logger.warning(f"Failed to store belief for {agent_name}", exc_info=True)


def get_beliefs(agent_name: str = None) -> list[dict]:
    """Retrieve current beliefs about agent(s).

    If agent_name is provided, returns beliefs about that specific agent.
    Otherwise returns beliefs about all agents.
    """
    query = f"agent state {agent_name}" if agent_name else "agent state beliefs"
    items = retrieve_with_metadata(BELIEFS_COLLECTION, query, n=20)
    if not items:
        return []

    beliefs = []
    seen_agents = set()
    for item in items:
        try:
            belief = json.loads(item["document"])
            agent = belief.get("agent", "")

            # Filter by agent if specified
            if agent_name and agent != agent_name:
                continue

            # Only keep the most recent belief per agent
            if agent in seen_agents:
                continue
            seen_agents.add(agent)
            beliefs.append(belief)
        except (json.JSONDecodeError, KeyError):
            continue

    return beliefs


def get_team_state_summary() -> str:
    """Return a formatted summary of all agents' current states.

    Used by the Commander to understand team status before routing.
    """
    beliefs = get_beliefs()
    if not beliefs:
        return ""

    lines = ["TEAM STATE:"]
    for b in beliefs:
        agent = b.get("agent", "unknown")
        state = b.get("state", "unknown")
        task = b.get("current_task", "")
        confidence = b.get("confidence", "")
        needs = b.get("needs", [])

        line = f"  - {agent}: {state}"
        if task:
            line += f" | task: {task[:60]}"
        if confidence:
            line += f" | confidence: {confidence}"
        if needs:
            line += f" | needs: {', '.join(needs[:3])}"
        lines.append(line)

    return "\n".join(lines)


def revise_beliefs(observation: str, agent_name: str) -> None:
    """Store an observation that may change beliefs about an agent.

    This is called when one agent observes something about another
    agent's output or behavior.
    """
    metadata = {
        "type": "observation",
        "about": agent_name,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    try:
        store(BELIEFS_COLLECTION, observation[:500], metadata)
    except Exception:
        logger.warning(f"Failed to store observation about {agent_name}", exc_info=True)
