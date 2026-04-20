"""
specialists.py — narrow-focus sub-agents for delegation-mode crews.

Each specialist carries a tightly-scoped toolkit (≤ 18 tools) so the
coordinator can delegate subtasks without exceeding any provider's tool
limit.  The coordinator itself gets a small core set plus CrewAI's
delegation meta-tools (delegate_work, ask_question).

This module is ONLY imported by delegated crew paths.  Legacy single-agent
paths continue to use app/agents/researcher.py, coder.py, etc. unchanged.
"""
from __future__ import annotations

import logging

from crewai import Agent

from app.llm_factory import create_specialist_llm
from app.souls.loader import compose_backstory

logger = logging.getLogger(__name__)


# ── Shared helpers ───────────────────────────────────────────────────

def _safe(factory, *args, **kwargs):
    """Try a tool-factory and swallow errors.  Specialists are opt-in —
    missing tools shouldn't kill the crew."""
    try:
        result = factory(*args, **kwargs)
        if isinstance(result, list):
            return result
        return [result] if result else []
    except Exception:
        return []


# ── Web specialist ───────────────────────────────────────────────────
# Handles: web search, page fetch, youtube transcripts, browser
# navigation, firecrawl scraping, MCP-served web tools.

_WEB_BACKSTORY = (
    "You are the Web Research Specialist.  Your job is simple: given a search "
    "query, return the most relevant up-to-date web content as structured text.  "
    "Use web_search for keyword lookups, web_fetch to read the contents of a "
    "specific URL, get_youtube_transcript for videos, browser_fetch for "
    "JavaScript-heavy pages that web_fetch can't handle.  Always cite the URL "
    "you pulled from.  Never speculate beyond what the sources say.  Keep "
    "responses compact — you're one step in a larger research pipeline."
)


def create_web_specialist(force_tier: str | None = None) -> Agent:
    llm = create_specialist_llm(max_tokens=4096, role="research", force_tier=force_tier)
    tools: list = []
    from app.tools.web_search import web_search
    from app.tools.web_fetch import web_fetch
    from app.tools.youtube_transcript import get_youtube_transcript
    tools.extend([web_search, web_fetch, get_youtube_transcript])

    # Browser / firecrawl — optional
    for mod, fn in [
        ("app.tools.browser_tools", "create_browser_tools"),
        ("app.tools.firecrawl_tools", "create_firecrawl_tools"),
    ]:
        tools.extend(_safe(lambda m=mod, f=fn: __import__(m, fromlist=[f]).__dict__[f]()))

    return Agent(
        role="Web Research Specialist",
        goal="Fetch authoritative web sources and return verbatim extracts with URLs.",
        backstory=_WEB_BACKSTORY,
        llm=llm,
        tools=tools,
        allow_delegation=False,  # Leaf agent — can't delegate further
        max_iter=6,
        verbose=False,
    )


# ── Document specialist ──────────────────────────────────────────────
# Handles: PDFs, OCR, attachments, local files, wiki reads, KB searches,
# research-KB, journal search.

_DOC_BACKSTORY = (
    "You are the Document Research Specialist.  Your focus is structured "
    "information: PDF documents, images needing OCR, user-attached files, "
    "the enterprise knowledge base, the research knowledge base (episteme), "
    "the experiential journal, and the internal wiki.  When asked for data "
    "you read the relevant sources and return faithful extracts with source "
    "citations (filename, page or section).  You do NOT invent data — if a "
    "source doesn't cover the asked question, say so plainly."
)


def create_document_specialist(force_tier: str | None = None) -> Agent:
    llm = create_specialist_llm(max_tokens=4096, role="research", force_tier=force_tier)
    tools: list = []
    from app.tools.attachment_reader import read_attachment
    from app.tools.file_manager import file_manager
    from app.knowledge_base.tools import KnowledgeSearchTool
    tools += [read_attachment, file_manager, KnowledgeSearchTool()]

    # OCR, PDF, wiki, research KB, journal — all optional
    for mod, fn, args in [
        ("app.tools.ocr_tool", "create_ocr_tool", ()),
        ("app.tools.wiki_tool_registry", "create_wiki_tools", ("read",)),
        ("app.episteme.tools", "get_episteme_tools", ("researcher",)),
        ("app.experiential.tools", "get_experiential_tools", ("researcher",)),
    ]:
        tools.extend(_safe(
            lambda m=mod, f=fn, a=args: __import__(m, fromlist=[f]).__dict__[f](*a),
        ))

    return Agent(
        role="Document Research Specialist",
        goal="Extract verified facts from documents, KBs, and the journal with citations.",
        backstory=_DOC_BACKSTORY,
        llm=llm,
        tools=tools,
        allow_delegation=False,
        max_iter=6,
        verbose=False,
    )


# ── Synthesis specialist ─────────────────────────────────────────────
# Handles: reasoning/dialectics, philosophy, aesthetic judgement,
# tensions, experiential journaling, self-report.

_SYNTH_BACKSTORY = (
    "You are the Synthesis Specialist.  When the coordinator has gathered "
    "raw facts from web/document specialists, your role is to integrate them "
    "into a coherent, well-reasoned response.  You use the philosophy KB for "
    "ethical/framework grounding, conceptual_blend for creative recombinations, "
    "find_counter_argument to stress-test claims, and the tensions/experiential "
    "stores to surface relevant past reflections.  Your output is the final "
    "synthesised answer: sourced, balanced, phone-readable."
)


def create_synthesis_specialist(force_tier: str | None = None) -> Agent:
    llm = create_specialist_llm(max_tokens=4096, role="writing", force_tier=force_tier)
    tools: list = []

    # Philosophy KB + dialectics
    for mod, fn, args in [
        ("app.philosophy.rag_tool", "PhilosophyRAGTool", ()),
        ("app.philosophy.dialectics_tool", "FindCounterArgumentTool", ()),
    ]:
        try:
            cls = __import__(mod, fromlist=[fn]).__dict__[fn]
            tools.append(cls())
        except Exception:
            pass

    # Conceptual blend
    try:
        from app.philosophy.blend_tool import create_conceptual_blend_tool
        t = create_conceptual_blend_tool()
        if t:
            tools.append(t)
    except Exception:
        pass

    # Tensions / experiential / aesthetic — all with their full read+write surface
    for mod, fn, args in [
        ("app.tensions.tools", "get_tension_tools", ("writer",)),
        ("app.experiential.tools", "get_experiential_tools", ("writer",)),
        ("app.aesthetics.tools", "get_aesthetic_tools", ("writer",)),
    ]:
        tools.extend(_safe(
            lambda m=mod, f=fn, a=args: __import__(m, fromlist=[f]).__dict__[f](*a),
        ))

    return Agent(
        role="Synthesis Specialist",
        goal="Integrate gathered facts into a balanced, sourced final response.",
        backstory=_SYNTH_BACKSTORY,
        llm=llm,
        tools=tools,
        allow_delegation=False,
        max_iter=6,
        verbose=False,
    )


# ── Research coordinator ─────────────────────────────────────────────
# Small tool set — mostly orchestration.  Delegation meta-tools
# (delegate_work, ask_question) are injected by CrewAI when
# allow_delegation=True.

_COORD_BACKSTORY = compose_backstory("researcher") + (
    "\n\n"
    "DELEGATION MODE — You coordinate a team of three specialists:\n"
    "  • Web Research Specialist — for live web content and URLs\n"
    "  • Document Research Specialist — for PDFs, KB passages, journal entries\n"
    "  • Synthesis Specialist — for final dialectical integration\n"
    "Use the delegate_work tool to dispatch sub-queries.  Give each "
    "specialist a focused, concrete instruction.  When you have enough raw "
    "material, delegate final integration to the Synthesis Specialist.  "
    "Return ONLY the final synthesised answer to the user — not a running "
    "commentary of your delegations."
)


def create_research_coordinator(force_tier: str | None = None) -> Agent:
    llm = create_specialist_llm(max_tokens=4096, role="research", force_tier=force_tier)
    tools: list = []

    # Coordinator needs memory access to remember context across delegations
    from app.tools.memory_tool import create_memory_tools
    from app.tools.scoped_memory_tool import create_scoped_memory_tools
    from app.tools.mem0_tools import create_mem0_tools
    tools.extend(create_memory_tools(collection="research"))
    tools.extend(create_scoped_memory_tools("researcher"))
    tools.extend(create_mem0_tools("researcher"))

    # A minimal web_search so simple follow-ups don't need a full delegation
    from app.tools.web_search import web_search
    tools.append(web_search)

    return Agent(
        role="Research Coordinator",
        goal=(
            "Answer the user's research question by delegating sub-queries to "
            "Web, Document, and Synthesis specialists, then presenting the "
            "integrated result."
        ),
        backstory=_COORD_BACKSTORY,
        llm=llm,
        tools=tools,
        allow_delegation=True,  # CrewAI adds delegate_work + ask_question
        max_iter=10,
        verbose=False,
    )
