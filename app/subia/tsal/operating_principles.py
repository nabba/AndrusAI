"""Operating-principles inference (TSAL §6).

The ONE TSAL component that requires an LLM (Tier-1, ~500 tokens,
weekly). Pure function: takes a TechnicalSelfModel + injected
predict_fn callable and returns a 300-word principles description.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from .self_model import TechnicalSelfModel

logger = logging.getLogger(__name__)


def _build_prompt(model: TechnicalSelfModel) -> str:
    cb = model.codebase
    cmp = model.components
    return (
        "Based on this discovered system architecture, describe in 300 words "
        "how the system operates. Focus on: (1) information flow inputs→processing→outputs, "
        "(2) how agents coordinate (orchestrator), (3) how knowledge compounds "
        "(persistence between operations), (4) what safety constraints exist, "
        "(5) what self-awareness mechanisms are active. Write as factual "
        "description of operating logic, not aspirational.\n\n"
        f"Codebase: {cb.total_modules} modules, {cb.total_lines:,} lines\n"
        f"Detected patterns: {cb.patterns_detected}\n"
        f"Agents: {[a.get('role') if isinstance(a, dict) else a for a in cb.agents][:8]}\n"
        f"Tools: {[t.get('name') for t in cb.tools][:12]}\n"
        f"ChromaDB collections: {[c['name'] for c in cmp.chromadb.collections]}\n"
        f"Neo4j: {cmp.neo4j.node_count} nodes / {cmp.neo4j.relation_count} rels\n"
        f"Wiki sections: {sorted(cmp.wiki.pages_by_section)}\n"
        f"Cascade tiers: {[t['name'] for t in cmp.cascade.tiers]}\n"
        f"SubIA active: {cmp.subia_active}\n"
    )


def infer_operating_principles(
    model: TechnicalSelfModel,
    *,
    predict_fn: Optional[Callable[[str], str]] = None,
    max_tokens: int = 500,
) -> str:
    """Return inferred operating principles, or "" on failure.

    `predict_fn(prompt) -> str` is injected so callers can pin a Tier-1
    model. If None, returns an empty string and the caller renders a
    placeholder page (the model still serializes safely).
    """
    if predict_fn is None:
        return ""
    prompt = _build_prompt(model)
    try:
        out = predict_fn(prompt) or ""
    except Exception as exc:
        logger.debug("tsal: operating-principles LLM call failed: %s", exc)
        return ""
    return out.strip()[: max_tokens * 6]  # rough char cap
