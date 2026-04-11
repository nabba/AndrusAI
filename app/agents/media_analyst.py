from crewai import Agent
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


MEDIA_ANALYST_BACKSTORY = compose_backstory("media_analyst")


def create_media_analyst(force_tier: str | None = None) -> Agent:
    llm = create_specialist_llm(max_tokens=4096, role="media", force_tier=force_tier)
    memory_tools = create_memory_tools(collection="media_analyst")
    scoped_tools = create_scoped_memory_tools("media_analyst")
    mem0_tools = create_mem0_tools("media_analyst")
    awareness_tools = [
        create_self_report_tool("media_analyst"),
        ReflectionTool(agent_role="media_analyst"),
    ]

    return Agent(
        role="Media Analyst",
        goal="Analyze multimedia content — videos, images, audio, documents — and extract structured insights.",
        backstory=MEDIA_ANALYST_BACKSTORY,
        llm=llm,
        tools=[
            web_search, web_fetch, get_youtube_transcript,
            file_manager, read_attachment, KnowledgeSearchTool(),
        ] + memory_tools + scoped_tools + mem0_tools + awareness_tools,
        max_execution_time=300,
        verbose=True,
    )
