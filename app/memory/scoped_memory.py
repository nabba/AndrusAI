"""
scoped_memory.py — Hierarchical scoped memory layer.

Wraps chromadb_manager with a scope-based naming convention and dual
retrieval profiles (operational vs. strategic).  Does NOT replace
the existing store/retrieve API — adds a higher-level layer on top.

Scope hierarchy:
  scope_team              — team-wide decisions, task status, beliefs
  scope_agent_{name}      — per-agent private working memory
  scope_project_{name}    — per-project accumulated knowledge
  scope_policies          — improvement policies (Phase 4)
  scope_beliefs           — belief state tracking (Phase 2)
"""

import logging
from datetime import datetime, timezone
from app.memory.chromadb_manager import (
    store,
    retrieve,
    retrieve_with_metadata,
    retrieve_filtered,
)

logger = logging.getLogger(__name__)


# ── Scoped store ──────────────────────────────────────────────────────────────

def store_scoped(
    scope: str,
    text: str,
    metadata: dict = None,
    importance: str = "normal",
) -> None:
    """Store text in a scoped collection with timestamp and importance."""
    meta = dict(metadata) if metadata else {}
    meta["scope"] = scope
    meta["importance"] = importance
    meta["ts"] = datetime.now(timezone.utc).isoformat()
    store(scope, text, meta)


def store_team_decision(text: str, importance: str = "normal") -> None:
    """Store a team-level decision or shared fact."""
    store_scoped("scope_team", text, {"type": "decision"}, importance)


def store_agent_memory(agent_name: str, text: str) -> None:
    """Store in an agent's private memory scope."""
    store_scoped(f"scope_agent_{agent_name}", text, {"agent": agent_name})


def store_project_memory(project_name: str, text: str, importance: str = "normal") -> None:
    """Store knowledge specific to a project."""
    store_scoped(f"scope_project_{project_name}", text, {"project": project_name}, importance)


# ── Research Blackboard ──────────────────────────────────────────────────────
#
# Long-lived shared workspace where agents deposit partial findings,
# hypotheses, and evidence. Unlike task-output chaining (where downstream
# agents only see the final output), the blackboard gives every agent
# access to ALL findings including rejected, low-confidence, and
# contradictory ones — enabling genuine synthesis.
#
# Integration with existing systems:
#   - Lives in ChromaDB via store_scoped (same plumbing as all other scopes)
#   - Findings carry structured metadata (confidence, source, status, agent)
#   - Semantic retrieval lets agents query by topic, not just by task_id
#   - Findings promoted to knowledge base when verified + high-confidence
#   - Mem0 cross-writes ensure long-term agent memory includes key findings

def store_finding(
    task_id: str,
    claim: str,
    evidence: str = "",
    confidence: str = "medium",
    source: str = "",
    agent: str = "",
    verification_status: str = "unverified",
) -> None:
    """Deposit a research finding onto the blackboard.

    Args:
        task_id: Research task identifier (groups related findings).
        claim: The factual claim or hypothesis.
        evidence: Supporting evidence or reasoning.
        confidence: "high" | "medium" | "low".
        source: URL, document, or tool that produced the evidence.
        agent: Which agent deposited this finding.
        verification_status: "verified" | "unverified" | "contradicted".
    """
    scope = f"scope_research_bb--{task_id}"
    text = f"{claim}\n\nEvidence: {evidence}" if evidence else claim
    metadata = {
        "task_id": task_id,
        "confidence": confidence,
        "source": source[:500] if source else "",
        "agent": agent,
        "verification_status": verification_status,
        "type": "finding",
    }
    store_scoped(scope, text, metadata, importance="high" if confidence == "high" else "normal")

    # Cross-write to Mem0 for long-term agent memory
    if confidence == "high" and agent:
        try:
            from app.memory.mem0_manager import get_manager
            mgr = get_manager()
            mgr.add(text[:500], user_id=agent, metadata={"type": "research_finding", "task_id": task_id})
        except Exception:
            pass


def retrieve_findings(
    task_id: str,
    query: str = "",
    n: int = 10,
    confidence_filter: str | None = None,
) -> list[dict]:
    """Read findings from the blackboard.

    Returns list of dicts with keys: text, metadata (confidence, source,
    agent, verification_status, ts).

    Args:
        task_id: Scope to a specific research task, or "" for all.
        query: Semantic query (empty = all findings for task_id).
        n: Max results.
        confidence_filter: If set, only return findings with this confidence.
    """
    scope = f"scope_research_bb--{task_id}" if task_id else "scope_research_bb"
    if not query:
        query = "research findings"  # default query for broad retrieval

    if confidence_filter:
        # retrieve_filtered returns list[str]; wrap as list[dict] for consistency
        raw = retrieve_filtered(scope, query, where={"confidence": confidence_filter}, n=n)
        return [{"text": t, "metadata": {"confidence": confidence_filter}} for t in raw]
    return retrieve_with_metadata(scope, query, n=n)


def promote_to_knowledge_base(task_id: str, project: str = "system") -> int:
    """Promote high-confidence verified findings to the project knowledge base.

    Returns count of promoted findings. Called at the end of a research run
    to bridge the ephemeral blackboard into long-lived project memory.
    """
    findings = retrieve_findings(task_id, n=50, confidence_filter="high")
    promoted = 0
    for f in findings:
        meta = f.get("metadata", {})
        if meta.get("verification_status") == "verified":
            text = f.get("document", f.get("text", ""))
            if text:
                store_project_memory(project, text, importance="high")
                promoted += 1
    return promoted


# ── Scoped retrieve ──────────────────────────────────────────────────────────

def retrieve_scoped(scope: str, query: str, n: int = 5) -> list[str]:
    """Basic scoped retrieval (same as regular retrieve but scope-named)."""
    return retrieve(scope, query, n)


def retrieve_operational(scope: str, query: str, n: int = 10) -> list[str]:
    """Operational profile: favors recent items.

    Retrieves more results than needed, then re-ranks by combining
    semantic similarity with recency, returning the top-n.
    """
    items = retrieve_with_metadata(scope, query, n=min(n * 3, 50))
    if not items:
        return []

    now = datetime.now(timezone.utc)
    scored = []
    for item in items:
        # Similarity score: ChromaDB distances are L2 — lower = more similar
        # Invert to get a similarity score
        sim_score = max(0, 1.0 - item["distance"] / 2.0)

        # Recency score: 1.0 for items from last hour, decays over days
        recency_score = 0.5  # default if no timestamp
        ts_str = item.get("metadata", {}).get("ts", "")
        if ts_str:
            try:
                item_time = datetime.fromisoformat(ts_str)
                age_hours = max(0, (now - item_time).total_seconds() / 3600)
                # Exponential decay: half-life of 24 hours
                recency_score = 2 ** (-age_hours / 24.0)
            except (ValueError, TypeError):
                pass

        # Combined score: 50% similarity + 50% recency
        combined = 0.5 * sim_score + 0.5 * recency_score
        scored.append((combined, item["document"]))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [doc for _, doc in scored[:n]]


def retrieve_strategic(scope: str, query: str, n: int = 5) -> list[str]:
    """Strategic profile: favors important items.

    Filters to items with importance >= 'high', falls back to unfiltered
    if no high-importance items exist.
    """
    # Try filtered first
    results = retrieve_filtered(
        scope, query, where={"importance": "high"}, n=n
    )
    if results:
        return results

    # Fallback: try 'critical' importance
    results = retrieve_filtered(
        scope, query, where={"importance": "critical"}, n=n
    )
    if results:
        return results

    # Last resort: unfiltered
    return retrieve(scope, query, n)
