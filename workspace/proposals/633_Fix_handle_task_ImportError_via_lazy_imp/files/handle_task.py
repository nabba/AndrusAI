from crewai import Task as CrewTask  # unchanged
# REMOVED: from agents.coding_agent import CodingAgent
# REMOVED: from agents.base_agent import BaseAgent

___

# Inside _execute_coding() or similar function:
# ADD lazy import to break cycle:
def _execute_coding(args: dict, context: dict) -> str:
    # Lazy import to avoid circular dependency at module load time
    from agents.coding_agent import CodingAgent
    from agents.base_agent import BaseAgent
    
    # ... rest of function unchanged