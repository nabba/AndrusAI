from crewai import Agent
from app.config import get_settings
from app.llm_factory import create_specialist_llm
from app.tools.web_search import web_search
from app.tools.web_fetch import web_fetch
from app.tools.youtube_transcript import get_youtube_transcript
from app.tools.memory_tool import create_memory_tools
from app.tools.scoped_memory_tool import create_scoped_memory_tools
from app.tools.file_manager import file_manager
from app.tools.attachment_reader import read_attachment
from app.tools.self_report_tool import create_self_report_tool
from app.tools.reflection_tool import ReflectionTool
from app.souls.loader import compose_backstory
from app.tools.mem0_tools import create_mem0_tools
from app.knowledge_base.tools import KnowledgeSearchTool

settings = get_settings()

RESEARCHER_BACKSTORY = compose_backstory("researcher")

# S9: Compact backstory for simple tasks — just the essential identity and rules.
# Saves ~2000 tokens per LLM call (×4-6 calls = 8000-12000 tokens per task).
_COMPACT_RESEARCHER_BACKSTORY = (
    "You are a Researcher — an intelligence analyst who finds, verifies, and "
    "synthesizes information from web sources. You distrust information by default "
    "and verify across multiple sources. Never fabricate URLs or data. "
    "Distinguish between verified fact, inference, and speculation.\n"
    "CRITICAL: You HAVE web browsing tools (web_search, web_fetch). You CAN "
    "and MUST use them to answer questions about current events, live websites, "
    "or any information you don't already know. NEVER say 'I cannot browse the "
    "internet' — you can. Use your tools.\n"
    "Any <reference_context> block in the task is silent background (current date, "
    "season, user location). Use it only if the user's question depends on 'now' or "
    "'here'. Never mention, quote, describe, or reason aloud about it — the user "
    "did not write it and does not see it. Answer the user's actual question only."
)


def create_researcher(force_tier: str | None = None, light: bool = False, task_id: str = "") -> Agent:
    """Create a researcher agent.

    Args:
        force_tier: Override model tier selection.
        light: If True, use minimal tools and compact backstory (S8/S9).
               Used for difficulty ≤ 3 simple factual questions.
        task_id: If set, adds blackboard tools scoped to this task.
    """
    llm = create_specialist_llm(max_tokens=4096, role="research", force_tier=force_tier)

    if light:
        # S8→S10: "Medium-light" — enough tools for most factual questions
        # without the full 40+ tool overhead.  Adds file/attachment access,
        # Mem0 conversational context, and Firecrawl so the agent can actually
        # answer current-events and web-content questions routed as difficulty ≤ 3.
        mem0_tools = create_mem0_tools("researcher")
        tools = [web_search, web_fetch, file_manager, read_attachment, KnowledgeSearchTool()] + mem0_tools
        # Firecrawl search+scrape
        try:
            from app.tools.firecrawl_tools import create_firecrawl_tools
            fc = create_firecrawl_tools()
            if fc:
                tools.extend(fc[:2])  # scrape + search (keep tool count low)
        except Exception:
            pass
        backstory = _COMPACT_RESEARCHER_BACKSTORY
    else:
        # R5: Removed world_model_tool (write-only, never read) and self-awareness
        # tools (self_report, reflection) — telemetry now runs in post-crew hook.
        # This saves ~600 tokens per LLM call (3 fewer tool descriptions).
        memory_tools = create_memory_tools(collection="researcher")
        scoped_tools = create_scoped_memory_tools("researcher")
        mem0_tools = create_mem0_tools("researcher")
        # New KB tools (Phase 2/3): episteme for research grounding + journal for past experience.
        from app.episteme.tools import get_episteme_tools
        from app.experiential.tools import get_experiential_tools
        tools = [web_search, web_fetch, get_youtube_transcript, file_manager, read_attachment, KnowledgeSearchTool()] + memory_tools + scoped_tools + mem0_tools + get_episteme_tools() + get_experiential_tools("researcher")
        # Firecrawl tools (scrape, extract, search, map) — gracefully empty if unavailable
        try:
            from app.tools.firecrawl_tools import create_firecrawl_tools
            fc_tools = create_firecrawl_tools()
            if fc_tools:
                tools.extend(fc_tools[:4])  # scrape, search, extract, map (not crawl)
        except Exception:
            pass
        # Composio SaaS tools (Gmail, GitHub, Slack, etc.) — gracefully empty if unavailable
        try:
            from app.tools.composio_tool import get_composio_tools
            composio_tools = get_composio_tools()
            if composio_tools:
                tools.extend(composio_tools[:10])
        except Exception:
            pass
        # Host Bridge tools (read/write host files, execute commands, LAN access)
        try:
            from app.tools.bridge_tools import create_bridge_tools
            bridge_tools = create_bridge_tools("researcher")
            if bridge_tools:
                tools.extend(bridge_tools)
        except Exception:
            pass
        # Wiki tools (read, write, search — researcher is primary wiki contributor)
        try:
            from app.tools.wiki_tool_registry import create_wiki_tools
            wiki_tools = create_wiki_tools("read", "write", "search", "slides")
            if wiki_tools:
                tools.extend(wiki_tools)
        except Exception:
            pass
        # Blackboard tools (deposit/read findings — P1 research synthesis)
        if task_id:
            try:
                from app.tools.blackboard_tool import create_blackboard_tools
                bb_tools = create_blackboard_tools(task_id, "researcher")
                tools.extend(bb_tools)
            except Exception:
                pass
        # Tension tools — researcher records contradictions found during research
        try:
            from app.tensions.tools import get_tension_tools
            tools.extend(get_tension_tools("researcher"))
        except Exception:
            pass
        # OCR tool — extract text from images (screenshots, documents, receipts)
        try:
            from app.tools.ocr_tool import create_ocr_tool
            ocr = create_ocr_tool()
            if ocr:
                tools.append(ocr)
        except Exception:
            pass
        backstory = RESEARCHER_BACKSTORY

    return Agent(
        role="Researcher",
        goal="Find accurate, comprehensive information on any topic using web search, article reading, and YouTube transcripts.",
        backstory=backstory,
        llm=llm,
        tools=tools,
        max_execution_time=300,
        verbose=True,
    )
