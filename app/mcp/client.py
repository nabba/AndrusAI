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
    StdioTransport, SSETransport, StreamableHTTPTransport,
    jsonrpc_request, jsonrpc_notification,
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
    headers: dict[str, str] = field(default_factory=dict)  # auth headers for remote servers

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
        self._transport: StdioTransport | SSETransport | StreamableHTTPTransport
        # Build auth headers for remote transports
        self._headers = dict(config.headers) if config.headers else {}
        # Auto-inject Smithery API key for Smithery-hosted servers
        if "smithery.ai" in (config.url or "") and "Authorization" not in self._headers:
            import os
            _sk = os.environ.get("SMITHERY_API_KEY", "")
            if _sk:
                self._headers["Authorization"] = f"Bearer {_sk}"

        if config.transport in ("http", "streamable-http"):
            self._transport = StreamableHTTPTransport(config.url, config.timeout, self._headers)
        elif config.transport == "sse":
            # Try SSE first; if it fails, fall back to Streamable HTTP
            self._transport = SSETransport(config.url, config.timeout)
        else:
            self._transport = StdioTransport(config.command, config.args, config.env)
        self._initialized = False

    def connect(self) -> bool:
        try:
            self._transport.start()
        except Exception as exc:
            # SSE → Streamable HTTP fallback for remote servers
            if self.config.transport == "sse" and self.config.url:
                logger.info(
                    f"mcp_client: '{self.config.name}' SSE failed, "
                    f"trying Streamable HTTP: {exc}"
                )
                self._transport = StreamableHTTPTransport(
                    self.config.url, self.config.timeout, self._headers,
                )
                try:
                    self._transport.start()
                except Exception as exc2:
                    logger.warning(f"mcp_client: '{self.config.name}' HTTP also failed: {exc2}")
                    return False
            else:
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
