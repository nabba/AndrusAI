"""
philosophy/dialectics_tool.py — CrewAI tool for dialectical counter-argument retrieval.

Enables agents to find counter-arguments to philosophical claims by
traversing the Neo4j argument graph.

Assigned to: Self-Improver, Commander, Critic.
Safety: READ-ONLY — no agent can modify the dialectical graph via tools.

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import logging
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class FindCounterArgumentInput(BaseModel):
    """Input schema for FindCounterArgumentTool."""

    claim: str = Field(
        description=(
            "The philosophical claim or position to find counter-arguments for. "
            "Be specific — e.g., 'Virtue is sufficient for happiness' or "
            "'The ends justify the means'."
        )
    )
    n_results: int = Field(
        default=3,
        description="Number of counter-arguments to return (1-5).",
        ge=1,
        le=5,
    )


class FindCounterArgumentTool(BaseTool):
    """Find counter-arguments to a philosophical claim.

    Uses the Neo4j dialectical graph to traverse
    Claim → COUNTERED_BY → CounterClaim (→ SYNTHESIZED_INTO → Synthesis)
    relationships built from the philosophy corpus.

    This tool helps agents:
    - Challenge their own reasoning
    - Consider opposing viewpoints
    - Find dialectical syntheses that transcend simple contradictions
    """

    name: str = "find_counter_argument"
    description: str = (
        "Find philosophical counter-arguments to a given claim or position. "
        "Uses a graph of argumentative relationships extracted from the "
        "philosophy corpus. Returns counter-claims with their sources and "
        "any available syntheses. Use this to challenge reasoning, consider "
        "opposing views, or find dialectical resolutions."
    )
    args_schema: Type[BaseModel] = FindCounterArgumentInput

    def _run(self, claim: str, n_results: int = 3) -> str:
        """Execute the counter-argument search."""
        from app.philosophy.dialectics import get_graph

        graph = get_graph()
        results = graph.find_counter_arguments(claim, n=n_results)

        if not results:
            # Fallback: try a direct dialectical chain search.
            chains = graph.find_dialectical_chain(claim, n=n_results)
            if chains:
                parts = [
                    f"No direct counter-arguments found, but {len(chains)} "
                    f"dialectical chains relate to this topic:\n"
                ]
                for i, chain in enumerate(chains, 1):
                    parts.append(
                        f"--- Chain {i} ---\n"
                        f"Claim ({chain.get('claim_tradition', '?')}): "
                        f"{chain['claim'][:300]}\n"
                        f"Counter ({chain.get('counter_tradition', '?')}): "
                        f"{chain['counter_claim'][:300]}\n"
                    )
                    if chain.get("synthesis"):
                        parts.append(f"Synthesis: {chain['synthesis'][:300]}\n")
                return "\n".join(parts)

            return (
                f"No counter-arguments found for: '{claim[:100]}'. "
                f"The dialectical graph may not cover this area yet, "
                f"or Neo4j may be unavailable."
            )

        parts = [f"Found {len(results)} counter-argument(s):\n"]
        for i, r in enumerate(results, 1):
            parts.append(
                f"--- Counter-argument {i} ---\n"
                f"Tradition: {r.get('tradition', 'Unknown')}\n"
                f"Source: {r.get('source', 'Unknown')}\n"
                f"Counter-claim: {r['counter_claim'][:500]}\n"
            )
            if r.get("synthesis"):
                parts.append(f"Synthesis: {r['synthesis'][:300]}\n")

        return "\n".join(parts)
