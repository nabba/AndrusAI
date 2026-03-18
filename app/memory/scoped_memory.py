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
