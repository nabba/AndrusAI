"""
reflection_tool.py — Tool for agents to store structured post-task reflections.

After completing a task, agents use this tool to record what went well,
what went wrong, and what they'd do differently.  This implements
situational awareness via memory (SAD benchmark): the agent understands
its own execution history.
"""

import json
import logging
from datetime import datetime, timezone
from crewai.tools import BaseTool
from pydantic import Field
from app.memory.chromadb_manager import store, store_team

logger = logging.getLogger(__name__)


class ReflectionTool(BaseTool):
    name: str = "store_reflection"
    description: str = (
        "Store a structured reflection after completing a task. "
        "Use this to record lessons learned for future runs. "
        "Args: task_description (str) - what the task was, "
        "what_went_well (str) - successes and strengths, "
        "what_went_wrong (str) - failures and weaknesses, "
        "lesson_learned (str) - key takeaway for future, "
        "would_do_differently (str) - optional improvements."
    )
    agent_role: str = Field(default="default")

    def _run(
        self,
        task_description: str,
        what_went_well: str = "",
        what_went_wrong: str = "",
        lesson_learned: str = "",
        would_do_differently: str = "",
    ) -> str:
        reflection = {
            "role": self.agent_role,
            "task": task_description[:300],
            "went_well": what_went_well[:300],
            "went_wrong": what_went_wrong[:300],
            "lesson": lesson_learned[:300],
            "would_change": would_do_differently[:300],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        reflection_text = json.dumps(reflection)
        metadata = {
            "role": self.agent_role,
            "type": "reflection",
            "ts": reflection["timestamp"],
        }

        # Store in agent-specific collection
        agent_collection = f"reflections_{self.agent_role}"
        try:
            store(agent_collection, reflection_text, metadata)
        except Exception:
            logger.warning(
                f"Failed to store reflection in {agent_collection}", exc_info=True
            )

        # Also store in shared team memory so other agents can learn
        try:
            store_team(reflection_text, metadata)
        except Exception:
            logger.warning("Failed to store reflection in team memory", exc_info=True)

        return f"Reflection stored: {reflection_text}"
