"""
app.trajectory.context_builder — Compose a "relevant prior learnings"
block from trajectory-sourced tips for injection into crew prompts.

The helper `compose_trajectory_hint_block` is called from the commander
right after the Observer fires. It:

  1. Queries the RetrievalOrchestrator across the four KBs, filtered
     by (agent_role, tip_type) with the Observer's predicted failure
     mode narrowing to recovery tips when fix_spiral is suspected.
  2. Formats the top-k results as a concise Markdown block, wrapped in
     a tag that signals "supplementary guidance — not the task".
  3. Returns "" when the flag is off, no tips match, or anything errors.

No LLM calls here — pure retrieval + formatting.

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# KB collection names the Integrator writes to (per app.self_improvement
# and the KB packages). These are the underlying Chroma collection names
# — unchanged from the broader KB architecture.
_KB_COLLECTIONS = [
    "episteme_research",
    "experiential_journal",
    "aesthetic_patterns",
    "unresolved_tensions",
]

# Cap how many tip snippets we surface to the crew. Too many dilutes the
# signal; too few wastes the filter. Six is a modest default matching
# the orchestrator's RERANK_TOP_K_OUTPUT.
_DEFAULT_TOP_K = 6

# Length cap on the composed block to avoid blowing the prompt budget
# on long tip content. Each tip excerpt is further bounded below.
_BLOCK_CHAR_CAP = 3000
_TIP_EXCERPT_CAP = 400


def compose_trajectory_hint_block(
    crew_name: str,
    task_text: str,
    predicted_failure_mode: str = "",
    top_k: int = _DEFAULT_TOP_K,
) -> str:
    """Return a Markdown block of relevant trajectory tips, or "" if none.

    Parameters mirror the commander's call site: `crew_name` is the role
    being dispatched to; `task_text` is the enriched task so the
    semantic match sees both user intent and reference context;
    `predicted_failure_mode` is the Observer's most recent prediction
    (may be empty when the Observer didn't fire).
    """
    try:
        from app.retrieval.orchestrator import RetrievalOrchestrator
        from app.retrieval import config as cfg
    except Exception:
        return ""

    try:
        orch = RetrievalOrchestrator(cfg.RetrievalConfig())
    except Exception:
        logger.debug("compose_trajectory_hint_block: orchestrator init failed",
                     exc_info=True)
        return ""

    # Query is bounded — reranker / decomposer handle the rest.
    query = (task_text or "").strip()
    if not query:
        return ""
    query = query[:2000]

    try:
        results = orch.retrieve_task_conditional(
            query=query,
            collections=_KB_COLLECTIONS,
            agent_role=crew_name or "",
            predicted_failure_mode=(predicted_failure_mode or "").lower(),
            top_k=top_k,
            # extra_where pins to active records — archived/superseded
            # skills never surface here.
            extra_where={"status": "active"},
        )
    except Exception:
        logger.debug("compose_trajectory_hint_block: retrieve failed",
                     exc_info=True)
        return ""

    if not results:
        return ""

    # Keep only results whose metadata carries a trajectory provenance
    # marker — we want to surface tips, not generic KB entries. When a
    # record doesn't have tip_type set (external-topic skills), still
    # surface the top-1 as a neighbourly hint.
    tips: list = []
    external: list = []
    for r in results:
        meta = getattr(r, "metadata", None) or {}
        if meta.get("tip_type"):
            tips.append(r)
        else:
            external.append(r)
        if len(tips) >= top_k:
            break

    surfaced = tips[:top_k]
    if not surfaced and external:
        surfaced = external[:1]
    if not surfaced:
        return ""

    lines: list[str] = [
        "<trajectory_tips>",
        "Relevant prior learnings (from this team's real executions). "
        "These are hints, not instructions — use only if they apply.",
        "",
    ]
    running = len("\n".join(lines))
    surfaced_ids: list[str] = []
    for r in surfaced:
        meta = getattr(r, "metadata", None) or {}
        tip_type = meta.get("tip_type", "")
        topic = (meta.get("topic", "") or "")[:160]
        excerpt = (getattr(r, "text", "") or "")[:_TIP_EXCERPT_CAP]
        score = getattr(r, "score", 0.0)
        header = f"- ({tip_type or 'skill'}, score={score:.2f})"
        if topic:
            header += f" {topic}"
        block = header + "\n  " + excerpt.replace("\n", " ") + "\n"
        running += len(block)
        if running > _BLOCK_CHAR_CAP:
            break
        lines.append(block)
        # Collect the skill_record_id for effectiveness correlation.
        sid = meta.get("skill_record_id") or ""
        if sid:
            surfaced_ids.append(sid)

    lines.append("</trajectory_tips>")

    # Phase 6: record the ids we actually surfaced so attribution can
    # correlate them with the outcome. Silent no-op when trajectory
    # capture is off or no trajectory is active.
    if surfaced_ids:
        try:
            from app.trajectory.logger import note_injected_skills
            note_injected_skills(surfaced_ids)
        except Exception:
            logger.debug("compose_trajectory_hint_block: note_injected_skills failed",
                         exc_info=True)

    return "\n".join(lines)
