"""desktop_agent.py — macOS desktop automation agent."""

from crewai import Agent
from app.llm_factory import create_specialist_llm
from app.tools.memory_tool import create_memory_tools
from app.tools.scoped_memory_tool import create_scoped_memory_tools
from app.tools.mem0_tools import create_mem0_tools
from app.souls.loader import compose_backstory


DESKTOP_BACKSTORY = compose_backstory("desktop")


def create_desktop_agent(force_tier: str | None = None) -> Agent:
    llm = create_specialist_llm(max_tokens=4096, role="coding", force_tier=force_tier)
    memory_tools = create_memory_tools(collection="desktop")
    scoped_tools = create_scoped_memory_tools("desktop")
    mem0_tools = create_mem0_tools("desktop")

    tools: list = [] + memory_tools + scoped_tools + mem0_tools

    # Desktop automation tools
    try:
        from app.tools.desktop_tools import create_desktop_tools
        desktop_tools = create_desktop_tools("desktop")
        if desktop_tools:
            tools.extend(desktop_tools)
    except Exception:
        pass

    # Bridge tools (filesystem, HTTP, execution)
    try:
        from app.tools.bridge_tools import create_bridge_tools
        bridge_tools = create_bridge_tools("desktop")
        if bridge_tools:
            tools.extend(bridge_tools)
    except Exception:
        pass

    # Wiki tools
    try:
        from app.tools.wiki_tool_registry import create_wiki_tools
        tools.extend(create_wiki_tools("read"))
    except Exception:
        pass

    return Agent(
        role="Desktop Automation Specialist",
        goal="Automate macOS desktop workflows: control apps, manage windows, execute shortcuts.",
        backstory=DESKTOP_BACKSTORY,
        llm=llm,
        tools=tools,
        max_execution_time=300,
        verbose=True,
    )
