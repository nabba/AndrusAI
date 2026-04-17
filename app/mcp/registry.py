"""
mcp/registry.py — MCP server lifecycle management.

Config sources (priority order):
  1. settings.mcp_servers_json (inline JSON)
  2. MCP_SERVERS env var (JSON array)
  3. /app/workspace/mcp_servers.json (file)

Supports hot-add/remove at runtime via add_server()/remove_server().
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
    lines = [f"MCP Servers ({len(servers)}):"]
    for name, connected, tools, transport in servers:
        status = "connected" if connected else "disconnected"
        lines.append(f"  [{status}] {name} ({transport}) — {tools} tools")
    return "\n".join(lines)


# ── Hot-add / remove at runtime ──────────────────────────────────────────────


def add_server(config_dict: dict) -> tuple[bool, str]:
    """Add and connect an MCP server at runtime. Also persists to config file.

    Args:
        config_dict: MCPServerConfig-compatible dict
            (name, transport, command/url, args, env, etc.)

    Returns:
        (success, message) tuple.
    """
    try:
        config = MCPServerConfig.from_dict(config_dict)
    except Exception as e:
        return False, f"Invalid config: {e}"

    if not config.name:
        return False, "Server config must have a 'name' field."

    with _lock:
        if config.name in _clients:
            return False, f"Server '{config.name}' already connected."

    # Try to connect
    client = MCPClient(config)
    if not client.connect():
        return False, f"Failed to connect to '{config.name}'. Check command/URL and credentials."

    with _lock:
        _clients[config.name] = client

    # Invalidate the plugin tool cache so next agent creation picks up new tools
    try:
        from app.crews.base_crew import _plugin_lock, _plugin_tools_cache
        import app.crews.base_crew as _bc
        with _bc._plugin_lock:
            _bc._plugin_tools_cache = None
    except Exception:
        pass

    # Persist to config file
    _persist_config(config_dict)

    tool_count = len(client.tools)
    tool_names = [t.name for t in client.tools[:10]]
    logger.info(f"mcp_registry: hot-added '{config.name}' — {tool_count} tools: {tool_names}")
    return True, f"Connected '{config.name}' — {tool_count} tools discovered: {', '.join(tool_names)}"


def remove_server(name: str) -> tuple[bool, str]:
    """Disconnect and remove an MCP server at runtime.

    Returns:
        (success, message) tuple.
    """
    with _lock:
        client = _clients.pop(name, None)
    if not client:
        return False, f"Server '{name}' not found."

    try:
        client.disconnect()
    except Exception:
        pass

    # Invalidate plugin tool cache
    try:
        import app.crews.base_crew as _bc
        with _bc._plugin_lock:
            _bc._plugin_tools_cache = None
    except Exception:
        pass

    # Remove from persisted config
    _unpersist_config(name)

    logger.info(f"mcp_registry: removed '{name}'")
    return True, f"Disconnected and removed '{name}'."


def list_servers() -> list[dict]:
    """Return status of all connected servers."""
    with _lock:
        return [
            {
                "name": name,
                "transport": client.config.transport,
                "connected": client.is_connected,
                "tools": [t.name for t in client.tools],
                "tool_count": len(client.tools),
            }
            for name, client in _clients.items()
        ]


def _persist_config(config_dict: dict) -> None:
    """Add a server config to the workspace config file."""
    try:
        existing = []
        if _CONFIG_FILE.exists():
            existing = json.loads(_CONFIG_FILE.read_text())
        # Replace if same name, otherwise append
        existing = [c for c in existing if c.get("name") != config_dict.get("name")]
        existing.append(config_dict)
        _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_FILE.write_text(json.dumps(existing, indent=2))
    except Exception as e:
        logger.warning(f"mcp_registry: failed to persist config: {e}")


def _unpersist_config(name: str) -> None:
    """Remove a server config from the workspace config file."""
    try:
        if not _CONFIG_FILE.exists():
            return
        existing = json.loads(_CONFIG_FILE.read_text())
        filtered = [c for c in existing if c.get("name") != name]
        _CONFIG_FILE.write_text(json.dumps(filtered, indent=2))
    except Exception as e:
        logger.warning(f"mcp_registry: failed to unpersist config: {e}")
