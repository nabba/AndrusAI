"""
delegated_research.py — research crew built as Coordinator + 3 specialists.

Engaged ONLY when delegation_settings.is_enabled("research") is True.
See app/crews/delegation_settings.py for the on/off switch and the
dashboard Org Chart toggle that flips it.

Architecture:
  Coordinator  (5-8 tools + 2 CrewAI delegation meta-tools)
   │
   ├─ Web Specialist        (~6 tools — web_search, fetch, youtube, firecrawl, browser, MCP web)
   ├─ Document Specialist   (~7 tools — PDF/OCR/attachment, KB, episteme, journal, wiki read)
   └─ Synthesis Specialist  (~9 tools — philosophy, dialectics, tensions, experiential, aesthetics)

Every agent stays ≤ 18 tools — fits Anthropic's 20-strict limit with
margin.  Scales to any provider.
"""
from __future__ import annotations

import logging

from crewai import Crew, Task, Process

from app.agents.specialists import (
    create_research_coordinator,
    create_web_specialist,
    create_document_specialist,
    create_synthesis_specialist,
)
from app.sanitize import wrap_user_input
from app.crews.lifecycle import crew_lifecycle
from app.llm_selector import difficulty_to_tier

logger = logging.getLogger(__name__)


# Task template — the coordinator sees this and chooses delegations.
_DELEGATED_TASK_TEMPLATE = """\
Research the following topic for the user:

{user_input}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TOOL-USE IS MANDATORY FOR LIVE DATA — DO NOT FABRICATE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

When the user asks for statistics, URLs, citations, or "live data":
 • The Web Specialist MUST call web_search + firecrawl_scrape /
   firecrawl_extract to fetch the actual page.  Every number / URL / DOI
   in the final answer must come from a tool result, not from memory.
 • Invented slugs ("bankwatch.org/story/clearcutting-chaos"), unverifiable
   paper titles, and false-precision figures ("~29,000 ha / $4.2M") are
   treated as task FAILURE by vetting and will be rejected.
 • If a source is unreachable after 3-4 tries, say so explicitly —
   partial honest coverage beats fabricated completeness.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Process:
1. Classify what's needed: live web content, document/KB facts, dialectical synthesis, or a combination.
2. Delegate focused sub-queries to the right specialist(s).
   — "Web Research Specialist" for URLs, current events, YouTube
   — "Document Research Specialist" for PDFs, KB/journal lookups, attachments
   — "Synthesis Specialist" for final integration of collected material
3. When you have enough material, either synthesise yourself (simple tasks)
   or delegate final synthesis to the Synthesis Specialist (complex/
   multi-source tasks).

OUTPUT RULES:
 - Return ONLY the final answer — do not narrate delegation steps.
 - The user reads on a phone via Signal.  Keep the answer focused.
 - Cite sources (URL or document name) inline — only REAL URLs your
   specialists actually fetched.
 - 200-800 words typical; longer only if the request explicitly asks for depth.
 - If the user asked for graphics/PDF/charts and the available tools
   cannot produce them, SAY SO directly — do not promise deliverables
   you cannot actually generate.
"""


class DelegatedResearchCrew:
    """Sub-agent delegation variant of the research crew."""

    def run(
        self,
        topic: str,
        parent_task_id: str | None = None,
        difficulty: int = 5,
    ) -> str:
        from app.llm_mode import get_mode
        force_tier = difficulty_to_tier(difficulty, get_mode())

        with crew_lifecycle(
            crew_name="research",
            agent_role="researcher",
            task_title=f"Research (delegated): {topic[:100]}",
            task_description=topic,
            parent_task_id=parent_task_id,
            mode="delegated",
        ) as ctx:
            # Build the team.  Sub-agents share the same LLM tier for cost
            # predictability; coordinator uses the role default for
            # research which, with the 353-model catalog loaded, tends
            # toward Grok-4.20 or Claude Sonnet.
            coordinator = create_research_coordinator(force_tier=force_tier)
            web = create_web_specialist(force_tier=force_tier)
            docs = create_document_specialist(force_tier=force_tier)
            synth = create_synthesis_specialist(force_tier=force_tier)

            task = Task(
                description=_DELEGATED_TASK_TEMPLATE.format(
                    user_input=wrap_user_input(topic),
                ),
                expected_output=(
                    "Final research answer with source citations, "
                    "phone-readable, focused on the user's question."
                ),
                agent=coordinator,
            )

            crew = Crew(
                agents=[coordinator, web, docs, synth],
                tasks=[task],
                # Hierarchical process enables the coordinator to call
                # delegate_work / ask_question on the other agents.
                process=Process.hierarchical,
                manager_llm=coordinator.llm,
                verbose=False,
            )

            result_str = str(crew.kickoff())
            ctx.set_outcome(result_str)
            return result_str
