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
import re
from datetime import datetime, timezone

from app.llm_factory import create_specialist_llm
from app.firebase_reporter import crew_started, crew_completed, crew_failed
from app.rate_throttle import start_request_tracking, stop_request_tracking
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
    start_request_tracking(task_id)

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
            stop_request_tracking()
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
            f'"action": "what we should do about it", '
            f'"openrouter_id": "provider/model-slug — ONLY for category=models AND only if you are confident the model is available on OpenRouter with that exact slug; omit this field otherwise"}}]\n\n'
            f"Only include genuinely NEW items not in the 'already known' list.\n"
            f"If nothing new, respond with: []"
        )

        raw = str(llm.call(prompt)).strip()

        from app.utils import safe_json_parse
        discoveries, err = safe_json_parse(raw)
        if not discoveries or not isinstance(discoveries, list):
            stop_request_tracking()
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

            if category == "models":
                _plant_model_stub(disc)

        # Notify user of high-relevance discoveries via Signal
        if high_relevance:
            try:
                _notify_user_discoveries(high_relevance)
            except Exception:
                logger.debug("Failed to notify user of tech discoveries", exc_info=True)

        tracker = stop_request_tracking()
        _tokens = tracker.total_tokens if tracker else 0
        _model = ", ".join(sorted(tracker.models_used)) if tracker and tracker.models_used else ""
        _cost = tracker.total_cost_usd if tracker else 0.0
        summary_msg = f"Tech radar: {stored} discoveries ({len(high_relevance)} high-relevance)"
        crew_completed("self_improvement", task_id, summary_msg,
                       tokens_used=_tokens, model=_model, cost_usd=_cost)
        return summary_msg

    except Exception as exc:
        stop_request_tracking()
        crew_failed("self_improvement", task_id, str(exc)[:200])
        logger.error(f"Tech radar scan failed: {exc}")
        return f"Tech radar failed: {str(exc)[:200]}"


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._:-]*$")


def _plant_model_stub(disc: dict) -> None:
    """If the discovery carries a plausible OpenRouter slug, plant a stub row
    in discovered_models so the OpenRouter scanner picks it up next cycle.

    Invalid or missing slugs are silently ignored — tech_radar remains a
    human-facing channel even when it can't resolve a slug.
    """
    raw = str(disc.get("openrouter_id", "")).strip().lower()
    if not raw:
        return
    # Accept both "provider/model" and "openrouter/provider/model"; normalize
    # to the catalog convention (openrouter/<slug>) to match scan_openrouter.
    if raw.startswith("openrouter/"):
        slug = raw[len("openrouter/"):]
    else:
        slug = raw
    if not _SLUG_RE.match(slug):
        logger.debug("tech_radar: skipping implausible openrouter_id %r", raw)
        return

    try:
        from app.llm_discovery import _store_stub
        _store_stub(
            model_id=f"openrouter/{slug}",
            provider="openrouter",
            display_name=str(disc.get("title", slug))[:200],
            source="tech_radar",
            metadata={"tech_radar_discovery": {
                "title": disc.get("title"),
                "summary": disc.get("summary"),
                "action": disc.get("action"),
                "relevance": disc.get("relevance"),
            }},
        )
    except Exception:
        logger.debug("tech_radar: stub insert failed", exc_info=True)


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
        loop = asyncio.get_running_loop()
        loop.call_soon_threadsafe(
            asyncio.ensure_future,
            signal_client.send(settings.signal_owner_number, msg),
        )
    except RuntimeError:
        logger.info(f"Tech radar notification (no event loop): {msg[:200]}")
    except Exception:
        logger.debug("Tech radar Signal notification failed", exc_info=True)


def get_recent_discoveries(n: int = 10) -> list[str]:
    """Return recent tech discoveries for display."""
    return retrieve_operational(SCOPE_TECH_RADAR, "technology discovery", n=n) or []
