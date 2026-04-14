# EVOLVE-BLOCK-START
"""
AndrusAI agent tool optimization — evolved by ShinkaEvolve.

This module contains the evolvable logic for agent tool selection,
response formatting, and task routing. ShinkaEvolve will mutate
this code to improve composite_score performance.
"""


def select_tool_strategy(task_description: str, available_tools: list[str]) -> str:
    """Select the best tool for a given task.

    Args:
        task_description: Natural language description of the task.
        available_tools: List of available tool names.

    Returns:
        Name of the recommended tool, or empty string for no tool.
    """
    task_lower = task_description.lower()

    # Web search for research/factual questions
    if any(kw in task_lower for kw in ("search", "find", "latest", "current", "news", "what is")):
        if "web_search" in available_tools:
            return "web_search"

    # Code execution for coding tasks
    if any(kw in task_lower for kw in ("write code", "function", "implement", "debug", "fix bug")):
        if "code_executor" in available_tools:
            return "code_executor"

    # File operations
    if any(kw in task_lower for kw in ("read file", "write file", "save", "load", "open")):
        if "file_manager" in available_tools:
            return "file_manager"

    # Memory for context-dependent tasks
    if any(kw in task_lower for kw in ("remember", "recall", "last time", "previously")):
        if "memory_search" in available_tools:
            return "memory_search"

    return ""


def format_response(raw_response: str, task_type: str) -> str:
    """Post-process agent response for quality and consistency.

    Args:
        raw_response: The raw LLM response text.
        task_type: One of 'research', 'coding', 'writing'.

    Returns:
        Formatted response string.
    """
    if not raw_response:
        return raw_response

    response = raw_response.strip()

    # Remove common LLM artifacts
    prefixes_to_strip = [
        "Sure, ", "Sure! ", "Of course! ", "Certainly! ",
        "Here's ", "Here is ", "I'd be happy to ",
    ]
    for prefix in prefixes_to_strip:
        if response.startswith(prefix):
            response = response[len(prefix):]
            break

    # Coding responses: ensure code blocks are properly formatted
    if task_type == "coding" and "```" not in response and "def " in response:
        # Wrap bare code in markdown code block
        response = f"```python\n{response}\n```"

    return response


def route_task(task_description: str) -> str:
    """Determine which crew should handle a task.

    Args:
        task_description: Natural language task description.

    Returns:
        Crew name: 'research', 'coding', or 'writing'.
    """
    task_lower = task_description.lower()

    coding_signals = [
        "write a function", "implement", "code", "debug", "fix bug",
        "refactor", "optimize", "algorithm", "data structure",
        "class", "method", "api endpoint",
    ]
    if any(signal in task_lower for signal in coding_signals):
        return "coding"

    research_signals = [
        "what is", "explain", "how does", "compare", "difference between",
        "find", "search", "look up", "research", "analyze",
    ]
    if any(signal in task_lower for signal in research_signals):
        return "research"

    return "writing"


# EVOLVE-BLOCK-END


# ── Fixed evaluation harness (not evolved) ──────────────────────────────────

_TEST_CASES = [
    # (task_description, expected_tool, available_tools)
    ("Search for the latest Python release", "web_search", ["web_search", "code_executor"]),
    ("Write a function to sort a list", "code_executor", ["web_search", "code_executor"]),
    ("Save this data to a file", "file_manager", ["file_manager", "web_search"]),
    ("What did we discuss last time?", "memory_search", ["memory_search", "web_search"]),
    ("Tell me a joke", "", ["web_search", "code_executor"]),
]

_ROUTING_CASES = [
    ("Write a Python function for binary search", "coding"),
    ("What is the CAP theorem?", "research"),
    ("Write a professional email", "writing"),
    ("Implement a linked list", "coding"),
    ("How does DNS work?", "research"),
]


def run_evaluation():
    """Run the fixed test suite and return metrics.

    Returns:
        Tuple of (combined_score, details_dict)
    """
    tool_correct = 0
    tool_total = len(_TEST_CASES)
    for task, expected, tools in _TEST_CASES:
        result = select_tool_strategy(task, tools)
        if result == expected:
            tool_correct += 1

    route_correct = 0
    route_total = len(_ROUTING_CASES)
    for task, expected in _ROUTING_CASES:
        result = route_task(task)
        if result == expected:
            route_correct += 1

    tool_accuracy = tool_correct / max(1, tool_total)
    route_accuracy = route_correct / max(1, route_total)

    # Combined score: weighted average
    combined = 0.5 * tool_accuracy + 0.5 * route_accuracy

    return combined, {
        "tool_accuracy": tool_accuracy,
        "route_accuracy": route_accuracy,
        "tool_correct": tool_correct,
        "tool_total": tool_total,
        "route_correct": route_correct,
        "route_total": route_total,
    }
