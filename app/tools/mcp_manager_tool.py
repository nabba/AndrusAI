"""
tools/mcp_manager_tool.py — Agent tools for MCP server discovery and management.

Gives agents the ability to:
  1. Search public MCP registries for useful servers
  2. Add/connect discovered servers at runtime (hot-add)
  3. List and remove connected servers
  4. Check MCP status

Registered via the tool plugin registry in base_crew.py — all agents get
these tools automatically alongside browser_fetch, session_search, etc.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def create_mcp_manager_tools() -> list:
    """Build CrewAI BaseTool instances for MCP server management.

    Returns [] if CrewAI not importable (graceful fallback).
    """
    try:
        from crewai.tools import BaseTool
        from pydantic import BaseModel, Field
    except ImportError:
        return []

    # ── Search Tool ──────────────────────────────────────────────────────

    class _SearchInput(BaseModel):
        query: str = Field(
            description=(
                "What kind of MCP server to find. Examples: "
                "'google calendar', 'filesystem', 'github', 'slack', "
                "'database postgres', 'weather', 'email'"
            )
        )
        limit: int = Field(
            default=5,
            description="Max results to return (1-10).",
        )

    class MCPSearchTool(BaseTool):
        name: str = "mcp_search_servers"
        description: str = (
            "Search public MCP server registries to find useful tool servers. "
            "MCP servers provide external capabilities (e.g., Google Calendar, "
            "GitHub, Slack, filesystem access, databases). Search by keyword — "
            "returns server name, description, install requirements. "
            "Use this when you need a capability you don't currently have."
        )
        args_schema: type = _SearchInput

        def _run(self, query: str, limit: int = 5) -> str:
            try:
                from app.mcp.discovery import search_all
            except ImportError:
                return "MCP discovery module not available."

            limit = max(1, min(int(limit), 10))
            results = search_all(query, limit=limit)

            if not results:
                return f"No MCP servers found for: '{query}'. Try broader search terms."

            parts = [f"Found {len(results)} MCP server(s) for '{query}':\n"]
            for i, ds in enumerate(results, 1):
                parts.append(f"--- {i}. {ds.format_summary()}\n")

                # Show what's needed to install
                needs_keys = [
                    v["name"] for v in ds.env_vars
                    if v.get("required") and v.get("secret")
                ]
                if needs_keys:
                    parts.append(f"  ⚠ Needs API key(s): {', '.join(needs_keys)}")
                if ds.remote_url:
                    parts.append(f"  → Ready to add (remote, no local install needed)")
                elif ds.package_id:
                    parts.append(
                        f"  → Add with: mcp_add_server(name='{ds.name}', "
                        f"source='{'official' if ds.source == 'official' else 'smithery'}', "
                        f"query='{query}')"
                    )
                parts.append("")

            parts.append(
                "To add a server, use the mcp_add_server tool with the server name."
            )
            return "\n".join(parts)

    # ── Add Server Tool ──────────────────────────────────────────────────

    class _AddInput(BaseModel):
        name: str = Field(
            description="Server name from search results (e.g., '@anthropic/mcp-server-filesystem')."
        )
        query: str = Field(
            default="",
            description="Original search query (helps re-find the server).",
        )
        env_vars: str = Field(
            default="",
            description=(
                "Environment variables as key=value pairs separated by semicolons. "
                "Example: 'API_KEY=sk-123;PROJECT_ID=my-project'"
            ),
        )

    class MCPAddServerTool(BaseTool):
        name: str = "mcp_add_server"
        description: str = (
            "Add and connect an MCP server found via mcp_search_servers. "
            "The server will be connected immediately, its tools discovered, "
            "and made available to all agents. The config is persisted so it "
            "survives restarts. Provide env_vars for any required API keys."
        )
        args_schema: type = _AddInput

        def _run(self, name: str, query: str = "", env_vars: str = "") -> str:
            # Parse env overrides
            overrides: dict[str, str] = {}
            if env_vars:
                for pair in env_vars.split(";"):
                    pair = pair.strip()
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        overrides[k.strip()] = v.strip()

            # Re-search to get full server details
            try:
                from app.mcp.discovery import search_all
                results = search_all(query or name, limit=20)
            except ImportError:
                return "MCP discovery module not available."

            # Find the matching server
            match = None
            for ds in results:
                if ds.name == name or name in ds.name:
                    match = ds
                    break

            if not match:
                # Maybe it's a direct config — try adding by name with SSE
                return (
                    f"Server '{name}' not found in registries. "
                    f"If this is a remote server, provide the full URL via "
                    f"env_vars or contact the system administrator."
                )

            # Check for missing required secrets
            missing = []
            for var in match.env_vars:
                if var.get("required") and var.get("secret"):
                    vname = var["name"]
                    if vname not in overrides and not __import__("os").environ.get(vname):
                        missing.append(vname)
            if missing:
                return (
                    f"Cannot add '{match.name}' — missing required credentials: "
                    f"{', '.join(missing)}. Pass them via env_vars parameter, "
                    f"e.g.: env_vars='{';'.join(f'{k}=YOUR_VALUE' for k in missing)}'"
                )

            # Generate config and hot-add
            config = match.to_install_config(overrides)
            try:
                from app.mcp.registry import add_server
                ok, msg = add_server(config)
                return msg
            except Exception as e:
                return f"Failed to add server: {e}"

    # ── List Servers Tool ────────────────────────────────────────────────

    class MCPListTool(BaseTool):
        name: str = "mcp_list_servers"
        description: str = (
            "List all currently connected MCP servers and their tools. "
            "Shows server name, connection status, transport type, and "
            "available tool names."
        )

        def _run(self, **kwargs) -> str:
            try:
                from app.mcp.registry import list_servers
            except ImportError:
                return "MCP registry not available."

            servers = list_servers()
            if not servers:
                return (
                    "No MCP servers connected. Use mcp_search_servers to find "
                    "and mcp_add_server to add useful servers."
                )

            parts = [f"Connected MCP servers ({len(servers)}):\n"]
            for s in servers:
                status = "connected" if s["connected"] else "disconnected"
                parts.append(
                    f"  [{status}] {s['name']} ({s['transport']}) — "
                    f"{s['tool_count']} tools"
                )
                if s["tools"]:
                    parts.append(f"    Tools: {', '.join(s['tools'][:15])}")
                    if len(s["tools"]) > 15:
                        parts.append(f"    ... and {len(s['tools']) - 15} more")
            return "\n".join(parts)

    # ── Remove Server Tool ───────────────────────────────────────────────

    class _RemoveInput(BaseModel):
        name: str = Field(description="Name of the MCP server to disconnect and remove.")

    class MCPRemoveTool(BaseTool):
        name: str = "mcp_remove_server"
        description: str = (
            "Disconnect and remove an MCP server. Its tools will no longer "
            "be available to agents. Use mcp_list_servers to see current servers."
        )
        args_schema: type = _RemoveInput

        def _run(self, name: str) -> str:
            try:
                from app.mcp.registry import remove_server
                ok, msg = remove_server(name)
                return msg
            except Exception as e:
                return f"Failed to remove server: {e}"

    return [MCPSearchTool(), MCPAddServerTool(), MCPListTool(), MCPRemoveTool()]
