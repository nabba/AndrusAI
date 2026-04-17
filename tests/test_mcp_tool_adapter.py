"""Tests for app/mcp/tool_adapter.py — MCP → CrewAI BaseTool bridge."""
from unittest.mock import patch

import pytest

from tests._v2_shim import install_settings_shim

install_settings_shim()

from app.mcp import tool_adapter  # noqa: E402
from app.mcp.client import MCPToolSchema  # noqa: E402


pytest.importorskip("crewai", reason="crewai required for tool adapter tests")


class TestCreateCrewaiTools:
    def test_empty_schema_returns_empty_list(self):
        with patch("app.mcp.registry.get_all_tools", return_value=[]):
            assert tool_adapter.create_crewai_tools() == []

    def test_builds_tool_for_each_schema(self):
        schemas = [
            MCPToolSchema(server_name="fs", name="read", description="Read file",
                          input_schema={"type": "object",
                                        "properties": {"path": {"type": "string",
                                                                "description": "File path"}},
                                        "required": ["path"]}),
            MCPToolSchema(server_name="fs", name="list", description="",
                          input_schema={}),
        ]
        with patch("app.mcp.registry.get_all_tools", return_value=schemas), \
             patch("app.mcp.registry.call_tool", return_value="ok"):
            tools = tool_adapter.create_crewai_tools()

        assert len(tools) == 2
        names = {t.name for t in tools}
        assert "mcp_fs_read" in names
        assert "mcp_fs_list" in names

    def test_tool_description_includes_server_name(self):
        schema = MCPToolSchema(server_name="github", name="search",
                               description="Search issues", input_schema={})
        with patch("app.mcp.registry.get_all_tools", return_value=[schema]), \
             patch("app.mcp.registry.call_tool", return_value=""):
            tools = tool_adapter.create_crewai_tools()
        assert "[MCP:github]" in tools[0].description
        assert "Search issues" in tools[0].description

    def test_tool_run_delegates_to_registry(self):
        schema = MCPToolSchema(server_name="s", name="t",
                               description="",
                               input_schema={"type": "object",
                                             "properties": {"q": {"type": "string"}},
                                             "required": ["q"]})
        call_log = []

        def _call(server, name, args):
            call_log.append((server, name, args))
            return "result"

        with patch("app.mcp.registry.get_all_tools", return_value=[schema]), \
             patch("app.mcp.registry.call_tool", side_effect=_call):
            tools = tool_adapter.create_crewai_tools()

        out = tools[0]._run(q="hello")
        assert out == "result"
        assert call_log == [("s", "t", {"q": "hello"})]

    def test_tool_truncates_long_output(self):
        schema = MCPToolSchema(server_name="s", name="big",
                               description="", input_schema={})
        long_output = "x" * 10000
        with patch("app.mcp.registry.get_all_tools", return_value=[schema]), \
             patch("app.mcp.registry.call_tool", return_value=long_output):
            tools = tool_adapter.create_crewai_tools()
        out = tools[0]._run()
        assert len(out) == 4000  # truncation cap

    def test_skips_tool_that_fails_to_build(self):
        good = MCPToolSchema(server_name="s", name="good", description="",
                             input_schema={})
        # Bad schema: invalid type coercion should be handled gracefully
        bad = MCPToolSchema(server_name="s", name="bad", description="",
                            input_schema={"type": "object",
                                          "properties": {"x": {"type": "NOT_A_TYPE"}}})
        with patch("app.mcp.registry.get_all_tools", return_value=[bad, good]), \
             patch("app.mcp.registry.call_tool", return_value=""):
            tools = tool_adapter.create_crewai_tools()
        # Good one still comes through; bad one may fall back to str type
        names = {t.name for t in tools}
        assert "mcp_s_good" in names
