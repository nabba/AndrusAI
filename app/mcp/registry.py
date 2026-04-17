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
    # Priority 1: inline JSON from Settings (mcp_servers_json)
    try:
        from app.config import get_settings
        inline = get_settings().mcp_servers_json.strip()
        if inline:
            return [MCPServerConfig.from_dict(e) for e in json.loads(inline)]
    except Exception as exc:
        logger.warning(f"mcp_registry: mcp_servers_json parse error: {exc}")

    # Priority 2: MCP_SERVERS env var
    env_val = os.environ.get("MCP_SERVERS", "")
    if env_val.strip():
        try:
            return [MCPServerConfig.from_dict(e) for e in json.loads(env_val)]
        except Exception as exc:
            logger.warning(f"mcp_registry: MCP_SERVERS parse error: {exc}")

    # Priority 3: workspace config file
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
