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

from app.config import get_settings
from app.firebase_reporter import crew_started, crew_completed, crew_failed
from app.memory.belief_state import update_belief
from app.benchmarks import record_metric
from app.llm_selector import difficulty_to_tier
from app.sanitize import wrap_user_input
from app.self_heal import diagnose_and_fix

logger = logging.getLogger(__name__)
settings = get_settings()

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


# Ordered priority list for tool capping (highest = drop last).
# When an agent's tool count exceeds MAX_TOOLS_PER_AGENT (default 18, max
# 20 due to Anthropic's strict-tools limit), tools with lower priority
# are pruned first.  Core capabilities — code execution, web search,
# file I/O, memory, knowledge base — always survive.  Peripheral
# introspection/tension/experiential tools drop first.
_TOOL_PRIORITY_ORDER = (
    # Priority 100 — core execution / I/O  (never drop these)
    ("execute_code", 100),
    ("file_manager", 100),
    ("read_attachment", 100),
    ("send_email", 100),
    # Priority 90 — primary information retrieval
    ("web_search", 90),
    ("web_fetch", 90),
    ("search_knowledge_base", 90),
    ("get_youtube_transcript", 90),
    # Priority 80 — memory (one mem0 + one scoped-memory is enough)
    ("mem0_add", 80),
    ("mem0_search", 80),
    ("scoped_memory_add", 80),
    ("scoped_memory_search", 80),
    ("team_memory_store", 70),
    ("team_memory_retrieve", 70),
    # Priority 70 — calendar / email / pim core
    ("check_email", 70),
    ("search_email", 70),
    ("list_calendar_events", 70),
    ("create_calendar_event", 70),
    ("list_tasks", 70),
    ("create_task", 70),
    # Priority 65 — MCP manager (needed for dynamic tool discovery)
    ("mcp_search_servers", 65),
    ("mcp_list_servers", 65),
    # Priority 60 — OCR / media
    ("ocr_extract_text", 60),
    ("read_pdf", 60),
    # Priority 55 — browser / host bridge
    ("browser_fetch", 55),
    ("execute_on_host", 55),
    ("http_from_host", 55),
    # Priority 50 — wiki (read OK, write is peripheral)
    ("wiki_read", 55),
    ("wiki_search", 55),
    ("wiki_write", 40),
    ("wiki_slides", 30),
    # Priority 58 — writer-essential dialectics.  These are peripheral for
    # coder/researcher but core for writer — writer tasks that benefit from
    # counter-arguments or conceptual reframing need these.  Ranked ABOVE
    # bridge tools (55) so the writer's 29-tool list preserves them while
    # dropping 2 bridge tools (which writers rarely use anyway).
    ("philosophy_knowledge_base", 58),
    ("find_counter_argument", 58),
    ("conceptual_blend", 58),
    # Priority 35 — introspective surface the agent can *call* during a task
    # (post-task telemetry writes experiential/tension entries automatically
    # via lifecycle hooks regardless, so the search tools matter more than
    # the record ones).
    ("search_tensions", 35),
    ("search_experiential", 35),
    ("search_aesthetic", 30),
    ("record_tension", 25),
    ("record_experience", 25),
    ("record_aesthetic", 25),
    ("self_report", 20),
    # Priority 10 — plugin tools / anything unrecognized (default)
)

_TOOL_PRIORITY_MAP = {name: prio for name, prio in _TOOL_PRIORITY_ORDER}


def _tool_priority(tool) -> int:
    """Return priority 0-100 for a tool.  Unknown names default to 10."""
    name = getattr(tool, "name", "") or ""
    return _TOOL_PRIORITY_MAP.get(name, 10)


def _cap_tools_by_priority(tools: list, cap: int) -> list:
    """Return at most *cap* tools, keeping highest-priority ones.

    Stable within priority bucket: if two tools share a priority,
    the one listed first in *tools* wins.  This lets a factory
    declare its preferred order and have it respected among peers.
    """
    if len(tools) <= cap:
        return tools
    # Sort by (priority DESC, original index ASC).  Then slice.
    indexed = list(enumerate(tools))
    indexed.sort(key=lambda it: (-_tool_priority(it[1]), it[0]))
    kept = [t for _, t in indexed[:cap]]
    # Preserve the caller's original order for the survivors
    survivor_set = {id(t) for t in kept}
    return [t for t in tools if id(t) in survivor_set]


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
                logger.warning(f"Tool plugin failed: {factory}", exc_info=True)
        _plugin_tools_cache = tools
        logger.info(f"Tool plugin registry: {len(tools)} tools from {len(_tool_plugins)} plugins")
        return tools


# ── Tool Manifest (Tool-First affordance) ──────────────────────────────────

_TOOL_MANIFEST_MARKER = "## Your Tools (Tool-First Protocol)"
_TOOL_MANIFEST_MAX = 40  # cap so the backstory doesn't explode past context limits


def _append_tool_manifest(backstory: str, tools: list) -> str:
    """Append a concise list of tool names + one-liners to the backstory.

    Idempotent — detects the marker and refreshes rather than duplicating.
    Keeps descriptions short so the system prompt stays lean.
    """
    if not tools:
        return backstory
    entries = []
    for t in tools[:_TOOL_MANIFEST_MAX]:
        name = getattr(t, "name", "")
        if not name:
            continue
        desc = (getattr(t, "description", "") or "").strip()
        # Collapse whitespace + trim to first sentence / 100 chars
        desc = " ".join(desc.split())[:120]
        entries.append(f"- `{name}` — {desc}")
    if not entries:
        return backstory
    extra = len(tools) - _TOOL_MANIFEST_MAX
    extra_note = f"\n  (+{extra} more — see function-calling schema)" if extra > 0 else ""
    manifest = (
        f"\n\n{_TOOL_MANIFEST_MARKER}\n"
        "You have these tools attached RIGHT NOW. Scan this list before answering any request.\n"
        "If ANY entry plausibly maps to the user's ask, CALL IT before saying you cannot. "
        "Refusing without a tool attempt violates your operating protocol.\n\n"
        + "\n".join(entries)
        + extra_note
    )
    # Idempotent refresh: strip any previous manifest block
    if _TOOL_MANIFEST_MARKER in backstory:
        backstory = backstory.split(_TOOL_MANIFEST_MARKER)[0].rstrip()
    return backstory + manifest


# ── Refusal Detection (Tool-First enforcement) ──────────────────────────────
# When a specialist answers with a refusal phrase AND has tools AND hasn't
# actually called any, we retry once with an explicit nudge listing available
# tool names. This captures the "LLM defaults to 'I can't' despite having
# working tools" failure mode that users observe most often.

_REFUSAL_PATTERNS = (
    "i don't have access",
    "i do not have access",
    "i can't help",
    "i cannot help",
    "i'm unable to",
    "i am unable to",
    "i cannot do that",
    "i can't do that",
    "i don't have the ability",
    "i do not have the ability",
    "i'm not able to",
    "i am not able to",
    "i'm sorry, but i can't",
    "i'm sorry, but i cannot",
    "i apologize, but i can't",
    "i apologize, but i cannot",
    "unfortunately, i can't",
    "unfortunately, i cannot",
    "as an ai",
    "i don't have real-time",
    "i don't have the capability",
)

_REFUSAL_MAX_LEN = 2000  # only scan the first N chars — long answers are rarely refusals


def _looks_like_refusal(result: str) -> bool:
    """Heuristic: does this response refuse to act without having called a tool?"""
    if not result:
        return False
    head = result[:_REFUSAL_MAX_LEN].lower()
    # If the agent clearly called tools (Action: / Observation: markers present),
    # it's not a pre-emptive refusal — it's a reasoned one after trying.
    if "observation:" in head or "action:" in head:
        return False
    return any(p in head for p in _REFUSAL_PATTERNS)


def _build_retry_prompt(original_task: str, agent_tools: list, refusal_text: str) -> str:
    """Compose an explicit re-prompt listing the tools the agent already has."""
    tool_lines = []
    for t in agent_tools[:25]:
        name = getattr(t, "name", "")
        desc = (getattr(t, "description", "") or "")[:120]
        if name:
            tool_lines.append(f"  - `{name}`: {desc}")
    tool_list = "\n".join(tool_lines) or "  (tool list empty — this is a bug)"
    return (
        "Your previous response refused to act, but you DO have tools that can help. "
        "Retry the task — this time call at least one of your tools before answering.\n\n"
        f"Original task:\n{original_task}\n\n"
        f"Your tools (call one of these):\n{tool_list}\n\n"
        f"What you said last time (don't repeat this):\n{refusal_text[:400]}\n\n"
        "Tool-First protocol: try the most plausible tool, inspect the output, chain if needed, "
        "and synthesise an answer from what the tools actually returned. Refusing again without "
        "a tool call is not acceptable."
    )


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

    # Inject plugin tools (MCP, browser, etc.) into the agent.
    # NOTE: The monkey-patched Agent.__init__ already injects these, so
    # only add tools whose names aren't already present (avoids _2 duplicates).
    plugin_tools = get_plugin_tools()
    if plugin_tools:
        existing = list(agent.tools) if agent.tools else []
        existing_names = {getattr(t, "name", "") for t in existing}
        new_plugins = [t for t in plugin_tools if getattr(t, "name", "") not in existing_names]
        if new_plugins:
            agent.tools = existing + new_plugins
    if extra_tools:
        existing = list(agent.tools) if agent.tools else []
        agent.tools = existing + extra_tools

    # Re-cap after late additions.  With strict=False the 20-tool Anthropic
    # limit doesn't apply, but we still cap for token-efficiency and to keep
    # LLM decisions focused.
    import os as _os
    _cap = int(_os.environ.get("MAX_TOOLS_PER_AGENT", "40"))
    if agent.tools and len(agent.tools) > _cap:
        before = len(agent.tools)
        agent.tools = _cap_tools_by_priority(agent.tools, _cap)
        logger.warning(
            f"{crew_name}: capped tools {before} → {len(agent.tools)} "
            f"(MAX_TOOLS_PER_AGENT={_cap})"
        )

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
        verbose=settings.crew_verbose,
    )

    try:
        result = str(crew.kickoff())

        # Tool-First enforcement: if the agent refused without calling tools, retry once
        # with an explicit nudge listing the tools it has.
        if _looks_like_refusal(result) and agent.tools:
            retry_prompt = _build_retry_prompt(task_description, agent.tools, result)
            logger.info(
                f"base_crew: refusal detected in {crew_name}, retrying with tool-first nudge "
                f"({len(agent.tools)} tools available)"
            )
            try:
                retry_task = Task(
                    description=retry_prompt,
                    expected_output=expected_output,
                    agent=agent,
                )
                retry_crew = Crew(
                    agents=[agent],
                    tasks=[retry_task],
                    process=Process.sequential,
                    verbose=settings.crew_verbose,
                )
                retry_result = str(retry_crew.kickoff())
                if retry_result and not _looks_like_refusal(retry_result):
                    result = retry_result
                    record_metric("refusal_retry_success", 1.0, {"crew": crew_name})
                else:
                    record_metric("refusal_retry_failed", 1.0, {"crew": crew_name})
            except Exception:
                logger.debug(f"base_crew: refusal retry in {crew_name} crashed", exc_info=True)

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
    # MCP manager tools (search/add/list/remove MCP servers — self-service)
    register_tool_plugin(
        lambda: __import__("app.tools.mcp_manager_tool", fromlist=["create_mcp_manager_tools"]).create_mcp_manager_tools()
    )

    # Patch crewai.Agent so every agent instance gets plugin tools automatically
    _patch_agent_for_plugins()


_agent_patched = False


def _patch_agent_for_plugins() -> None:
    """Monkey-patch crewai.Agent so every Agent auto-appends plugin tools.

    Hooks the constructor so multi-agent crews (research / media / critic /
    creative / etc.) that construct crewai.Agent directly — without going
    through run_single_agent_crew — still get plugin tools.

    Injection happens BEFORE original __init__ runs, so CrewAI's
    field_validator("tools") processes plugin tools uniformly (wrapping
    langchain-style tools, validating BaseTool subclasses). Post-init
    assignment to self.tools bypasses that validator and caused subtle bugs
    where dynamically-added MCP tools weren't picked up by the executor.
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
        # Pre-init injection — extend kwargs["tools"] before pydantic validates.
        try:
            plugins = get_plugin_tools()
            if plugins:
                existing = list(kwargs.get("tools") or [])
                existing_names = {getattr(t, "name", "") for t in existing}
                additions = [
                    t for t in plugins
                    if getattr(t, "name", "") and getattr(t, "name", "") not in existing_names
                ]
                if additions:
                    kwargs["tools"] = existing + additions
                    logger.debug(
                        f"Agent('{kwargs.get('role', '?')}') + {len(additions)} plugin tools: "
                        f"{[getattr(t, 'name', '?') for t in additions]}"
                    )
        except Exception:
            logger.debug("Agent plugin pre-init failed (non-fatal)", exc_info=True)

        # Tool-count safety cap.  With strict=False (patched below) Anthropic's
        # 20-tool limit no longer applies, but a very large tool list still:
        #   - Burns tokens on every system prompt (each tool schema is ~200
        #     tokens, so 60 tools = ~12k tokens of system-prompt overhead).
        #   - Overwhelms LLM decision-making (more choices = more chance of
        #     picking a marginally-wrong tool).
        # So we still cap, just much more generously than before.  The
        # priority pruning is defensive — if anyone reverts the strict-false
        # patch, this keeps the 20-strict cap from biting immediately.
        try:
            tools = list(kwargs.get("tools") or [])
            MAX_TOOLS_PER_AGENT = int(
                __import__("os").environ.get("MAX_TOOLS_PER_AGENT", "40")
            )
            if len(tools) > MAX_TOOLS_PER_AGENT:
                tools_after = _cap_tools_by_priority(tools, MAX_TOOLS_PER_AGENT)
                dropped = len(tools) - len(tools_after)
                dropped_names = [
                    getattr(t, "name", "?") for t in tools
                    if t not in tools_after
                ][:8]
                logger.warning(
                    f"Agent('{kwargs.get('role', '?')}'): capped tools "
                    f"{len(tools)} → {len(tools_after)} "
                    f"(dropped {dropped}: {dropped_names})"
                )
                kwargs["tools"] = tools_after
        except Exception:
            logger.debug("Agent tool-cap pre-init failed (non-fatal)", exc_info=True)

        # Tool-First affordance: append a short manifest of available tools to the
        # backstory so the LLM sees "I have tools X, Y, Z — USE THEM" in its system
        # prompt. CrewAI already lists tool schemas for function-calling, but this
        # second-person narrative is much stickier for refusal-prone LLMs.
        # Only modify `backstory` if the caller already supplied one — don't add
        # a kwarg the callee doesn't accept.
        try:
            tools = kwargs.get("tools") or []
            if tools and "backstory" in kwargs and kwargs["backstory"]:
                kwargs["backstory"] = _append_tool_manifest(kwargs["backstory"], tools)
        except Exception:
            logger.debug("Agent tool-manifest injection failed (non-fatal)", exc_info=True)

        original_init(self, *args, **kwargs)

    Agent.__init__ = patched_init
    _agent_patched = True
    logger.info("crewai.Agent patched for plugin-tool auto-injection (pre-init)")

    # Also patch tool-schema emission so strict=False is sent to Anthropic.
    # Anthropic's tool_use API caps STRICT tools at 20 per request; non-strict
    # tools have a much higher limit (~100).  CrewAI's agent_utils hard-codes
    # strict=True on every tool schema, which forces the cap.  Flipping to
    # strict=False removes Anthropic's cap, so agents can see the full set of
    # peripheral tools (introspection, tensions, experiential, philosophy)
    # without the 18-tool priority cull in patched_init.
    _patch_crewai_strict_false()


def _patch_crewai_strict_false() -> None:
    """Monkey-patch CrewAI's convert_tools_to_openai_schema so every tool is
    emitted with strict=False.  Unlocks Anthropic's higher tool limit."""
    try:
        import crewai.utilities.agent_utils as _cu
        _original_convert = _cu.convert_tools_to_openai_schema

        def _loose_convert(*args, **kwargs):
            schemas, fns, name_map = _original_convert(*args, **kwargs)
            for s in schemas:
                fn = s.get("function")
                if isinstance(fn, dict) and fn.get("strict") is True:
                    fn["strict"] = False
            return schemas, fns, name_map

        _cu.convert_tools_to_openai_schema = _loose_convert
        logger.info("crewai: tool schemas patched to strict=False (lifts Anthropic 20-tool cap)")
    except Exception:
        logger.debug("crewai strict-false patch failed (non-fatal)", exc_info=True)
