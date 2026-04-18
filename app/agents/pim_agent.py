"""pim_agent.py — Personal Information Management agent (email, calendar, tasks)."""

from crewai import Agent
from app.llm_factory import create_specialist_llm
from app.tools.memory_tool import create_memory_tools
from app.tools.scoped_memory_tool import create_scoped_memory_tools
from app.tools.mem0_tools import create_mem0_tools
from app.souls.loader import compose_backstory


PIM_BACKSTORY = compose_backstory("pim")


def create_pim_agent(force_tier: str | None = None) -> Agent:
    llm = create_specialist_llm(max_tokens=4096, role="writing", force_tier=force_tier)
    memory_tools = create_memory_tools(collection="pim")
    scoped_tools = create_scoped_memory_tools("pim")
    mem0_tools = create_mem0_tools("pim")

    tools: list = [] + memory_tools + scoped_tools + mem0_tools

    # Email tools
    try:
        from app.tools.email_tools import create_email_tools
        email_tools = create_email_tools("pim")
        if email_tools:
            tools.extend(email_tools)
    except Exception:
        pass

    # Calendar tools
    try:
        from app.tools.calendar_tools import create_calendar_tools
        cal_tools = create_calendar_tools("pim")
        if cal_tools:
            tools.extend(cal_tools)
    except Exception:
        pass

    # Task tools
    try:
        from app.tools.task_tools import create_task_tools
        task_tools = create_task_tools("pim")
        if task_tools:
            tools.extend(task_tools)
    except Exception:
        pass

    # Photos tools (macOS Photos.app via AppleScript through bridge)
    try:
        from app.tools.photos_tools import create_photos_tools
        photo_tools = create_photos_tools("pim")
        if photo_tools:
            tools.extend(photo_tools)
    except Exception:
        pass

    # Wiki tools
    try:
        from app.tools.wiki_tool_registry import create_wiki_tools
        tools.extend(create_wiki_tools("read", "write"))
    except Exception:
        pass

    return Agent(
        role="Personal Information Manager",
        goal="Manage email, calendar, and tasks. Summarize, prioritize, and organize.",
        backstory=PIM_BACKSTORY,
        llm=llm,
        tools=tools,
        max_execution_time=300,
        verbose=True,
    )
