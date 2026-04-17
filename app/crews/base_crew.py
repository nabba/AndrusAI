"""
base_crew.py — Shared crew execution logic with tool plugin registry.

Tool Plugin Registry:
    register_tool_plugin(factory_fn) — called once per tool source at import
    get_plugin_tools() — returns all plugin tools, cached per-process

    MCP tools, browser tools, and any future tool sources register here.
    All agents get them automatically. No per-agent file modification.

Auto-Skill Creation:
    Complex crew executions (>= _SKILL_CREATION_THRESHOLD tool calls) trigger
    a background distillation into a reusable SkillDraft. The draft is routed
    through the standard Integrator so novelty checking still applies.
"""

import logging
import threading
import time as _time
from pathlib import Path

from crewai import Task, Crew, Process

from app.firebase_reporter import crew_started, crew_completed, crew_failed
from app.memory.belief_state import update_belief
from app.benchmarks import record_metric
from app.llm_selector import difficulty_to_tier
from app.sanitize import wrap_user_input
from app.self_heal import diagnose_and_fix

logger = logging.getLogger(__name__)

# ── Tool Plugin Registry ──────────────────────────────────────────────────────

_tool_plugins: list = []  # list of Callable[[], list[BaseTool]]
_plugin_tools_cache: list | None = None
_plugin_lock = threading.Lock()


def register_tool_plugin(factory) -> None:
    """Register a tool factory function. All agents get these tools automatically.

    Factory must return a list of CrewAI tool instances. Called lazily on
    first crew execution (not at registration time).
    """
    global _plugin_tools_cache
    with _plugin_lock:
        _tool_plugins.append(factory)
        _plugin_tools_cache = None  # Invalidate cache


def get_plugin_tools() -> list:
    """Collect tools from all registered plugins. Cached after first call."""
    global _plugin_tools_cache
    if _plugin_tools_cache is not None:
        return _plugin_tools_cache
    with _plugin_lock:
        if _plugin_tools_cache is not None:
            return _plugin_tools_cache
        tools = []
        for factory in _tool_plugins:
            try:
                result = factory()
                if result:
                    tools.extend(result)
            except Exception:
                logger.debug(f"Tool plugin failed: {factory}", exc_info=True)
        _plugin_tools_cache = tools
        if tools:
            logger.info(f"Tool plugin registry: {len(tools)} tools from {len(_tool_plugins)} plugins")
        return tools


# ── Auto-Skill Creation ──────────────────────────────────────────────────────

_SKILL_CREATION_THRESHOLD = 5  # Minimum tool calls to trigger skill creation
_SKILL_EXCLUDED_CREWS = {"self_improvement", "retrospective", "critic"}


def _estimate_tool_calls(result: str) -> int:
    """Estimate tool call count from crew output text."""
    text = str(result)
    # CrewAI outputs "Observation:" after each tool call
    count = text.count("Observation:")
    if count == 0:
        # Fallback: "Action:" markers
        count = text.count("Action:")
    if count == 0 and len(text) > 2000:
        # Heuristic for long results without markers
        count = _SKILL_CREATION_THRESHOLD
    return count


def _auto_create_skill(crew_name: str, task: str, result: str, tool_calls: int) -> None:
    """Background: distill a complex crew execution into a reusable skill."""
    try:
        from app.llm_factory import create_specialist_llm
        from app.self_improvement.types import SkillDraft
        from app.self_improvement.integrator import integrate as integrate_draft
        import uuid

        llm = create_specialist_llm(max_tokens=800, role="synthesis")
        prompt = (
            f"A {crew_name} crew completed a complex task ({tool_calls} tool calls).\n\n"
            f"Task: {task[:500]}\n\nResult excerpt: {result[:1000]}\n\n"
            f"Distill into a reusable SKILL:\n"
            f"1. Topic (one line)\n2. When to use\n3. Procedure (max 5 steps)\n"
            f"4. Pitfalls\n\nMax 300 words."
        )
        skill_text = str(llm.call(prompt)).strip()
        if not skill_text or len(skill_text) < 50:
            return

        lines = skill_text.strip().split("\n")
        topic = lines[0].replace("Topic:", "").replace("#", "").strip()[:100] or task[:80]

        draft = SkillDraft(
            id=f"auto_{uuid.uuid4().hex[:8]}",
            topic=topic,
            rationale=f"Auto-captured from {crew_name} ({tool_calls} tool calls)",
            content_markdown=skill_text,
            proposed_kb="experiential",
        )
        integrate_draft(draft)
        logger.info(f"Auto-skill created: '{topic}' from {crew_name}")
    except Exception:
        logger.debug("Auto-skill creation failed", exc_info=True)


# ── Core Crew Execution ──────────────────────────────────────────────────────

def run_single_agent_crew(
    crew_name: str,
    agent_role: str,
    create_agent_fn,
    task_template: str,
    task_description: str,
    expected_output: str,
    parent_task_id: str = None,
    difficulty: int = 5,
    extra_tools: list = None,
) -> str:
    """Run a single-agent crew with all standard boilerplate.

    Args:
        crew_name: Firebase crew name (e.g. "coding", "writing")
        agent_role: Belief state role (e.g. "coder", "writer")
        create_agent_fn: Factory function (force_tier) → Agent
        task_template: Template string with {user_input} placeholder
        task_description: The user's task (gets wrapped in template)
        expected_output: Expected output description for CrewAI
        parent_task_id: Optional parent task for sub-agent tracking
        difficulty: Task difficulty (1-10)
        extra_tools: additional tools specific to this crew (on top of plugin tools).

    Returns:
        The crew's output as a string.
    """
    start = _time.monotonic()

    from app.conversation_store import estimate_eta
    from app.llm_mode import get_mode
    force_tier = difficulty_to_tier(difficulty, get_mode())
    agent = create_agent_fn(force_tier=force_tier)

    # Inject plugin tools (MCP, browser, etc.) into the agent
    plugin_tools = get_plugin_tools()
    if plugin_tools:
        existing = list(agent.tools) if agent.tools else []
        agent.tools = existing + plugin_tools
    if extra_tools:
        existing = list(agent.tools) if agent.tools else []
        agent.tools = existing + extra_tools

    # Extract model name from the agent's LLM for the task record
    _model_name = ""
    try:
        _model_name = getattr(agent.llm, 'model', '') if getattr(agent, 'llm', None) else ''
    except Exception:
        pass

    # Strip injected context from the task summary shown on dashboard.
    _clean_desc = task_description
    for _ctx_marker in ("KNOWLEDGE BASE CONTEXT", "RELEVANT KNOWLEDGE", "RELEVANT TEAM CONTEXT",
                        "<recent_conversation>", "LESSONS FROM PAST"):
        _idx = _clean_desc.find(_ctx_marker)
        if _idx == 0:
            for _end_marker in ("\n\nNOTE:", "\n</", "\nNOTE:"):
                _end = _clean_desc.rfind(_end_marker)
                if _end > 0:
                    _rest = _clean_desc[_end:].split("\n\n", 2)
                    if len(_rest) > 1:
                        _clean_desc = _rest[-1].strip()
                        break
            break
    task_id = crew_started(
        crew_name,
        f"{crew_name.title()}: {_clean_desc[:100]}",
        eta_seconds=estimate_eta(crew_name),
        parent_task_id=parent_task_id,
        model=_model_name,
    )
    update_belief(agent_role, "working", current_task=_clean_desc[:100])

    task = Task(
        description=task_template.format(user_input=wrap_user_input(task_description)),
        expected_output=expected_output,
        agent=agent,
    )

    crew = Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        verbose=True,
    )

    try:
        result = str(crew.kickoff())
        duration = _time.monotonic() - start

        update_belief(agent_role, "completed", current_task=task_description[:100])
        record_metric("task_completion_time", duration, {"crew": crew_name})

        # L4: Autobiographical journal entry (append-only, ~100 bytes)
        try:
            import json as _json
            from datetime import datetime as _dt, timezone as _tz
            _journal = Path("/app/workspace/journal.jsonl")
            with open(_journal, "a") as _jf:
                _jf.write(_json.dumps({
                    "ts": _dt.now(_tz.utc).isoformat(),
                    "crew": crew_name,
                    "task": task_description[:200],
                    "result": "success",
                    "duration_s": round(duration, 1),
                }) + "\n")
        except Exception:
            pass

        # Capture token usage from active request tracker
        _tokens = 0; _model = ""; _cost = 0.0
        try:
            from app.rate_throttle import get_active_tracker
            t = get_active_tracker()
            if t:
                _tokens = t.total_tokens
                _model = ", ".join(sorted(t.models_used)) if t.models_used else ""
                _cost = t.total_cost_usd
        except Exception:
            pass
        crew_completed(crew_name, task_id, result[:2000],
                       tokens_used=_tokens, model=_model, cost_usd=_cost)

        # Auto-skill creation for complex tasks
        if crew_name not in _SKILL_EXCLUDED_CREWS:
            tool_calls = _estimate_tool_calls(result)
            if tool_calls >= _SKILL_CREATION_THRESHOLD:
                threading.Thread(
                    target=_auto_create_skill,
                    args=(crew_name, task_description, result, tool_calls),
                    daemon=True, name=f"skill-{crew_name}",
                ).start()

        return result
    except Exception as exc:
        update_belief(agent_role, "failed", current_task=task_description[:100])
        crew_failed(crew_name, task_id, str(exc)[:200])

        # L4: Journal failure entry
        try:
            import json as _json
            from datetime import datetime as _dt, timezone as _tz
            _journal = Path("/app/workspace/journal.jsonl")
            with open(_journal, "a") as _jf:
                _jf.write(_json.dumps({
                    "ts": _dt.now(_tz.utc).isoformat(),
                    "crew": crew_name,
                    "task": task_description[:200],
                    "result": "failed",
                    "error": str(exc)[:100],
                    "duration_s": round(_time.monotonic() - start, 1),
                }) + "\n")
        except Exception:
            pass

        diagnose_and_fix(crew_name, task_description, exc, task_id=task_id)
        raise


# ── Plugin auto-registration at import time ──────────────────────────────────
# These execute only when the factories are first called (lazy), not at import.

def _register_default_plugins() -> None:
    """Register built-in tool plugins. Called once from main.py startup.

    Also patches crewai.Agent so every Agent instance — including those built
    by multi-agent crews that don't use run_single_agent_crew — gets plugin
    tools auto-appended at construction time.
    """
    # MCP tools
    register_tool_plugin(
        lambda: __import__("app.mcp.tool_adapter", fromlist=["create_crewai_tools"]).create_crewai_tools()
    )
    # Browser tools
    register_tool_plugin(
        lambda: __import__("app.tools.browser_tools", fromlist=["create_browser_tools"]).create_browser_tools()
    )
    # Session search tool
    register_tool_plugin(
        lambda: __import__("app.tools.session_search_tool", fromlist=["create_session_search_tools"]).create_session_search_tools()
    )

    # Patch crewai.Agent so every agent instance gets plugin tools automatically
    _patch_agent_for_plugins()


_agent_patched = False


def _patch_agent_for_plugins() -> None:
    """Monkey-patch crewai.Agent so every Agent auto-appends plugin tools.

    This is the ONE place where plugin tools reach every agent — multi-agent
    crews (research / media / critic / creative / etc.) don't use
    run_single_agent_crew but do construct crewai.Agent, so hooking the
    constructor ensures no agent is missed.
    """
    global _agent_patched
    if _agent_patched:
        return
    try:
        from crewai import Agent
    except ImportError:
        logger.debug("crewai not importable; skipping Agent plugin patch")
        return

    original_init = Agent.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        try:
            plugins = get_plugin_tools()
            if plugins:
                existing = list(self.tools) if self.tools else []
                # Avoid double-registration if the same tool name is already present
                names = {getattr(t, "name", "") for t in existing}
                self.tools = existing + [t for t in plugins if getattr(t, "name", "") not in names]
        except Exception:
            logger.debug("Agent plugin injection failed (non-fatal)", exc_info=True)

    Agent.__init__ = patched_init
    _agent_patched = True
    logger.info("crewai.Agent patched for plugin-tool auto-injection")
