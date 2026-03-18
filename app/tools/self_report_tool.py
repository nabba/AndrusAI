"""
self_report_tool.py — Tool for agents to assess their own confidence,
completeness, blockers, and risks.

Implements meta-cognitive monitoring (Li et al. 2025): the agent can
evaluate and report on its own performance mid-task.
"""

import json
import logging
from datetime import datetime, timezone
from crewai.tools import BaseTool
from pydantic import Field
from app.memory.chromadb_manager import store

logger = logging.getLogger(__name__)

SELF_REPORTS_COLLECTION = "self_reports"


class SelfReportTool(BaseTool):
    name: str = "self_report"
    description: str = (
        "Report your current confidence, completeness, blockers, and risks for a task. "
        "Use this after completing major work to assess your own performance. "
        "Args: task_summary (str) - brief description of what you did, "
        "confidence (str) - 'high', 'medium', or 'low', "
        "completeness (str) - 'complete', 'partial', or 'failed', "
        "blockers (str) - what you're stuck on (optional), "
        "risks (str) - what could go wrong (optional), "
        "needs_from_team (str) - what you need from teammates (optional)."
    )
    agent_role: str = Field(default="default")

    def _run(
        self,
        task_summary: str,
        confidence: str = "medium",
        completeness: str = "complete",
        blockers: str = "",
        risks: str = "",
        needs_from_team: str = "",
    ) -> str:
        # Normalize values
        confidence = confidence.lower().strip()
        if confidence not in ("high", "medium", "low"):
            confidence = "medium"
        completeness = completeness.lower().strip()
        if completeness not in ("complete", "partial", "failed"):
            completeness = "partial"

        report = {
            "role": self.agent_role,
            "task_summary": task_summary[:500],
            "confidence": confidence,
            "completeness": completeness,
            "blockers": blockers[:300] if blockers else "",
            "risks": risks[:300] if risks else "",
            "needs_from_team": needs_from_team[:300] if needs_from_team else "",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        report_text = json.dumps(report)

        # Store in ChromaDB for retrieval by other agents and the retrospective crew
        try:
            store(
                SELF_REPORTS_COLLECTION,
                report_text,
                {
                    "role": self.agent_role,
                    "confidence": confidence,
                    "completeness": completeness,
                    "ts": report["timestamp"],
                },
            )
        except Exception:
            logger.warning("Failed to store self-report in memory", exc_info=True)

        return f"Self-report recorded: {report_text}"


def create_self_report_tool(role: str) -> SelfReportTool:
    """Factory to create a self-report tool configured for a specific agent role."""
    return SelfReportTool(agent_role=role)
