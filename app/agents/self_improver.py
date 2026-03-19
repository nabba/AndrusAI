from crewai import Agent
from app.config import get_settings
from app.llm_factory import create_specialist_llm
from app.tools.web_search import web_search
from app.tools.web_fetch import web_fetch
from app.tools.youtube_transcript import get_youtube_transcript
from app.tools.memory_tool import create_memory_tools
from app.tools.scoped_memory_tool import create_scoped_memory_tools
from app.tools.file_manager import file_manager
from app.tools.self_report_tool import create_self_report_tool
from app.tools.reflection_tool import ReflectionTool
from app.souls.loader import compose_backstory

settings = get_settings()

# Soul-composed backstory: constitution + self_improver soul + style + self-model
SELF_IMPROVER_BACKSTORY = compose_backstory("self_improver")


def create_self_improver() -> Agent:
    llm = create_specialist_llm(max_tokens=4096, role="research")
    memory_tools = create_memory_tools(collection="skills")
    scoped_tools = create_scoped_memory_tools("self_improver")
    awareness_tools = [
        create_self_report_tool("self_improver"),
        ReflectionTool(agent_role="self_improver"),
    ]

    return Agent(
        role="Learning Specialist",
        goal="Acquire deep, practical knowledge on assigned topics and distil it into reusable skill files.",
        backstory=SELF_IMPROVER_BACKSTORY,
        llm=llm,
        tools=[web_search, web_fetch, get_youtube_transcript, file_manager] + memory_tools + scoped_tools + awareness_tools,
        verbose=True,
    )
