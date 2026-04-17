"""Tests for app/mcp/client.py — MCPClient lifecycle."""
import json
from unittest.mock import MagicMock

from tests._v2_shim import install_settings_shim

install_settings_shim()

from app.mcp.client import MCPClient, MCPServerConfig, MCPToolSchema  # noqa: E402


class _FakeTransport:
    def __init__(self, responses=None):
        self._responses = responses or []
        self._i = 0
        self.notifications = []
        self.started = False
        self.stopped = False
        self.is_alive = True

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True
        self.is_alive = False

    def send_receive(self, msg):
        if self._i >= len(self._responses):
            return {"result": {}}
        r = self._responses[self._i]
        self._i += 1
        if isinstance(r, Exception):
            raise r
        return r

    def send_notification(self, msg):
        self.notifications.append(msg)


class TestMCPServerConfig:
    def test_from_dict_filters_unknown_fields(self):
        d = {"name": "fs", "transport": "stdio", "command": "/bin/fs",
             "unknown_field": "ignored"}
        cfg = MCPServerConfig.from_dict(d)
        assert cfg.name == "fs"
        assert cfg.command == "/bin/fs"
        assert not hasattr(cfg, "unknown_field")

    def test_defaults(self):
        cfg = MCPServerConfig(name="x")
        assert cfg.transport == "stdio"
        assert cfg.enabled is True
        assert cfg.timeout == 30.0
        assert cfg.args == []


class TestMCPClient:
    def _make_client(self, transport=None, transport_kind="stdio"):
        cfg = MCPServerConfig(name="test", transport=transport_kind,
                              command="/bin/true", url="https://example.com/sse")
        client = MCPClient(cfg)
        if transport is not None:
            client._transport = transport
        return client

    def test_connect_success_flow(self):
        t = _FakeTransport(responses=[
            # initialize response
            {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}},
            # tools/list response
            {"jsonrpc": "2.0", "id": 2, "result": {"tools": [
                {"name": "search", "description": "Search tool",
                 "inputSchema": {"type": "object",
                                 "properties": {"q": {"type": "string"}}}},
                {"name": "write", "description": "Write tool",
                 "inputSchema": {}},
            ]}},
        ])
        client = self._make_client(transport=t)
        ok = client.connect()
        assert ok is True
        assert client._initialized is True
        assert t.started is True
        # Notification was sent between init and tools/list
        assert any(n["method"] == "notifications/initialized" for n in t.notifications)
        # 2 tools discovered
        assert len(client.tools) == 2
        assert client.tools[0].name == "search"
        assert client.tools[0].server_name == "test"
        assert client.tools[1].input_schema == {}

    def test_connect_returns_false_on_start_failure(self):
        class BrokenTransport(_FakeTransport):
            def start(self):
                raise ConnectionError("port in use")
        client = self._make_client(transport=BrokenTransport())
        assert client.connect() is False
        assert client._initialized is False

    def test_connect_returns_false_on_init_error(self):
        t = _FakeTransport(responses=[
            {"jsonrpc": "2.0", "id": 1, "error": {"message": "bad protocol"}},
        ])
        client = self._make_client(transport=t)
        assert client.connect() is False

    def test_connect_handles_tool_discovery_failure(self):
        t = _FakeTransport(responses=[
            {"jsonrpc": "2.0", "id": 1, "result": {}},
            RuntimeError("tools/list crashed"),
        ])
        client = self._make_client(transport=t)
        # Should still succeed with empty tool list
        assert client.connect() is True
        assert client.tools == []

    def test_call_tool_requires_initialization(self):
        client = self._make_client(transport=_FakeTransport())
        import pytest
        with pytest.raises(ConnectionError, match="not initialized"):
            client.call_tool("search", {"q": "hello"})

    def test_call_tool_returns_text_blocks_joined(self):
        t = _FakeTransport(responses=[
            {"jsonrpc": "2.0", "id": 1, "result": {
                "content": [
                    {"type": "text", "text": "first line"},
                    {"type": "text", "text": "second line"},
                    {"type": "image", "data": "ignored"},
                ]
            }},
        ])
        client = self._make_client(transport=t)
        client._initialized = True
        out = client.call_tool("search", {"q": "x"})
        assert out == "first line\nsecond line"

    def test_call_tool_returns_error_string(self):
        t = _FakeTransport(responses=[
            {"jsonrpc": "2.0", "id": 1, "error": {"message": "bad args"}},
        ])
        client = self._make_client(transport=t)
        client._initialized = True
        out = client.call_tool("search", {})
        assert "MCP error" in out and "bad args" in out

    def test_call_tool_falls_back_to_json_when_no_text(self):
        t = _FakeTransport(responses=[
            {"jsonrpc": "2.0", "id": 1, "result": {"data": {"count": 3}}},
        ])
        client = self._make_client(transport=t)
        client._initialized = True
        out = client.call_tool("ping", {})
        parsed = json.loads(out)
        assert parsed == {"data": {"count": 3}}

    def test_disconnect_clears_state(self):
        client = self._make_client(transport=_FakeTransport())
        client._initialized = True
        client.disconnect()
        assert client._initialized is False
        assert client._transport.stopped is True
