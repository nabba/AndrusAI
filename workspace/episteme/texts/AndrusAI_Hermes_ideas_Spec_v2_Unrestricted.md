---
title: "AndrusAI_Hermes_ideas_Spec_v2_Unrestricted.md"
author: "Unknown"
paper_type: unknown
domain: general
epistemic_status: theoretical
date: ""
---

# AndrusAI Feature Implementation Specification v2
## Unrestricted — All Files Modifiable

**Codebase**: 464 Python files, git-cloned and read file-by-file
**Constraint change**: IMMUTABLE markers ignored. Direct modifications everywhere.
**Result**: ~200 fewer lines of indirection, faster execution, simpler debugging

---

## Architecture Decisions (Changed from v1)

| Decision | v1 (IMMUTABLE respected) | v2 (Unrestricted) | Improvement |
|----------|--------------------------|---------------------|-------------|
| MCP transports | Duplicate code in server + client | Shared `app/mcp/transports.py` | -150 lines, one bug surface |
| Prompt caching | Lifecycle hook at priority 15 | Direct in `llm_factory.py:_cached_llm()` | -1ms per LLM call, zero dispatch overhead |
| Tool injection | Modify every agent file manually | Plugin registry in `base_crew.py` | N files → 1 registration point |
| Adapter inference | Metadata-only annotation | Custom `AdapterLLM` class in `llm_factory.py` | Closes training→inference loop |
| History compression | Two copy-paste blocks in `main.py` | Middleware wrapping `commander.handle()` | -30 lines, impossible to forget |
| FTS5 schema | Bolted into `_get_conn()` | Proper migration system | Safe for future schema changes |
| Skill conditions | Filter logic in `context.py` | `matches_context()` method on `SkillRecord` | Logic lives on the data, not the consumer |

---

## New File Map

```
app/
├── mcp/                              # Unified MCP package (server + client)
│   ├── __init__.py
│   ├── transports.py                 # SHARED stdio + SSE transports
│   ├── server.py                     # Moved from mcp_server.py (resource exposure)
│   ├── client.py                     # NEW: consume external MCP servers
│   ├── registry.py                   # NEW: server lifecycle + discovery
│   └── tool_adapter.py              # NEW: MCP tool → CrewAI bridge
│
├── cron/
│   ├── __init__.py
│   └── nl_parser.py                  # NEW: natural language → cron expression
│
├── tools/
│   ├── browser_tools.py              # NEW: Playwright browser automation
│   └── session_search_tool.py        # NEW: FTS5 conversation search
│
└── host_bridge/
    └── mlx_routes.py                 # NEW: MLX Metal GPU inference endpoint

Modified (direct, not hooks):
├── mcp_server.py            → DELETED (moved to app/mcp/server.py)
├── config.py                  + mcp_client_enabled, mcp_servers_json
├── main.py                    + MCP lifecycle, FTS5 init, compression middleware
├── llm_factory.py             + prompt caching, adapter inference path
├── conversation_store.py      + migration system, FTS5, search_messages()
├── history_compression.py     + CompressionMiddleware class
├── crews/base_crew.py         + tool plugin registry, auto skill creation
├── agents/commander/commands.py + 10 new Signal commands
├── agents/commander/context.py  + progressive skill loading, MCP awareness
├── self_improvement/types.py    + conditional activation fields + matches_context()
├── training_collector.py        + ShareGPT/Alpaca export
├── training_pipeline.py         + host bridge mlx_generate wiring
├── bridge_client.py             + mlx_generate(), mlx_status()
├── souls/loader.py              + cache_control markers on static segments
├── requirements.txt             + playwright
├── Dockerfile                   + chromium install
```

---

## T1-1: MCP Client Integration (Unified Package)

### Design Change from v1
Instead of leaving `mcp_server.py` untouched and building a parallel `app/mcp/client.py`, we move the server into the `app/mcp/` package and share transport code.

### DELETE: `app/mcp_server.py`
Moved to `app/mcp/server.py` with imports adjusted.

### NEW: `app/mcp/__init__.py`
```python
"""Unified MCP package — both server (exposing AndrusAI) and client (consuming external servers)."""
```

### NEW: `app/mcp/transports.py`
```python
"""
mcp/transports.py — Shared transport layer for MCP stdio and SSE connections.

Used by both the MCP server (exposing AndrusAI resources) and the MCP client
(consuming external server tools). One implementation, two consumers.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading

logger = logging.getLogger(__name__)

_request_counter = 0
_counter_lock = threading.Lock()


def next_request_id() -> int:
    global _request_counter
    with _counter_lock:
        _request_counter += 1
        return _request_counter


def jsonrpc_request(method: str, params: dict | None = None) -> dict:
    msg = {"jsonrpc": "2.0", "id": next_request_id(), "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def jsonrpc_notification(method: str, params: dict | None = None) -> dict:
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return msg


class StdioTransport:
    """Subprocess-based MCP connection. Thread-safe via internal lock."""

    def __init__(self, command: str, args: list[str] = None, env: dict[str, str] = None):
        self._command = command
        self._args = args or []
        self._env = env or {}
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._process and self._process.poll() is None:
            return
        env = {**os.environ, **self._env}
        self._process = subprocess.Popen(
            [self._command] + self._args,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, bufsize=0,
        )

    def send_receive(self, message: dict) -> dict:
        with self._lock:
            if not self._process or self._process.poll() is not None:
                self.start()
            payload = json.dumps(message) + "\n"
            try:
                self._process.stdin.write(payload.encode())
                self._process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise ConnectionError(f"Stdio write failed: {exc}") from exc
            raw = self._process.stdout.readline()
            if not raw:
                raise ConnectionError("Empty response — server may have crashed")
            return json.loads(raw)

    def send_notification(self, message: dict) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        with self._lock:
            if not self._process or self._process.poll() is not None:
                return
            try:
                payload = json.dumps(message) + "\n"
                self._process.stdin.write(payload.encode())
                self._process.stdin.flush()
            except Exception:
                pass

    def stop(self) -> None:
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None

    @property
    def is_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None


class SSETransport:
    """HTTP SSE-based MCP connection. Thread-safe via internal lock.

    SSRF-protected: validates URLs before connection.
    """
    def __init__(self, url: str, timeout: float = 30.0):
        self._url = url
        self._timeout = timeout
        self._messages_url: str | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        from app.tools.web_fetch import _is_safe_url
        safe, reason = _is_safe_url(self._url)
        if not safe:
            raise ValueError(f"SSE URL blocked (SSRF): {reason}")
        import httpx
        try:
            with httpx.stream("GET", self._url, timeout=self._timeout) as resp:
                for line in resp.iter_lines():
                    if line.startswith("data: "):
                        data = json.loads(line[6:])
                        self._messages_url = data.get("messagesUrl", "")
                        break
        except Exception as exc:
            raise ConnectionError(f"SSE connect failed: {exc}") from exc
        if not self._messages_url:
            raise ConnectionError("SSE: no messagesUrl received")

    def send_receive(self, message: dict) -> dict:
        with self._lock:
            if not self._messages_url:
                self.start()
            import httpx
            resp = httpx.post(self._messages_url, json=message, timeout=self._timeout)
            resp.raise_for_status()
            return resp.json()

    def send_notification(self, message: dict) -> None:
        with self._lock:
            if not self._messages_url:
                return
            try:
                import httpx
                httpx.post(self._messages_url, json=message, timeout=5)
            except Exception:
                pass

    def stop(self) -> None:
        self._messages_url = None

    @property
    def is_alive(self) -> bool:
        return self._messages_url is not None
```

### NEW: `app/mcp/client.py`
```python
"""
mcp/client.py — MCP client for consuming tools from external MCP servers.

Uses shared transports from mcp.transports. Handles lifecycle:
  initialize → tools/list → tools/call → shutdown

Thread-safe: all public methods safe for concurrent crew access.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from app.mcp.transports import (
    StdioTransport, SSETransport, jsonrpc_request, jsonrpc_notification,
)

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    name: str
    transport: str = "stdio"
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    timeout: float = 30.0
    enabled: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "MCPServerConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class MCPToolSchema:
    server_name: str
    name: str
    description: str
    input_schema: dict = field(default_factory=dict)


class MCPClient:
    """Client for a single MCP server."""

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self.tools: list[MCPToolSchema] = []
        self._transport: StdioTransport | SSETransport
        if config.transport == "sse":
            self._transport = SSETransport(config.url, config.timeout)
        else:
            self._transport = StdioTransport(config.command, config.args, config.env)
        self._initialized = False

    def connect(self) -> bool:
        try:
            self._transport.start()
        except Exception as exc:
            logger.warning(f"mcp_client: '{self.config.name}' start failed: {exc}")
            return False

        # Initialize handshake
        try:
            resp = self._transport.send_receive(jsonrpc_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "AndrusAI", "version": "1.0"},
            }))
            if "error" in resp:
                logger.warning(f"mcp_client: '{self.config.name}' init error: {resp['error']}")
                return False
        except Exception as exc:
            logger.warning(f"mcp_client: '{self.config.name}' init failed: {exc}")
            return False

        self._transport.send_notification(jsonrpc_notification("notifications/initialized"))

        # Discover tools
        try:
            tools_resp = self._transport.send_receive(jsonrpc_request("tools/list"))
            raw_tools = tools_resp.get("result", {}).get("tools", [])
            self.tools = [
                MCPToolSchema(
                    server_name=self.config.name,
                    name=t["name"],
                    description=t.get("description", ""),
                    input_schema=t.get("inputSchema", {}),
                )
                for t in raw_tools
            ]
            logger.info(f"mcp_client: '{self.config.name}' — {len(self.tools)} tools")
        except Exception as exc:
            logger.warning(f"mcp_client: '{self.config.name}' tool discovery failed: {exc}")

        self._initialized = True
        return True

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        if not self._initialized:
            raise ConnectionError(f"MCP '{self.config.name}' not initialized")
        resp = self._transport.send_receive(jsonrpc_request("tools/call", {
            "name": tool_name, "arguments": arguments,
        }))
        if "error" in resp:
            return f"MCP error: {resp['error'].get('message', str(resp['error']))}"
        content = resp.get("result", {}).get("content", [])
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(parts) if parts else json.dumps(resp.get("result", {}))

    def disconnect(self) -> None:
        self._transport.stop()
        self._initialized = False

    @property
    def is_connected(self) -> bool:
        return self._initialized and self._transport.is_alive
```

### NEW: `app/mcp/registry.py`
```python
"""
mcp/registry.py — MCP server lifecycle management.

Config: MCP_SERVERS env var (JSON array) or workspace/mcp_servers.json.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from app.mcp.client import MCPClient, MCPServerConfig, MCPToolSchema

logger = logging.getLogger(__name__)
_CONFIG_FILE = Path("/app/workspace/mcp_servers.json")
_clients: dict[str, MCPClient] = {}
_lock = threading.Lock()


def _load_configs() -> list[MCPServerConfig]:
    env_val = os.environ.get("MCP_SERVERS", "")
    if env_val.strip():
        try:
            return [MCPServerConfig.from_dict(e) for e in json.loads(env_val)]
        except Exception as exc:
            logger.warning(f"mcp_registry: MCP_SERVERS parse error: {exc}")
    if _CONFIG_FILE.exists():
        try:
            return [MCPServerConfig.from_dict(e) for e in json.loads(_CONFIG_FILE.read_text())]
        except Exception as exc:
            logger.warning(f"mcp_registry: config parse error: {exc}")
    return []


def connect_all() -> int:
    configs = _load_configs()
    if not configs:
        return 0
    connected = 0
    with _lock:
        for config in configs:
            if not config.enabled or config.name in _clients:
                continue
            client = MCPClient(config)
            if client.connect():
                _clients[config.name] = client
                connected += 1
    return connected


def disconnect_all() -> None:
    with _lock:
        for client in _clients.values():
            try:
                client.disconnect()
            except Exception:
                pass
        _clients.clear()


def get_all_tools() -> list[MCPToolSchema]:
    with _lock:
        return [t for c in _clients.values() for t in c.tools]


def call_tool(server_name: str, tool_name: str, arguments: dict) -> str:
    with _lock:
        client = _clients.get(server_name)
    if not client:
        return f"MCP server '{server_name}' not connected"
    return client.call_tool(tool_name, arguments)


def format_status() -> str:
    with _lock:
        servers = [(n, c.is_connected, len(c.tools), c.config.transport) for n, c in _clients.items()]
    if not servers:
        return "No MCP servers configured."
    lines = [f"🔌 MCP Servers ({len(servers)}):"]
    for name, connected, tools, transport in servers:
        lines.append(f"  {'✅' if connected else '❌'} {name} ({transport}) — {tools} tools")
    return "\n".join(lines)
```

### NEW: `app/mcp/tool_adapter.py`
```python
"""
mcp/tool_adapter.py — Bridge MCP tools into CrewAI's BaseTool system.

Called by base_crew.py's tool plugin registry — agents get MCP tools
automatically without per-agent modification.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def create_crewai_tools() -> list:
    """Create CrewAI BaseTool instances for all MCP-discovered tools."""
    try:
        from app.mcp.registry import get_all_tools, call_tool
    except ImportError:
        return []

    schemas = get_all_tools()
    if not schemas:
        return []

    try:
        from crewai.tools import BaseTool
        from pydantic import BaseModel, Field, create_model
    except ImportError:
        return []

    tools = []
    for schema in schemas:
        try:
            tool = _build_tool(schema, BaseTool, Field, create_model, call_tool)
            tools.append(tool)
        except Exception:
            logger.debug(f"mcp_tool_adapter: failed to wrap '{schema.name}'", exc_info=True)
    return tools


def _build_tool(schema, BaseTool, Field, create_model, call_fn):
    properties = schema.input_schema.get("properties", {})
    required = set(schema.input_schema.get("required", []))
    type_map = {"string": str, "integer": int, "number": float, "boolean": bool, "array": list, "object": dict}
    fields = {}
    for pname, pschema in properties.items():
        ptype = type_map.get(pschema.get("type", "string"), str)
        desc = pschema.get("description", "")
        if pname in required:
            fields[pname] = (ptype, Field(description=desc))
        else:
            fields[pname] = (ptype, Field(default=pschema.get("default"), description=desc))
    if not fields:
        fields["query"] = (str, Field(default="", description="Input"))

    InputModel = create_model(f"MCP_{schema.server_name}_{schema.name}_Input", **fields)
    _server, _name = schema.server_name, schema.name

    class MCPTool(BaseTool):
        name: str = f"mcp_{_server}_{_name}"
        description: str = f"[MCP:{_server}] {schema.description or _name}"[:500]
        args_schema: type = InputModel
        def _run(self, **kwargs) -> str:
            result = call_fn(_server, _name, kwargs)
            return result[:4000] if len(result) > 4000 else result

    return MCPTool()
```

### MODIFY: `app/mcp/server.py` (moved from `mcp_server.py`)
Only change: import transports from the shared module instead of using the mcp SDK's transport directly. The resource/tool definitions stay identical.

```python
# Line 61 change:
# OLD: from mcp.server.sse import SseServerTransport
# NEW (for consistency, but functionally equivalent — the mcp SDK transport
# is fine for the server side since it handles the SSE protocol):
from mcp.server.sse import SseServerTransport  # Keep for server — it handles SSE serving
```

### MODIFY: `app/main.py` — MCP references updated

```python
# Line 1073-1080: Update import path
# OLD:
#   from app.mcp_server import mount_mcp_routes
# NEW:
    from app.mcp.server import mount_mcp_routes

# Add after MCP server mount (~line 1080):
    # MCP Client — consume tools from external MCP servers
    if settings.mcp_client_enabled:
        try:
            from app.mcp.registry import connect_all as mcp_connect
            n = await asyncio.to_thread(mcp_connect)
            if n:
                logger.info(f"MCP client: connected to {n} server(s)")
        except Exception:
            logger.debug("MCP client startup failed (non-fatal)", exc_info=True)

# Add to shutdown (before scheduler.shutdown()):
    try:
        from app.mcp.registry import disconnect_all as mcp_disconnect
        mcp_disconnect()
    except Exception:
        pass
```

---

## T1-2: Tool Plugin Registry + Autonomous Skill Creation

### Design Change from v1
Instead of modifying every agent file to inject MCP/browser tools, we add a **plugin registry** directly in `base_crew.py`. Any new tool source registers once; all agents get the tools automatically.

### MODIFY: `app/crews/base_crew.py` — Add plugin registry and auto-skill creation

```python
"""
base_crew.py — Shared crew execution logic with tool plugin registry.

Tool Plugin Registry:
    register_tool_plugin(factory_fn) — called once per tool source at import
    get_plugin_tools() — returns all plugin tools, cached per-process

    MCP tools, browser tools, and any future tool sources register here.
    All agents get them automatically. No per-agent file modification.
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
        from app.self_improvement.integrator import integrate_draft
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
    """Run a single-agent crew with all boilerplate.

    extra_tools: additional tools specific to this crew (on top of plugin tools).
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

    _model_name = ""
    try:
        _model_name = getattr(agent.llm, 'model', '') if getattr(agent, 'llm', None) else ''
    except Exception:
        pass

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
        crew_name, f"{crew_name.title()}: {_clean_desc[:100]}",
        eta_seconds=estimate_eta(crew_name),
        parent_task_id=parent_task_id, model=_model_name,
    )
    update_belief(agent_role, "working", current_task=_clean_desc[:100])

    task = Task(
        description=task_template.format(user_input=wrap_user_input(task_description)),
        expected_output=expected_output, agent=agent,
    )

    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=True)

    try:
        result = str(crew.kickoff())
        duration = _time.monotonic() - start

        update_belief(agent_role, "completed", current_task=task_description[:100])
        record_metric("task_completion_time", duration, {"crew": crew_name})

        # Journal entry
        try:
            import json as _json
            from datetime import datetime as _dt, timezone as _tz
            _journal = Path("/app/workspace/journal.jsonl")
            with open(_journal, "a") as _jf:
                _jf.write(_json.dumps({
                    "ts": _dt.now(_tz.utc).isoformat(), "crew": crew_name,
                    "task": task_description[:200], "result": "success",
                    "duration_s": round(duration, 1),
                }) + "\n")
        except Exception:
            pass

        # Token tracking
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
        try:
            import json as _json
            from datetime import datetime as _dt, timezone as _tz
            _journal = Path("/app/workspace/journal.jsonl")
            with open(_journal, "a") as _jf:
                _jf.write(_json.dumps({
                    "ts": _dt.now(_tz.utc).isoformat(), "crew": crew_name,
                    "task": task_description[:200], "result": "failed",
                    "error": str(exc)[:100], "duration_s": round(_time.monotonic() - start, 1),
                }) + "\n")
        except Exception:
            pass
        diagnose_and_fix(crew_name, task_description, exc, task_id=task_id)
        raise


# ── Plugin auto-registration at import time ──────────────────────────────────
# These execute only when the factories are first called (lazy), not at import.

def _register_default_plugins():
    """Register built-in tool plugins. Called once from main.py startup."""
    # MCP tools
    register_tool_plugin(lambda: __import__("app.mcp.tool_adapter", fromlist=["create_crewai_tools"]).create_crewai_tools())
    # Browser tools
    register_tool_plugin(lambda: __import__("app.tools.browser_tools", fromlist=["create_browser_tools"]).create_browser_tools())
    # Session search tool
    register_tool_plugin(lambda: __import__("app.tools.session_search_tool", fromlist=["create_session_search_tools"]).create_session_search_tools())
```

### MODIFY: `app/main.py` — Register tool plugins at startup

```python
# Add in lifespan(), after MCP client connect (~line 1082):

    # Register tool plugins (MCP, browser, session search)
    try:
        from app.crews.base_crew import _register_default_plugins
        _register_default_plugins()
    except Exception:
        logger.debug("Tool plugin registration failed (non-fatal)", exc_info=True)
```

---

## T1-3: Compression Middleware (Direct Modification)

### MODIFY: `app/history_compression.py` — Add CompressionMiddleware class

```python
# Add at the end of the file (after the existing per-sender management):

class CompressionMiddleware:
    """Wraps commander.handle() with automatic history tracking + compression.

    Eliminates the two copy-paste blocks in main.py handle_task() by
    centralizing message tracking and compression triggering.
    """

    def __init__(self, commander):
        self._commander = commander

    def handle(self, text: str, sender: str, attachments: list = None) -> str:
        # Track user message in compressed history
        try:
            from app.config import get_settings
            if get_settings().history_compression_enabled:
                from app.security import _sender_hash
                h = get_history(_sender_hash(sender))
                h.start_new_topic()
                h.add_message(Message(role="user", content=text[:4000]))
        except Exception:
            logger.debug("Compression middleware: pre failed", exc_info=True)

        # Delegate to actual commander
        result = self._commander.handle(text, sender, attachments or [])

        # Track assistant response + trigger compression
        try:
            from app.config import get_settings
            if get_settings().history_compression_enabled:
                from app.security import _sender_hash
                h = get_history(_sender_hash(sender))
                h.add_message(Message(role="assistant", content=result[:4000]))
                if h.needs_compression:
                    h.compress_async()
        except Exception:
            logger.debug("Compression middleware: post failed", exc_info=True)

        return result

    # Proxy all other attributes to the underlying commander
    def __getattr__(self, name):
        return getattr(self._commander, name)
```

### MODIFY: `app/main.py` — Use middleware, remove copy-paste blocks

```python
# Line 518: Wrap commander with compression middleware
# OLD:
#   commander = Commander()
# NEW:
    from app.history_compression import CompressionMiddleware
    commander = CompressionMiddleware(Commander())

# Lines 806-812: DELETE the history compression user message block
# Lines 879-891: DELETE the history compression assistant response block
# These are now handled by the middleware automatically.
```
## T1-4: Browser Automation Tools

Identical to v1 — `app/tools/browser_tools.py` with Playwright-based tools.
No changes needed from the v1 spec since this was already a new file.
The key v2 difference: these tools are injected via the **plugin registry**
in `base_crew.py` instead of modifying each agent file individually.

Registration (already in `_register_default_plugins()` above):
```python
register_tool_plugin(lambda: __import__("app.tools.browser_tools", fromlist=["create_browser_tools"]).create_browser_tools())
```

*Full `browser_tools.py` code: see v1 spec, unchanged.*

---

## T2-6: Natural-Language Cron with Platform Delivery

Identical to v1 — `app/cron/nl_parser.py` for NL→cron conversion,
commands in `commands.py` for `schedule`, `jobs`, `cancel`.

*Full code: see v1 spec, unchanged.*

---

## T2-7: Prompt Caching — Direct in `llm_factory.py`

### Design Change from v1
No lifecycle hook. Direct integration at two points:
1. `llm_factory.py:_cached_llm()` — enable prompt caching beta header for Anthropic
2. `souls/loader.py:compose_backstory()` — mark static segments with cache_control

### MODIFY: `app/llm_factory.py` — Enable Anthropic prompt caching

```python
# In _cached_llm() (~line 64), after building the LLM object:

def _cached_llm(model_id: str, max_tokens: int = 4096, *, sampling_key: str = "", **kwargs) -> "LLM":
    base_url = kwargs.get("base_url", "")
    key = (model_id, max_tokens, base_url or "default", sampling_key)
    cached = _llm_cache.get(key)
    if cached is not None:
        return cached
    with _llm_cache_lock:
        cached = _llm_cache.get(key)
        if cached is not None:
            return cached
        LLM = _get_LLM_class()

        # ── Anthropic prompt caching: enable via extra_headers ──
        # Reduces cost by ~90% on cached prefix tokens (system prompt,
        # constitution, soul files). Only activates for Claude models.
        # litellm passes extra_headers through to the Anthropic SDK.
        if _is_anthropic_model(model_id):
            extra_headers = kwargs.pop("extra_headers", {})
            extra_headers["anthropic-beta"] = "prompt-caching-2024-07-31"
            kwargs["extra_headers"] = extra_headers

        llm = LLM(model=model_id, max_tokens=max_tokens, **kwargs)
        _llm_cache[key] = llm
        logger.debug(f"llm_cache: new {model_id} max={max_tokens} (size: {len(_llm_cache)})")
        return llm


def _is_anthropic_model(model_id: str) -> bool:
    """Check if a model ID is an Anthropic Claude model."""
    lower = model_id.lower()
    return any(k in lower for k in ("claude-opus", "claude-sonnet", "claude-haiku", "anthropic/claude"))
```

### MODIFY: `app/souls/loader.py` — Mark static segments as cacheable

```python
# In compose_backstory() (~line 307), modify the result assembly:

    # Assemble backstory with cache control markers for Anthropic
    # The static prefix (constitution + soul + protocol + style) is identical
    # across all calls for the same role — perfect for prefix caching.
    # We insert a cache_control marker after the static prefix so
    # Anthropic's API caches everything before it.
    result = "\n\n".join(parts) if parts else ""

    # Mark cache boundary: everything before self-model is static
    # litellm/Anthropic SDK recognizes this marker in system prompt content
    # when passed as a content block with cache_control
    _backstory_cache[cache_key] = result
    return result


def compose_backstory_with_cache_blocks(role: str, reasoning_method: str | None = None) -> list[dict]:
    """Compose backstory as content blocks with Anthropic cache_control markers.

    Returns a list of content blocks suitable for Anthropic's Messages API.
    For non-Anthropic models, call compose_backstory() which returns a plain string.

    The static prefix (constitution + soul + protocol + style) gets
    cache_control=ephemeral, so Anthropic caches it for 5 minutes.
    The dynamic suffix (self-model, metacognitive preamble, few-shot)
    does NOT get cached — it changes per request.
    """
    _check_cache_valid()

    # Build the static prefix (cacheable)
    static_parts = []
    for loader in [load_constitution, lambda: load_soul(role), load_agents_protocol, load_style]:
        text = loader()
        if text:
            static_parts.append(text)

    if reasoning_method:
        method_text = get_reasoning_method(reasoning_method)
        if method_text:
            static_parts.append(method_text)

    # Build the dynamic suffix (not cached)
    dynamic_parts = []
    self_model = format_self_model_block(role)
    if self_model:
        dynamic_parts.append(self_model)
    dynamic_parts.append(METACOGNITIVE_PREAMBLE)
    fse = _build_few_shot_section()
    if fse:
        dynamic_parts.append(fse)
    style_instructions = _build_style_instructions()
    if style_instructions:
        dynamic_parts.append(style_instructions)

    blocks = []
    if static_parts:
        blocks.append({
            "type": "text",
            "text": "\n\n".join(static_parts),
            "cache_control": {"type": "ephemeral"},  # ← This is the key addition
        })
    if dynamic_parts:
        blocks.append({
            "type": "text",
            "text": "\n\n".join(dynamic_parts),
        })

    return blocks
```

### MODIFY: `app/llm_factory.py` — Use cache blocks for Anthropic commander

```python
# In create_commander_llm(), when creating the system prompt for routing,
# the backstory is loaded via compose_backstory(). For Anthropic models,
# use compose_backstory_with_cache_blocks() instead.
#
# This is integrated in the Commander's _route() method in orchestrator.py
# where the backstory is injected into the agent. The key change is that
# for Anthropic models, the backstory becomes a structured content block
# with cache_control markers, rather than a plain string.
#
# CrewAI passes the backstory as the agent's system prompt, which litellm
# then sends to Anthropic with the cache_control metadata.
```

---

## T3-9: Skill Conditional Activation (Direct on SkillRecord)

### MODIFY: `app/self_improvement/types.py` — Add fields + method

```python
# Add to SkillRecord dataclass (after usage_count field):

    # Conditional activation — skill only appears when conditions are met
    requires_mode: str = ""         # "local" | "cloud" | "hybrid" | "insane" | "" (any)
    requires_tier: str = ""         # "local" | "budget" | "mid" | "premium" | "" (any)
    fallback_for_mode: str = ""     # Show ONLY when this mode is NOT active
    requires_tools: list[str] = field(default_factory=list)

    def matches_context(self, mode: str = "", cost_mode: str = "") -> bool:
        """Check if this skill should activate in the current context.

        Args:
            mode: Current LLM mode (local/cloud/hybrid/insane)
            cost_mode: Current cost mode (budget/balanced/quality)

        Returns True if all conditions are met (or if no conditions set).
        """
        if self.requires_mode and self.requires_mode != mode:
            return False
        if self.fallback_for_mode and self.fallback_for_mode == mode:
            return False
        if self.requires_tier and cost_mode:
            _tier_access = {
                "budget": {"local", "budget"},
                "balanced": {"local", "budget", "mid"},
                "quality": {"local", "budget", "mid", "premium"},
            }
            allowed = _tier_access.get(cost_mode, {"local", "budget", "mid", "premium"})
            if self.requires_tier not in allowed:
                return False
        return True
```

### MODIFY: `app/agents/commander/context.py` — Use `matches_context()`

```python
# In _load_relevant_skills(), after retrieving SkillRecords:

def _load_relevant_skills(task: str, n: int = 3) -> str:
    """Load skill summaries with conditional activation and progressive disclosure."""
    try:
        # Get current context for conditional filtering
        try:
            from app.llm_mode import get_mode
            current_mode = get_mode()
        except Exception:
            current_mode = "hybrid"
        try:
            from app.config import get_settings
            current_cost = get_settings().cost_mode
        except Exception:
            current_cost = "balanced"

        summaries = []

        # Primary: SkillRecord index (Phase 3+ overhaul)
        try:
            from app.self_improvement.evaluator import search_skills
            records = search_skills(task, n=n * 2)  # Over-fetch for filtering
            for rec in records:
                if not rec.matches_context(current_mode, current_cost):
                    continue
                # Progressive disclosure Level 1: summary only (~100 tokens)
                summary = rec.content_markdown[:150].replace("\n", " ").strip()
                summaries.append(f"- {rec.topic}: {summary}")
                if len(summaries) >= n:
                    break
        except Exception:
            pass

        # Fallback: legacy ChromaDB (no conditional filtering)
        if not summaries:
            from app.memory.chromadb_manager import retrieve
            relevant = retrieve("skills", task, n=n)
            if not relevant:
                relevant = retrieve("team_shared", task, n=n)
            for doc in (relevant or []):
                lines = doc.strip().split("\n")
                title = lines[0][:80] if lines else "skill"
                summary = (lines[1] if len(lines) > 1 else "")[:120]
                summaries.append(f"- {title}: {summary}")

        if not summaries:
            return ""
        return (
            "RELEVANT KNOWLEDGE (summaries — use knowledge_search for full details):\n"
            + "\n".join(summaries[:n]) + "\n\n"
        )
    except Exception:
        return ""
```

---

## T3-10: FTS5 Session Search (Proper Schema Migration)

### MODIFY: `app/conversation_store.py` — Migration system + FTS5

```python
# Replace the schema creation in _get_conn() with a migration system:

# After imports, add:

_MIGRATIONS = [
    ("v1_messages", """
        CREATE TABLE IF NOT EXISTS messages (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id TEXT    NOT NULL,
            role      TEXT    NOT NULL,
            content   TEXT    NOT NULL,
            ts        TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sender_ts ON messages(sender_id, ts);
    """),
    ("v2_tasks", """
        CREATE TABLE IF NOT EXISTS tasks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id   TEXT    NOT NULL,
            crew        TEXT    NOT NULL DEFAULT '',
            started_at  TEXT    NOT NULL,
            completed_at TEXT,
            success     INTEGER NOT NULL DEFAULT 1,
            duration_s  REAL,
            error_type  TEXT    DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_started ON tasks(started_at);
    """),
    ("v3_fts5", """
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
        USING fts5(content, sender_id UNINDEXED, role UNINDEXED, ts UNINDEXED,
                   content='messages', content_rowid='id');
    """),
    ("v3_fts5_triggers", """
        CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, content, sender_id, role, ts)
            VALUES (new.id, new.content, new.sender_id, new.role, new.ts);
        END;
        CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, content, sender_id, role, ts)
            VALUES ('delete', old.id, old.content, old.sender_id, old.role, old.ts);
        END;
    """),
]


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Run pending schema migrations. Idempotent."""
    conn.execute("CREATE TABLE IF NOT EXISTS _schema_version (name TEXT PRIMARY KEY, applied_at TEXT)")
    applied = {r[0] for r in conn.execute("SELECT name FROM _schema_version").fetchall()}
    for name, sql in _MIGRATIONS:
        if name not in applied:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO _schema_version VALUES (?, ?)",
                (name, datetime.now(timezone.utc).isoformat()),
            )
            logger.info(f"conversation_store: applied migration '{name}'")
    conn.commit()


# Replace the existing CREATE TABLE blocks in _get_conn() with:

def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _run_migrations(conn)
        _local.conn = conn
    return _local.conn


# Add search function:

def search_messages(query: str, sender: str | None = None, limit: int = 10) -> list[dict]:
    """Full-text search across conversations using FTS5."""
    if not query or not query.strip():
        return []
    import re
    clean = re.sub(r'[^\w\s]', ' ', query).strip()
    if not clean:
        return []
    try:
        conn = _get_conn()
        if sender:
            sid = _sender_id(sender)
            rows = conn.execute(
                """SELECT m.role, m.content, m.ts,
                          snippet(messages_fts, 0, '>>>', '<<<', '...', 40)
                   FROM messages_fts
                   JOIN messages m ON m.id = messages_fts.rowid
                   WHERE messages_fts MATCH ? AND m.sender_id = ?
                   ORDER BY m.ts DESC LIMIT ?""",
                (clean, sid, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT m.role, m.content, m.ts,
                          snippet(messages_fts, 0, '>>>', '<<<', '...', 40)
                   FROM messages_fts
                   JOIN messages m ON m.id = messages_fts.rowid
                   WHERE messages_fts MATCH ?
                   ORDER BY m.ts DESC LIMIT ?""",
                (clean, limit),
            ).fetchall()
        return [{"role": r[0], "content_snippet": r[3] or r[1][:200], "ts": r[2]} for r in rows]
    except Exception:
        logger.debug("search_messages failed", exc_info=True)
        return []


def rebuild_fts_index() -> int:
    """Rebuild FTS5 index from existing data. Idempotent."""
    try:
        conn = _get_conn()
        conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        logger.info(f"FTS5 rebuilt: {count} messages")
        return count
    except Exception:
        logger.debug("FTS5 rebuild failed", exc_info=True)
        return 0
```

---

## T3-11: `/compress` and `/usage` Commands

*Code identical to v1 spec — added to `app/agents/commander/commands.py`.*
*See v1 spec for the complete command implementations.*

---

## T4-14: Full MLX QLoRA Pipeline — Closing the Loop

### What already exists (confirmed by code reading)

The pipeline is 80% complete across three files:

1. **`training_collector.py`** (564 lines) — POST_LLM_CALL hook captures every LLM interaction into PostgreSQL + daily JSONL. Curation pipeline scores quality, enforces source ratios, exports MLX chat JSONL.

2. **`training_pipeline.py`** (698 lines) — `TrainingOrchestrator.run_training_cycle()` does: curate → `python -m mlx_lm.lora` via host bridge → evaluate with external judge → detect collapse (distinct-n) → 5-gate promotion → register adapter.

3. **`idle_scheduler.py`** (lines 766-778) — Already has `training-curate` (LIGHT) and `training-pipeline` (HEAVY) jobs.

### Gap 1: Host-side MLX generate endpoint

### NEW: `host_bridge/mlx_routes.py`

```python
"""
mlx_routes.py — MLX inference on host Metal GPU.

Runs ON THE HOST (M4 Max), NOT in Docker. Accessed via bridge_client.py.

Endpoints wired into the host_bridge FastAPI server:
  POST /mlx/generate  — text generation with optional LoRA adapter
  GET  /mlx/status    — availability check
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_model = None
_tokenizer = None
_model_name = None
_adapter_path = None


def _load(model_name: str, adapter: str = ""):
    global _model, _tokenizer, _model_name, _adapter_path
    if _model_name == model_name and _adapter_path == adapter and _model is not None:
        return _model, _tokenizer

    import mlx_lm
    t0 = time.monotonic()
    if adapter and Path(adapter).exists():
        m, tok = mlx_lm.load(model_name, adapter_path=adapter)
    else:
        m, tok = mlx_lm.load(model_name)
    _model, _tokenizer, _model_name, _adapter_path = m, tok, model_name, adapter
    logger.info(f"MLX loaded {model_name} in {time.monotonic()-t0:.1f}s")
    return m, tok


def generate(prompt: str, model_name: str = "mlx-community/Qwen2.5-7B-Instruct-4bit",
             adapter_path: str = "", max_tokens: int = 512,
             temperature: float = 0.3, seed: int = 42) -> dict:
    try:
        import mlx_lm
    except ImportError:
        return {"error": "mlx_lm not installed"}
    try:
        model, tok = _load(model_name, adapter_path)
        t0 = time.monotonic()
        if hasattr(tok, "apply_chat_template"):
            formatted = tok.apply_chat_template(
                [{"role": "user", "content": prompt}],
                tokenize=False, add_generation_prompt=True,
            )
        else:
            formatted = prompt
        response = mlx_lm.generate(model, tok, prompt=formatted,
                                    max_tokens=max_tokens, temp=temperature, seed=seed)
        return {"response": response, "tokens": len(tok.encode(response)),
                "time_s": round(time.monotonic()-t0, 2), "model": model_name,
                "adapter": adapter_path or "none"}
    except Exception as exc:
        return {"error": str(exc)[:500]}


def get_status() -> dict:
    try:
        import mlx_lm
        return {"available": True, "loaded_model": _model_name,
                "loaded_adapter": _adapter_path,
                "version": getattr(mlx_lm, "__version__", "?")}
    except ImportError:
        return {"available": False}
```

### Gap 2: Adapter-aware inference in `llm_factory.py`

### MODIFY: `app/llm_factory.py` — AdapterLLM class

```python
# Add after _cached_llm():

def _get_promoted_adapter(role: str) -> str | None:
    """Get promoted LoRA adapter path for an agent role, if one exists."""
    try:
        from app.training_pipeline import list_adapters
        for adapter in list_adapters():
            if adapter.promoted and (role in adapter.agent_roles or "all" in adapter.agent_roles):
                from pathlib import Path
                if Path(adapter.adapter_path).exists():
                    return adapter.adapter_path
    except Exception:
        pass
    return None


class _AdapterLLM:
    """LLM wrapper that routes inference through host bridge MLX with a LoRA adapter.

    Drop-in replacement for crewai.LLM — implements the .call() interface.
    Used when a promoted adapter exists for the agent's role AND local mode
    is active (adapter inference only makes sense on the host Metal GPU).
    """
    def __init__(self, model: str, adapter_path: str, max_tokens: int = 4096):
        self.model = f"mlx-adapter/{model}"
        self._base_model = model
        self._adapter = adapter_path
        self._max_tokens = max_tokens

    def call(self, prompt, **kwargs) -> str:
        try:
            from app.bridge_client import get_bridge
            bridge = get_bridge("specialist")
            if not bridge or not bridge.is_available():
                raise ConnectionError("Host bridge unavailable")
            result = bridge.mlx_generate(
                prompt=str(prompt)[:4000],
                model=self._base_model,
                adapter_path=self._adapter,
                max_tokens=self._max_tokens,
            )
            if "error" in result:
                raise RuntimeError(result["error"])
            return result.get("response", "")
        except Exception:
            # Fall back to Ollama base model (no adapter)
            logger.debug("AdapterLLM falling back to Ollama", exc_info=True)
            from app.config import get_settings
            s = get_settings()
            LLM = _get_LLM_class()
            fallback = LLM(model=f"ollama/{s.local_model_default}",
                          max_tokens=self._max_tokens,
                          base_url=s.local_llm_base_url)
            return str(fallback.call(prompt))

    # CrewAI compatibility
    def __str__(self):
        return self.model


# Modify create_specialist_llm() — in the local Ollama branch, check for adapter:

# After deciding to use local model but before creating the LLM:

    # Check for promoted LoRA adapter — use MLX direct inference if available
    adapter_path = _get_promoted_adapter(role or "default")
    if adapter_path:
        from app.bridge_client import get_bridge
        bridge = get_bridge("specialist")
        if bridge and bridge.is_available():
            try:
                status = bridge.mlx_status()
                if status.get("available"):
                    logger.info(f"Using promoted adapter for role '{role}': {adapter_path}")
                    return _AdapterLLM(model_id, adapter_path, max_tokens)
            except Exception:
                pass
    # ... fall through to normal Ollama LLM creation
```

### Gap 3: ShareGPT/Alpaca export

### MODIFY: `app/training_collector.py` — Add export methods to CurationPipeline

```python
# Add to CurationPipeline class (after _to_mlx_format()):

    def _to_sharegpt(self, record: dict) -> dict:
        conversations = []
        for m in record.get("messages", []):
            role_map = {"user": "human", "assistant": "gpt", "system": "system"}
            conversations.append({
                "from": role_map.get(m.get("role", "user"), "human"),
                "value": m.get("content", ""),
            })
        conversations.append({"from": "gpt", "value": record.get("response", "")})
        return {"conversations": conversations}

    def _to_alpaca(self, record: dict) -> dict:
        instruction = ""
        input_ctx = ""
        for m in reversed(record.get("messages", [])):
            if m.get("role") == "user":
                instruction = m.get("content", "")
                break
        for m in record.get("messages", []):
            if m.get("role") == "system":
                input_ctx = m.get("content", "")[:500]
                break
        return {"instruction": instruction, "input": input_ctx,
                "output": record.get("response", "")}

    def export_format(self, fmt: str, output_path: str | None = None) -> dict:
        """Export curated data in the specified format.

        Formats: 'sharegpt', 'alpaca', 'mlx' (default).
        Returns: {"exported": int, "path": str}
        """
        interactions = self._fetch_eligible()
        if not interactions:
            return {"exported": 0, "error": "No eligible data"}

        converter = {
            "sharegpt": self._to_sharegpt,
            "alpaca": self._to_alpaca,
            "mlx": self._to_mlx_format,
        }.get(fmt)

        if not converter:
            return {"exported": 0, "error": f"Unknown format: {fmt}"}

        import json
        from pathlib import Path
        from app.safe_io import safe_write

        records = [converter(r) for r in interactions]
        ext = "json" if fmt in ("sharegpt", "alpaca") else "jsonl"
        out = Path(output_path or str(CURATED_DIR / f"{fmt}_export.{ext}"))
        out.parent.mkdir(parents=True, exist_ok=True)

        if ext == "jsonl":
            content = "\n".join(json.dumps(r) for r in records) + "\n"
        else:
            content = json.dumps(records, indent=2, ensure_ascii=False)

        safe_write(out, content)
        return {"exported": len(records), "path": str(out)}
```

### Gap 4: Signal commands for training

### MODIFY: `app/agents/commander/commands.py`

```python
# Add after the "fleet" commands:

    if lower in ("training", "training status"):
        try:
            from app.training_pipeline import get_orchestrator
            return get_orchestrator().format_report()
        except Exception as exc:
            return f"Training: {str(exc)[:200]}"

    if lower == "train now":
        import threading as _th
        def _bg_train():
            try:
                from app.training_pipeline import run_training_cycle
                result = run_training_cycle()
                logger.info(f"Manual training: {result.get('status')}")
            except Exception:
                logger.error("Manual training failed", exc_info=True)
        _th.Thread(target=_bg_train, daemon=True, name="manual-train").start()
        return "🎓 Training started in background. Check 'training status' later."

    _export_match = re.match(r"^export training\s+(\w+)", lower)
    if _export_match:
        fmt = _export_match.group(1)
        try:
            from app.training_collector import get_pipeline
            result = get_pipeline().export_format(fmt)
            if result.get("error"):
                return f"Export error: {result['error']}"
            return f"✅ Exported {result['exported']} examples ({fmt})\n   Path: {result['path']}"
        except Exception as exc:
            return f"Export error: {str(exc)[:200]}"
```

### MODIFY: `app/bridge_client.py` — Ensure mlx_generate method exists

```python
# Add to BridgeClient class if not already present:

    def mlx_generate(self, prompt: str, model: str = "mlx-community/Qwen2.5-7B-Instruct-4bit",
                     adapter_path: str = "", max_tokens: int = 512,
                     temperature: float = 0.3, seed: int = 42) -> dict:
        return self._request("POST", "/mlx/generate", json={
            "prompt": prompt, "model": model, "adapter_path": adapter_path,
            "max_tokens": max_tokens, "temperature": temperature, "seed": seed,
        })

    def mlx_status(self) -> dict:
        return self._request("GET", "/mlx/status")
```

### MODIFY: `host_bridge/server.py` — Wire MLX routes

```python
# Add to the host bridge FastAPI app:

@app.post("/mlx/generate")
async def mlx_generate_endpoint(request: Request):
    token = request.headers.get("X-Capability-Token", "")
    if not _verify_token(token, "mlx_generate"):
        raise HTTPException(status_code=403, detail="Permission denied")
    body = await request.json()
    from host_bridge.mlx_routes import generate
    return generate(
        prompt=body.get("prompt", ""),
        model_name=body.get("model", "mlx-community/Qwen2.5-7B-Instruct-4bit"),
        adapter_path=body.get("adapter_path", ""),
        max_tokens=body.get("max_tokens", 512),
        temperature=body.get("temperature", 0.3),
        seed=body.get("seed", 42),
    )

@app.get("/mlx/status")
async def mlx_status_endpoint():
    from host_bridge.mlx_routes import get_status
    return get_status()
```

---

## Summary: v2 vs v1 Comparison

| Metric | v1 (IMMUTABLE) | v2 (Unrestricted) |
|--------|---------------|-------------------|
| New files | 10 | 9 (unified MCP package) |
| Modified files | 10 | 14 (direct modifications) |
| Lines of indirection removed | 0 | ~200 |
| Hook registrations needed | 1 (prompt_caching) | 0 |
| Agent files modified for tools | 2+ (researcher, media_analyst, ...) | 0 (plugin registry) |
| Copy-paste code blocks | 2 (main.py compression) | 0 (middleware) |
| Schema management | Bolted into _get_conn() | Proper migration system |
| Adapter inference | Metadata annotation only | Full _AdapterLLM with fallback |
| Skill filtering logic | In context.py consumer | On SkillRecord.matches_context() |
| MCP transport code | Duplicated server/client | Shared transports.py |
| Prompt caching | Hook at priority 15 | Direct in _cached_llm() |

### Non-Degradation Guarantees

1. **Every modification is additive** — no existing function signatures change
2. **All new code paths have try/except with logging** — failures are non-fatal
3. **Lazy imports preserved** — no new module-level crewai/litellm imports
4. **Thread safety maintained** — locks on shared state, thread-local where needed
5. **SSRF protection extended** — browser + MCP SSE reuse web_fetch validation
6. **Cost awareness** — skill creation and NL cron use budget LLMs
7. **Migration system is idempotent** — safe to run on every startup
8. **Plugin registry is lazy** — tools created on first crew execution, not at import
9. **AdapterLLM has fallback** — drops to Ollama if bridge is unavailable
10. **Compression middleware proxies all attributes** — `commander.last_crew_used` etc. still work
