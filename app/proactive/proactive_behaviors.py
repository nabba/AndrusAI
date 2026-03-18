"""
proactive_behaviors.py — Role-specific proactive behavior definitions.

Defines what triggers each agent type should respond to and what
actions they should take when a trigger is detected.
"""

PROACTIVE_BEHAVIORS = {
    "commander": {
        "triggers": ["plan_drift", "crew_failure", "conflicting_results"],
        "actions": {
            "plan_drift": "Re-evaluate the task plan. Check if crew results match the original intent. If drifting, consider dispatching a corrective crew.",
            "crew_failure": "Analyze the failure. Check if the task should be retried, re-routed to a different crew, or simplified.",
            "conflicting_results": "Compare conflicting outputs. Determine which is more reliable based on sources and confidence. Flag the conflict for the user.",
        },
    },
    "researcher": {
        "triggers": ["contradictory_sources", "low_confidence", "missing_data"],
        "actions": {
            "contradictory_sources": "Flag the contradiction in team memory. Search for additional sources to resolve the conflict.",
            "low_confidence": "Perform a focused verification search on the low-confidence claims. Use at least 2 additional sources.",
            "missing_data": "Identify what specific data is missing and request it from the appropriate teammate via team memory.",
        },
    },
    "critic": {
        "triggers": ["quality_drift", "repeated_error_pattern", "unjustified_confidence"],
        "actions": {
            "quality_drift": "Perform a deeper review focusing on the areas showing quality decline. Compare against previous high-quality outputs.",
            "repeated_error_pattern": "Flag the pattern for the retrospective crew. Document the pattern in team memory with suggested preventive measures.",
            "unjustified_confidence": "Challenge the high-confidence claim with specific questions. Request evidence or additional verification.",
        },
    },
    "coder": {
        "triggers": ["test_failure", "security_concern", "incomplete_implementation"],
        "actions": {
            "test_failure": "Run additional targeted tests. Check edge cases and error handling. Document which tests failed and why.",
            "security_concern": "Flag the security issue in team memory. Suggest specific mitigation steps.",
            "incomplete_implementation": "Identify which requirements are not yet met. Prioritize and address the most critical gaps.",
        },
    },
}


def get_proactive_prompt(role: str, trigger_type: str) -> str:
    """Return a task description for a proactive action.

    Args:
        role: The agent role (e.g., 'researcher', 'critic')
        trigger_type: The type of trigger detected

    Returns:
        A task description string, or empty string if no match.
    """
    behaviors = PROACTIVE_BEHAVIORS.get(role, {})
    actions = behaviors.get("actions", {})
    action = actions.get(trigger_type, "")
    if not action:
        return ""

    return (
        f"PROACTIVE ACTION ({trigger_type}):\n\n"
        f"{action}\n\n"
        f"This is an unsolicited helpful action triggered by detecting "
        f"a {trigger_type} condition. Focus on being concise and actionable."
    )
