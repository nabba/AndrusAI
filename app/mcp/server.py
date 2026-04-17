"""
mcp/server.py — Model Context Protocol server for AndrusAI (P6).

(Moved from app/mcp_server.py as part of the unified MCP package.)

Exposes five resource types and three tools via the MCP standard, enabling
external LLM applications (Claude Desktop, custom agents, IDE copilots)
to consume AndrusAI's knowledge and state.

Resources (read-only context that MCP clients can consume):
  - andrusai://philosophy/{query}      — Philosophical RAG passages
  - andrusai://mcsv/{agent_id}         — Metacognitive State Vector
  - andrusai://blackboard/{task_id}    — Research blackboard findings
  - andrusai://personality/{role}      — PDS personality state
  - andrusai://memory/{agent}/{query}  — Mem0 agent memories

Tools (actions MCP clients can invoke):
  - search_philosophy   — query the philosophical RAG corpus
  - query_blackboard    — semantic search over research findings
  - score_creativity    — run Torrance scoring on arbitrary text

Transport: SSE (Server-Sent Events) mounted on the existing FastAPI app
at /mcp/sse — no separate process needed.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Lazy flag — set True once mount_mcp_routes() succeeds
_mounted = False


def mount_mcp_routes(app: Any) -> bool:
    """Mount MCP SSE endpoints on an existing FastAPI app.

    Call this once during app startup. Returns False if the mcp SDK is
    not installed (graceful degradation — the rest of the system works
    fine without MCP).
    """
    global _mounted
    if _mounted:
        return True

    try:
        from mcp.server import Server
        # Keep SDK SSE transport for serving (it handles the SSE protocol).
        # Our shared app/mcp/transports.py is for client-side consumption.
        from mcp.server.sse import SseServerTransport
        from mcp.types import Resource, Tool, TextContent
    except ImportError:
        logger.info("mcp.server: mcp SDK not installed — MCP endpoints disabled. "
                     "Install with: pip install mcp")
        return False

    from starlette.routing import Route

    server = Server("andrusai")
    sse_transport = SseServerTransport("/mcp/messages/")

    # ── Resources ──────────────────────────────────────────────────────

    @server.list_resources()
    async def list_resources() -> list[Resource]:
        return [
            Resource(
                uri="andrusai://philosophy/query",
                name="Philosophy RAG",
                description="Humanist philosophical knowledge base — Aristotle, Seneca, Kant, Mill, Arendt.",
                mimeType="text/plain",
            ),
            Resource(
                uri="andrusai://mcsv/current",
                name="Metacognitive State Vector",
                description="5-dimensional experiential state: emotional awareness, correctness, experience matching, conflict detection, complexity.",
                mimeType="application/json",
            ),
            Resource(
                uri="andrusai://blackboard/latest",
                name="Research Blackboard",
                description="Shared research findings with confidence and verification metadata.",
                mimeType="text/plain",
            ),
            Resource(
                uri="andrusai://personality/writer",
                name="Personality State",
                description="Current PDS (Personality Development System) assessment for an agent role.",
                mimeType="application/json",
            ),
            Resource(
                uri="andrusai://memory/team/recent",
                name="Team Memory",
                description="Recent shared team memories from Mem0.",
                mimeType="text/plain",
            ),
        ]

    @server.read_resource()
    async def read_resource(uri: str) -> str:
        parts = uri.replace("andrusai://", "").split("/")
        domain = parts[0] if parts else ""
        arg = "/".join(parts[1:]) if len(parts) > 1 else ""

        if domain == "philosophy":
            return _read_philosophy(arg or "virtue ethics")
        elif domain == "mcsv":
            return _read_mcsv(arg)
        elif domain == "blackboard":
            return _read_blackboard(arg)
        elif domain == "personality":
            return _read_personality(arg or "writer")
        elif domain == "memory":
            return _read_memory(arg)
        else:
            return json.dumps({"error": f"Unknown resource domain: {domain}"})

    # ── Tools ──────────────────────────────────────────────────────────

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="search_philosophy",
                description="Search the humanist philosophical knowledge base. Returns relevant passages.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Natural language philosophy query"},
                        "tradition": {"type": "string", "description": "Optional tradition filter"},
                        "n_results": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="query_blackboard",
                description="Search the research blackboard for findings from any agent.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "What to search for"},
                        "task_id": {"type": "string", "default": "", "description": "Scope to a task"},
                        "confidence": {"type": "string", "description": "Filter: high/medium/low"},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="score_creativity",
                description="Run Torrance-style creativity scoring on text. Returns fluency, flexibility, originality, elaboration.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Text to score"},
                    },
                    "required": ["text"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        if name == "search_philosophy":
            result = _read_philosophy(
                arguments.get("query", ""),
                tradition=arguments.get("tradition"),
                n=arguments.get("n_results", 5),
            )
        elif name == "query_blackboard":
            result = _read_blackboard_query(
                arguments.get("query", ""),
                task_id=arguments.get("task_id", ""),
                confidence=arguments.get("confidence"),
            )
        elif name == "score_creativity":
            result = _score_creativity(arguments.get("text", ""))
        else:
            result = json.dumps({"error": f"Unknown tool: {name}"})
        return [TextContent(type="text", text=result)]

    # ── SSE endpoints ─────────────────────────────────────────────────

    async def handle_sse(request):
        async with sse_transport.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await server.run(
                streams[0], streams[1], server.create_initialization_options()
            )

    async def handle_messages(request):
        await sse_transport.handle_post_message(
            request.scope, request.receive, request._send
        )

    # Mount under /mcp/
    app.routes.append(Route("/mcp/sse", endpoint=handle_sse))
    app.routes.append(Route("/mcp/messages/", endpoint=handle_messages, methods=["POST"]))

    _mounted = True
    logger.info("mcp.server: MCP SSE endpoints mounted at /mcp/sse")
    return True


# ── Resource readers ─────────────────────────────────────────────────────

def _read_philosophy(query: str, tradition: str | None = None, n: int = 5) -> str:
    try:
        from app.philosophy.vectorstore import get_store
        store = get_store()
        if store._collection.count() == 0:
            return "Philosophy knowledge base is empty."
        where_filter = {"tradition": tradition} if tradition else None
        results = store.query(query_text=query, n_results=n, where_filter=where_filter)
        if not results:
            return f"No passages found for: {query}"
        lines = []
        for r in results:
            meta = r["metadata"]
            lines.append(
                f"[{meta.get('author', '?')} — {meta.get('title', '?')}] "
                f"({meta.get('tradition', '?')}, {meta.get('era', '?')})\n{r['text']}"
            )
        return "\n\n---\n\n".join(lines)
    except Exception as exc:
        return f"Philosophy retrieval error: {exc}"


def _read_mcsv(agent_id: str) -> str:
    try:
        from app.subia.belief.internal_state import MetacognitiveStateVector
        # Return a default MCSV — live integration requires hooking into the
        # sentience pipeline, which runs per-request. This gives the schema.
        mcsv = MetacognitiveStateVector()
        return json.dumps(mcsv.to_dict(), indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def _read_blackboard(task_id: str) -> str:
    try:
        from app.memory.scoped_memory import retrieve_findings
        findings = retrieve_findings(task_id=task_id or "", n=20)
        if not findings:
            return "No findings on the blackboard."
        lines = []
        for f in findings:
            meta = f.get("metadata", {})
            lines.append(
                f"[{meta.get('confidence', '?')} / {meta.get('verification_status', '?')}] "
                f"(agent: {meta.get('agent', '?')})\n{f.get('document', f.get('text', ''))}"
            )
        return "\n\n---\n\n".join(lines)
    except Exception as exc:
        return f"Blackboard error: {exc}"


def _read_blackboard_query(query: str, task_id: str = "", confidence: str | None = None) -> str:
    try:
        from app.memory.scoped_memory import retrieve_findings
        findings = retrieve_findings(
            task_id=task_id, query=query, n=10,
            confidence_filter=confidence,
        )
        if not findings:
            return "No matching findings."
        lines = []
        for f in findings:
            meta = f.get("metadata", {})
            lines.append(
                f"[{meta.get('confidence', '?')}] {f.get('document', f.get('text', ''))[:300]}"
            )
        return "\n".join(lines)
    except Exception as exc:
        return f"Blackboard query error: {exc}"


def _read_personality(role: str) -> str:
    try:
        from app.personality.state import get_personality_state
        state = get_personality_state(role)
        return json.dumps(state, indent=2, default=str) if state else json.dumps({"role": role, "state": "not_assessed"})
    except Exception as exc:
        return json.dumps({"role": role, "error": str(exc)})


def _read_memory(path: str) -> str:
    parts = path.split("/") if path else ["team", "recent"]
    agent = parts[0] if parts else "team"
    query = parts[1] if len(parts) > 1 else "recent events"
    try:
        from app.memory.mem0_manager import get_manager
        mgr = get_manager()
        results = mgr.search(query=query, user_id=agent, limit=10)
        if not results:
            return f"No memories found for {agent}."
        lines = []
        for r in results:
            text = r.get("memory", r.get("text", "")) if isinstance(r, dict) else str(r)
            lines.append(text[:300])
        return "\n---\n".join(lines)
    except Exception as exc:
        return f"Memory error: {exc}"


def _score_creativity(text: str) -> str:
    try:
        from app.personality.creativity_scoring import score_output
        scores = score_output(text)
        return json.dumps(scores.as_dict(), indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
