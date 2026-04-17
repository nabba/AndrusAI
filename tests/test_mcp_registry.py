"""Tests for app/mcp/registry.py — config loading + lifecycle management."""
import json
from pathlib import Path
from unittest.mock import MagicMock

from tests._v2_shim import install_settings_shim

install_settings_shim()

from app.mcp import registry  # noqa: E402
from app.mcp.client import MCPServerConfig, MCPToolSchema  # noqa: E402


def _reset_registry():
    # Clear module-level state between tests
    registry._clients.clear()


class TestLoadConfigs:
    def setup_method(self):
        _reset_registry()
        # Clear any leftover env var
        import os
        os.environ.pop("MCP_SERVERS", None)

    def test_priority_1_settings_inline_json(self, monkeypatch):
        shim = install_settings_shim(mcp_servers_json=json.dumps([
            {"name": "inline", "transport": "stdio", "command": "/bin/inline"}
        ]))
        cfgs = registry._load_configs()
        assert len(cfgs) == 1
        assert cfgs[0].name == "inline"
        assert cfgs[0].command == "/bin/inline"

    def test_priority_2_env_var(self, monkeypatch):
        install_settings_shim(mcp_servers_json="")
        monkeypatch.setenv("MCP_SERVERS", json.dumps([
            {"name": "env_server", "command": "/bin/env"}
        ]))
        cfgs = registry._load_configs()
        assert len(cfgs) == 1
        assert cfgs[0].name == "env_server"

    def test_priority_3_workspace_file(self, monkeypatch, tmp_path):
        install_settings_shim(mcp_servers_json="")
        monkeypatch.delenv("MCP_SERVERS", raising=False)
        config_file = tmp_path / "mcp.json"
        config_file.write_text(json.dumps([
            {"name": "from_file", "command": "/bin/file"}
        ]))
        monkeypatch.setattr(registry, "_CONFIG_FILE", config_file)
        cfgs = registry._load_configs()
        assert len(cfgs) == 1
        assert cfgs[0].name == "from_file"

    def test_returns_empty_when_no_source(self, monkeypatch, tmp_path):
        install_settings_shim(mcp_servers_json="")
        monkeypatch.delenv("MCP_SERVERS", raising=False)
        monkeypatch.setattr(registry, "_CONFIG_FILE", tmp_path / "nope.json")
        assert registry._load_configs() == []

    def test_bad_json_is_handled_gracefully(self, monkeypatch, tmp_path):
        install_settings_shim(mcp_servers_json="not valid json {")
        # Should fall through to env/file, not raise
        monkeypatch.delenv("MCP_SERVERS", raising=False)
        monkeypatch.setattr(registry, "_CONFIG_FILE", tmp_path / "nope.json")
        assert registry._load_configs() == []


class TestConnectAll:
    def setup_method(self):
        _reset_registry()

    def test_connect_all_no_configs_returns_zero(self, monkeypatch):
        install_settings_shim(mcp_servers_json="")
        monkeypatch.delenv("MCP_SERVERS", raising=False)
        monkeypatch.setattr(registry, "_CONFIG_FILE", Path("/nonexistent"))
        assert registry.connect_all() == 0

    def test_connect_all_skips_disabled(self, monkeypatch):
        install_settings_shim(mcp_servers_json=json.dumps([
            {"name": "off", "enabled": False, "command": "/bin/off"},
            {"name": "on", "enabled": True, "command": "/bin/on"},
        ]))

        # Patch MCPClient to mock connect()
        created = []

        class FakeClient:
            def __init__(self, config):
                self.config = config
                self.tools = []
                created.append(config.name)

            def connect(self):
                return True

            @property
            def is_connected(self):
                return True

        monkeypatch.setattr(registry, "MCPClient", FakeClient)
        n = registry.connect_all()
        assert n == 1
        assert created == ["on"]
        assert "off" not in registry._clients

    def test_connect_all_counts_only_successful(self, monkeypatch):
        install_settings_shim(mcp_servers_json=json.dumps([
            {"name": "good", "command": "/bin/good"},
            {"name": "bad", "command": "/bin/bad"},
        ]))

        class FakeClient:
            def __init__(self, config):
                self.config = config
                self.tools = []

            def connect(self):
                return self.config.name == "good"

            @property
            def is_connected(self):
                return True

        monkeypatch.setattr(registry, "MCPClient", FakeClient)
        n = registry.connect_all()
        assert n == 1

    def test_connect_all_skips_already_registered(self, monkeypatch):
        install_settings_shim(mcp_servers_json=json.dumps([
            {"name": "dup", "command": "/bin/dup"},
        ]))

        class FakeClient:
            def __init__(self, config):
                self.config = config
                self.tools = []

            def connect(self):
                return True

            @property
            def is_connected(self):
                return True

        monkeypatch.setattr(registry, "MCPClient", FakeClient)
        registry.connect_all()
        first_count = len(registry._clients)
        # Call again — should not double-register
        registry.connect_all()
        assert len(registry._clients) == first_count


class TestGetAllToolsAndCall:
    def setup_method(self):
        _reset_registry()

    def test_get_all_tools_aggregates(self):
        c1 = MagicMock()
        c1.tools = [MCPToolSchema(server_name="a", name="t1", description="")]
        c2 = MagicMock()
        c2.tools = [MCPToolSchema(server_name="b", name="t2", description=""),
                    MCPToolSchema(server_name="b", name="t3", description="")]
        registry._clients["a"] = c1
        registry._clients["b"] = c2
        tools = registry.get_all_tools()
        assert len(tools) == 3
        assert {t.name for t in tools} == {"t1", "t2", "t3"}

    def test_call_tool_unknown_server(self):
        assert "not connected" in registry.call_tool("ghost", "t1", {})

    def test_call_tool_delegates_to_client(self):
        c = MagicMock()
        c.call_tool = MagicMock(return_value="tool result")
        registry._clients["a"] = c
        out = registry.call_tool("a", "t1", {"x": 1})
        assert out == "tool result"
        c.call_tool.assert_called_once_with("t1", {"x": 1})


class TestFormatStatus:
    def setup_method(self):
        _reset_registry()

    def test_no_servers(self):
        assert "No MCP servers" in registry.format_status()

    def test_with_servers(self):
        c = MagicMock()
        c.is_connected = True
        c.tools = [MCPToolSchema(server_name="x", name="t", description="")]
        c.config = MagicMock()
        c.config.transport = "stdio"
        registry._clients["x"] = c
        out = registry.format_status()
        assert "x" in out
        assert "1 tools" in out
        assert "stdio" in out


class TestDisconnectAll:
    def setup_method(self):
        _reset_registry()

    def test_disconnect_all_clears(self):
        c = MagicMock()
        registry._clients["x"] = c
        registry.disconnect_all()
        assert registry._clients == {}
        c.disconnect.assert_called_once()

    def test_disconnect_all_survives_client_error(self):
        c = MagicMock()
        c.disconnect.side_effect = RuntimeError("boom")
        registry._clients["x"] = c
        # Should not raise
        registry.disconnect_all()
        assert registry._clients == {}
