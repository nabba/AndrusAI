"""
scoped_memory_tool.py — BaseTool wrappers for the scoped memory system.

Provides agents with tools to store/retrieve from scoped memory, record
team decisions, update belief states, and view team status.
"""

from crewai.tools import BaseTool
from pydantic import Field
from app.memory.scoped_memory import (
    store_scoped,
    retrieve_scoped,
    retrieve_operational,
    store_team_decision,
)
from app.memory.belief_state import (
    update_belief,
    get_team_state_summary,
)


class ScopedMemoryStoreTool(BaseTool):
    name: str = "scoped_memory_store"
    description: str = (
        "Store information in a scoped memory collection. "
        "Args: text (str) - content to store, "
        "scope (str) - memory scope like 'scope_team' or 'scope_agent_researcher', "
        "importance (str) - 'normal', 'high', or 'critical' (optional, default 'normal')."
    )
    default_scope: str = Field(default="scope_team")

    def _run(self, text: str, scope: str = "", importance: str = "normal") -> str:
        scope = scope.strip() or self.default_scope
        importance = importance.strip().lower()
        if importance not in ("normal", "high", "critical"):
            importance = "normal"
        store_scoped(scope, text, importance=importance)
        return f"Stored in {scope} (importance: {importance}): {text[:100]}..."


class ScopedMemoryRetrieveTool(BaseTool):
    name: str = "scoped_memory_retrieve"
    description: str = (
        "Retrieve relevant information from a scoped memory collection. "
        "Uses recency-weighted retrieval for operational queries. "
        "Args: query (str) - search query, "
        "scope (str) - memory scope to search (optional, default 'scope_team')."
    )
    default_scope: str = Field(default="scope_team")

    def _run(self, query: str, scope: str = "") -> str:
        scope = scope.strip() or self.default_scope
        results = retrieve_operational(scope, query, n=5)
        if not results:
            return f"No relevant memories found in {scope}."
        return "\n\n---\n\n".join(results)


class TeamDecisionTool(BaseTool):
    name: str = "team_decision"
    description: str = (
        "Record a team-level decision or shared conclusion that all agents should know. "
        "Use this for agreed decisions, key findings, or shared context. "
        "Args: text (str) - the decision or shared fact, "
        "importance (str) - 'normal', 'high', or 'critical' (optional)."
    )

    def _run(self, text: str, importance: str = "normal") -> str:
        importance = importance.strip().lower()
        if importance not in ("normal", "high", "critical"):
            importance = "normal"
        store_team_decision(text, importance)
        return f"Team decision recorded (importance: {importance}): {text[:100]}..."


class BeliefUpdateTool(BaseTool):
    name: str = "update_team_belief"
    description: str = (
        "Update your belief about a teammate's current state. "
        "Use this to track what other agents are doing, their progress, and needs. "
        "Args: agent_name (str) - name of the agent (e.g. 'researcher', 'coder'), "
        "state (str) - one of 'idle', 'working', 'blocked', 'completed', 'failed', "
        "current_task (str) - what the agent is doing (optional), "
        "confidence (str) - their confidence level (optional), "
        "needs (str) - comma-separated needs from teammates (optional)."
    )

    def _run(
        self,
        agent_name: str,
        state: str,
        current_task: str = "",
        confidence: str = "medium",
        needs: str = "",
    ) -> str:
        needs_list = [n.strip() for n in needs.split(",") if n.strip()] if needs else []
        update_belief(
            agent_name=agent_name.strip(),
            state=state.strip().lower(),
            current_task=current_task,
            confidence=confidence.strip().lower(),
            needs=needs_list,
        )
        return f"Belief updated: {agent_name} is {state}"


class TeamStateTool(BaseTool):
    name: str = "team_state"
    description: str = (
        "View the current state of all team members. "
        "Shows what each agent is doing, their confidence, and any needs. "
        "No arguments needed."
    )

    def _run(self, **kwargs) -> str:
        summary = get_team_state_summary()
        return summary if summary else "No team state information available yet."


def create_scoped_memory_tools(agent_name: str) -> list:
    """Factory to create scoped memory tools configured for a specific agent.

    Returns tools for: scoped store/retrieve, team decisions,
    belief updates, and team state viewing.
    """
    return [
        ScopedMemoryStoreTool(default_scope=f"scope_agent_{agent_name}"),
        ScopedMemoryRetrieveTool(default_scope=f"scope_agent_{agent_name}"),
        TeamDecisionTool(),
        BeliefUpdateTool(),
        TeamStateTool(),
    ]
