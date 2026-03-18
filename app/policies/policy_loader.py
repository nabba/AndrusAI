"""
policy_loader.py — Policy storage, loading, and statistics.

Policies are stored in the scope_policies ChromaDB collection.
Before each task run, relevant policies are loaded and injected
into the agent's task description as active guidelines.
"""

import json
import logging
from app.memory.chromadb_manager import retrieve, retrieve_with_metadata
from app.memory.scoped_memory import store_scoped, retrieve_strategic

logger = logging.getLogger(__name__)

POLICIES_SCOPE = "scope_policies"


def store_policy(
    trigger: str,
    action: str,
    evidence: str,
    scope: str = "",
) -> None:
    """Store an improvement policy in the policies scope."""
    policy = {
        "trigger": trigger[:300],
        "action": action[:500],
        "evidence": evidence[:500],
        "scope": scope,
    }
    policy_text = json.dumps(policy)
    store_scoped(
        POLICIES_SCOPE,
        policy_text,
        metadata={
            "type": "policy",
            "trigger_preview": trigger[:100],
        },
        importance="high",
    )


def load_relevant_policies(
    task_description: str,
    agent_role: str = "",
    n: int = 5,
) -> str:
    """Load policies relevant to the current task and format for injection.

    Args:
        task_description: The task being executed
        agent_role: The role of the agent (for role-specific filtering)
        n: Maximum number of policies to return

    Returns:
        Formatted policy block for injection into task description,
        or empty string if no policies found.
    """
    # Query combines task description with role for better matching
    query = f"{agent_role} {task_description[:200]}" if agent_role else task_description[:300]

    # Use strategic retrieval (importance-weighted)
    results = retrieve_strategic(POLICIES_SCOPE, query, n=n)
    if not results:
        return ""

    policies = []
    for i, doc in enumerate(results, 1):
        try:
            policy = json.loads(doc)
            trigger = policy.get("trigger", "Unknown trigger")
            action = policy.get("action", "Unknown action")
            policies.append(
                f"[{i}] TRIGGER: {trigger}\n"
                f"    ACTION: {action}"
            )
        except (json.JSONDecodeError, KeyError):
            # Raw text policy (not JSON)
            policies.append(f"[{i}] {doc[:200]}")

    if not policies:
        return ""

    return (
        "ACTIVE POLICIES (from past experience — apply when relevant):\n"
        + "\n".join(policies)
        + "\n"
    )


def get_all_policies() -> list[dict]:
    """Retrieve all policies for display or analysis."""
    items = retrieve_with_metadata(POLICIES_SCOPE, "improvement policy", n=50)
    policies = []
    for item in items:
        try:
            policy = json.loads(item["document"])
            policy["metadata"] = item.get("metadata", {})
            policies.append(policy)
        except (json.JSONDecodeError, KeyError):
            policies.append({
                "trigger": "Unknown",
                "action": item.get("document", "")[:200],
                "evidence": "",
                "metadata": item.get("metadata", {}),
            })
    return policies


def get_policy_stats() -> dict:
    """Get statistics about stored policies."""
    policies = get_all_policies()
    return {
        "total_policies": len(policies),
        "sample_triggers": [
            p.get("trigger", "")[:60] for p in policies[:5]
        ],
    }


def format_policies_for_display(max_policies: int = 10) -> str:
    """Format policies for human-readable display (e.g., Signal command)."""
    policies = get_all_policies()[:max_policies]
    if not policies:
        return "No improvement policies stored yet."

    lines = [f"Improvement Policies ({len(policies)} shown):\n"]
    for i, p in enumerate(policies, 1):
        trigger = p.get("trigger", "Unknown")[:60]
        action = p.get("action", "Unknown")[:80]
        lines.append(f"#{i} TRIGGER: {trigger}")
        lines.append(f"   ACTION: {action}")
        lines.append("")

    return "\n".join(lines)
