"""
tech_radar_crew.py — Internet technology monitoring crew.

Scans the web for new technologies, LLM models, agent frameworks,
and research papers relevant to the system. Runs during idle time
and notifies the user of high-relevance discoveries via Signal.

Searches:
  - OpenRouter for new/updated models
  - CrewAI releases and blog posts
  - arXiv for self-improving agent papers
  - General AI agent framework news
"""

import json
import logging
from datetime import datetime, timezone

from app.llm_factory import create_specialist_llm
from app.firebase_reporter import crew_started, crew_completed, crew_failed
from app.memory.scoped_memory import store_scoped, retrieve_operational

logger = logging.getLogger(__name__)

SCOPE_TECH_RADAR = "scope_tech_radar"

# Search queries for different technology categories
_SEARCH_QUERIES = [
    ("models", "new LLM model released OpenRouter 2026"),
    ("models", "new open source language model benchmark 2026"),
    ("frameworks", "CrewAI new release update 2026"),
    ("frameworks", "AI agent framework comparison 2026"),
    ("research", "self-improving AI agent paper arxiv 2026"),
    ("research", "multi-agent system self-healing autonomous 2026"),
    ("tools", "LLM tool use function calling improvement 2026"),
]


def run_tech_scan() -> str:
    """Scan the internet for new relevant technologies.

    Uses direct LLM call to analyze search results and identify
    high-relevance discoveries. No CrewAI overhead.

    Returns a summary string.
    """
    task_id = crew_started("self_improvement", "Tech Radar scan", eta_seconds=120)

    try:
        from app.tools.web_search import web_search

        # Check what we've already discovered (avoid re-reporting)
        known = retrieve_operational(SCOPE_TECH_RADAR, "discovered technology", n=20)
        known_text = "\n".join(k[:200] for k in (known or []))

        # Run searches
        search_results = {}
        for category, query in _SEARCH_QUERIES:
            try:
                result = web_search.run(query)
                if result and result != "No results found." and "error" not in result.lower():
                    search_results.setdefault(category, []).append(result)
            except Exception:
                continue

        if not search_results:
            crew_completed("self_improvement", task_id, "No search results")
            return "Tech radar: no results from web searches."

        # Format search results for analysis
        search_block = ""
        for cat, results in search_results.items():
            search_block += f"\n## {cat.upper()}\n"
            for r in results:
                search_block += f"{r[:1500]}\n---\n"

        # Direct LLM call to analyze results
        llm = create_specialist_llm(max_tokens=2048, role="research")
        prompt = (
            f"You are a technology radar for an AI agent system built on CrewAI. "
            f"Analyze these web search results and identify NEW technologies, models, "
            f"or approaches that could improve our system.\n\n"
            f"Search results:\n{search_block[:8000]}\n\n"
            f"Already known (don't report again):\n{known_text[:2000]}\n\n"
            f"Respond with ONLY a JSON array of discoveries:\n"
            f'[{{"title": "...", "category": "models|frameworks|research|tools", '
            f'"relevance": "high|medium|low", '
            f'"summary": "1-2 sentence description", '
            f'"action": "what we should do about it"}}]\n\n'
            f"Only include genuinely NEW items not in the 'already known' list.\n"
            f"If nothing new, respond with: []"
        )

        raw = str(llm.call(prompt)).strip()

        from app.utils import safe_json_parse
        discoveries, err = safe_json_parse(raw)
        if not discoveries or not isinstance(discoveries, list):
            crew_completed("self_improvement", task_id, "No new discoveries")
            return "Tech radar: no new discoveries."

        # Store discoveries and identify high-relevance items
        high_relevance = []
        stored = 0
        for disc in discoveries[:10]:
            if not isinstance(disc, dict):
                continue
            title = disc.get("title", "")
            category = disc.get("category", "unknown")
            relevance = disc.get("relevance", "low")
            summary = disc.get("summary", "")
            action = disc.get("action", "")

            if not title or not summary:
                continue

            # Store in tech radar memory
            store_scoped(
                SCOPE_TECH_RADAR,
                f"[{category}] {title}: {summary}. Action: {action}",
                {"category": category, "relevance": relevance, "title": title},
                importance="high" if relevance == "high" else "normal",
            )
            stored += 1

            if relevance == "high":
                high_relevance.append(disc)

            # If it's a new model, create a proposal to add it to the catalog
            if category == "models" and relevance in ("high", "medium"):
                try:
                    from app.proposals import create_proposal
                    create_proposal(
                        title=f"New model: {title}"[:100],
                        description=(
                            f"Tech radar discovered: {title}\n\n"
                            f"{summary}\n\n"
                            f"Recommended action: {action}"
                        ),
                        proposal_type="skill",
                    )
                except Exception:
                    pass

        # Notify user of high-relevance discoveries via Signal
        if high_relevance:
            try:
                _notify_user_discoveries(high_relevance)
            except Exception:
                logger.debug("Failed to notify user of tech discoveries", exc_info=True)

        summary_msg = f"Tech radar: {stored} discoveries ({len(high_relevance)} high-relevance)"
        crew_completed("self_improvement", task_id, summary_msg)
        return summary_msg

    except Exception as exc:
        crew_failed("self_improvement", task_id, str(exc)[:200])
        logger.error(f"Tech radar scan failed: {exc}")
        return f"Tech radar failed: {str(exc)[:200]}"


def _notify_user_discoveries(discoveries: list[dict]) -> None:
    """Send high-relevance discoveries to user via Signal."""
    lines = ["🔬 Tech Radar — new discoveries:\n"]
    for d in discoveries[:3]:
        lines.append(f"• [{d.get('category', '')}] {d.get('title', '')}")
        lines.append(f"  {d.get('summary', '')}")
        if d.get("action"):
            lines.append(f"  → {d['action']}")
        lines.append("")

    msg = "\n".join(lines)

    # Fire-and-forget Signal notification
    import asyncio
    try:
        from app.main import signal_client
        from app.config import get_settings
        settings = get_settings()
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.call_soon_threadsafe(
                asyncio.ensure_future,
                signal_client.send(settings.signal_owner_number, msg),
            )
        else:
            logger.info(f"Tech radar notification (no event loop): {msg[:200]}")
    except Exception:
        logger.debug("Tech radar Signal notification failed", exc_info=True)


def get_recent_discoveries(n: int = 10) -> list[str]:
    """Return recent tech discoveries for display."""
    return retrieve_operational(SCOPE_TECH_RADAR, "technology discovery", n=n) or []
