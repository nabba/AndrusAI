"""desktop_agent.py — macOS desktop automation agent."""

import logging

from crewai import Agent
from app.llm_factory import create_specialist_llm
from app.tools.memory_tool import create_memory_tools
from app.tools.scoped_memory_tool import create_scoped_memory_tools
from app.tools.mem0_tools import create_mem0_tools
from app.souls.loader import compose_backstory

logger = logging.getLogger(__name__)


DESKTOP_BACKSTORY = compose_backstory("desktop")


def create_desktop_agent(force_tier: str | None = None) -> Agent:
    llm = create_specialist_llm(max_tokens=4096, role="coding", force_tier=force_tier)
    memory_tools = create_memory_tools(collection="desktop")
    scoped_tools = create_scoped_memory_tools("desktop")
    mem0_tools = create_mem0_tools("desktop")

    tools: list = [] + memory_tools + scoped_tools + mem0_tools

    # ── Workspace file access (reliable, self-contained) ────────────
    # Always available — no external dependencies.  The desktop crew
    # previously had only bridge-tool-based filesystem access, which
    # silently failed when the host bridge wasn't reachable (2026-04-24
    # PSP task #68: "get me the file" routed here but the crew reported
    # "I cannot access the macOS local filesystem").  file_manager
    # works against /app/workspace/ via the security-allowlisted
    # pathlib-only implementation, so the crew can at least read the
    # response .md files it's asked to deliver.
    try:
        from app.tools.file_manager import file_manager
        from app.tools.attachment_reader import read_attachment
        tools.extend([file_manager, read_attachment])
    except Exception as exc:
        logger.warning("desktop_agent: file_manager import failed: %s", exc)

    # Desktop automation tools (AppleScript, shortcuts, UI control).
    # Requires host-bridge access; log-loud when missing so the crew's
    # self-diagnosis can see why at install time instead of reasoning
    # about it at runtime.
    try:
        from app.tools.desktop_tools import create_desktop_tools
        desktop_tools = create_desktop_tools("desktop")
        if desktop_tools:
            tools.extend(desktop_tools)
        else:
            logger.info("desktop_agent: desktop_tools returned empty "
                        "(host-bridge likely unreachable)")
    except Exception as exc:
        logger.warning("desktop_agent: desktop_tools import failed: %s", exc)

    # Hardware/IoT tools (serial, MQTT, USB enumeration — all via bridge)
    try:
        from app.tools.hardware_tools import create_hardware_tools
        hw_tools = create_hardware_tools("desktop")
        if hw_tools:
            tools.extend(hw_tools)
    except Exception as exc:
        logger.debug("desktop_agent: hardware_tools unavailable: %s", exc)

    # Bridge tools (filesystem, HTTP, execution) — preferred path for
    # host-side file ops; file_manager above is the in-container
    # fallback when bridge is down.
    try:
        from app.tools.bridge_tools import create_bridge_tools
        bridge_tools = create_bridge_tools("desktop")
        if bridge_tools:
            tools.extend(bridge_tools)
        else:
            logger.info("desktop_agent: bridge_tools returned empty "
                        "(host-bridge likely unreachable) — file_manager "
                        "remains available as workspace-scoped fallback")
    except Exception as exc:
        logger.warning("desktop_agent: bridge_tools import failed: %s", exc)

    # Wiki tools
    try:
        from app.tools.wiki_tool_registry import create_wiki_tools
        tools.extend(create_wiki_tools("read"))
    except Exception as exc:
        logger.debug("desktop_agent: wiki_tools unavailable: %s", exc)

    return Agent(
        role="Desktop Automation Specialist",
        goal="Automate macOS desktop workflows: control apps, manage windows, execute shortcuts.",
        backstory=DESKTOP_BACKSTORY,
        llm=llm,
        tools=tools,
        max_execution_time=300,
        verbose=True,
    )
