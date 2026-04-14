"""
blackboard_tool.py — CrewAI tool for the research blackboard (P1).

Two tools:
  deposit_finding — write a claim + evidence + confidence to the blackboard
  read_findings   — semantic search across deposited findings

Uses scoped_memory.store_finding / retrieve_findings under the hood.
"""
from __future__ import annotations

import logging
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class DepositFindingInput(BaseModel):
    claim: str = Field(description="The factual claim or hypothesis.")
    evidence: str = Field(default="", description="Supporting evidence, source URL, or reasoning.")
    confidence: str = Field(
        default="medium",
        description="Confidence level: 'high' (verified by 2+ sources), 'medium' (single source), 'low' (inference/speculation).",
    )
    verification_status: str = Field(
        default="unverified",
        description="'verified' (cross-checked), 'unverified' (single source), or 'contradicted' (conflicts with other findings).",
    )


class DepositFindingTool(BaseTool):
    """Deposit a research finding onto the shared blackboard.

    Use this to record claims, evidence, and hypotheses during research.
    Other agents can read your findings. Include confidence and verification
    status so downstream consumers know how much to trust each finding.
    """

    name: str = "deposit_finding"
    description: str = (
        "Record a research finding on the shared blackboard. Include the claim, "
        "evidence, confidence level (high/medium/low), and verification status. "
        "Other agents will see this finding and can build on it."
    )
    args_schema: Type[BaseModel] = DepositFindingInput

    task_id: str = ""
    agent_name: str = ""

    def _run(
        self,
        claim: str,
        evidence: str = "",
        confidence: str = "medium",
        verification_status: str = "unverified",
    ) -> str:
        from app.memory.scoped_memory import store_finding
        try:
            store_finding(
                task_id=self.task_id,
                claim=claim,
                evidence=evidence,
                confidence=confidence,
                agent=self.agent_name,
                verification_status=verification_status,
            )
            return f"Finding deposited (confidence={confidence}, status={verification_status})."
        except Exception as exc:
            logger.warning(f"blackboard: deposit failed: {exc}")
            return f"Failed to deposit finding: {exc}"


class ReadFindingsInput(BaseModel):
    query: str = Field(description="What to search for in existing findings.")
    confidence_filter: str | None = Field(
        default=None,
        description="Optional: only return 'high', 'medium', or 'low' confidence findings.",
    )
    n: int = Field(default=5, description="Max findings to return.", ge=1, le=20)


class ReadFindingsTool(BaseTool):
    """Read findings from the shared research blackboard.

    Use this to see what other agents have found, including rejected or
    contradictory findings. This enables genuine synthesis — you see ALL
    evidence, not just the polished output.
    """

    name: str = "read_findings"
    description: str = (
        "Search the shared research blackboard for findings from any agent. "
        "Returns claims with their evidence, confidence, and verification status. "
        "Use this to check what's already known before searching the web."
    )
    args_schema: Type[BaseModel] = ReadFindingsInput

    task_id: str = ""

    def _run(
        self,
        query: str,
        confidence_filter: str | None = None,
        n: int = 5,
    ) -> str:
        from app.memory.scoped_memory import retrieve_findings
        try:
            results = retrieve_findings(
                task_id=self.task_id,
                query=query,
                n=n,
                confidence_filter=confidence_filter,
            )
            if not results:
                return "No findings on the blackboard matching your query."

            lines = [f"Found {len(results)} findings:\n"]
            for i, r in enumerate(results, 1):
                text = r.get("document", r.get("text", "(empty)"))
                meta = r.get("metadata", {})
                conf = meta.get("confidence", "?")
                status = meta.get("verification_status", "?")
                agent = meta.get("agent", "?")
                lines.append(
                    f"--- Finding {i} [{conf} / {status}] (from {agent}) ---\n{text}\n"
                )
            return "\n".join(lines)
        except Exception as exc:
            logger.warning(f"blackboard: read failed: {exc}")
            return f"Failed to read findings: {exc}"


def create_blackboard_tools(task_id: str, agent_name: str) -> list[BaseTool]:
    """Create a deposit + read tool pair scoped to a task."""
    deposit = DepositFindingTool()
    deposit.task_id = task_id
    deposit.agent_name = agent_name
    read = ReadFindingsTool()
    read.task_id = task_id
    return [deposit, read]
