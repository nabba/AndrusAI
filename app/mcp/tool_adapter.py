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
            if tool is not None:
                tools.append(tool)
        except Exception:
            logger.debug(f"mcp_tool_adapter: failed to wrap '{schema.name}'", exc_info=True)
    return tools


def _build_tool(schema, BaseTool, Field, create_model, call_fn):
    properties = schema.input_schema.get("properties", {})
    required = set(schema.input_schema.get("required", []))
    # Use concrete generic types so pydantic can emit valid JSON schemas for
    # Anthropic/OpenAI tool calling. Bare `list`/`dict` produce schemas
    # without `items`/`properties` which some providers reject.
    type_map = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,       # upgraded to list[str] below
        "object": dict,      # upgraded to dict[str, str] below
    }
    fields: dict = {}
    for pname, pschema in properties.items():
        raw_type = pschema.get("type", "string")
        if raw_type == "array":
            item_type = (pschema.get("items") or {}).get("type", "string")
            ptype = list[type_map.get(item_type, str)]
        elif raw_type == "object":
            ptype = dict[str, type_map.get("string", str)]
        else:
            ptype = type_map.get(raw_type, str)
        desc = pschema.get("description", "")
        if pname in required:
            fields[pname] = (ptype, Field(description=desc))
        else:
            fields[pname] = (ptype, Field(default=pschema.get("default"), description=desc))
    if not fields:
        fields["query"] = (str, Field(default="", description="Input"))

    try:
        InputModel = create_model(f"MCP_{schema.server_name}_{schema.name}_Input", **fields)
    except Exception as exc:
        # Invalid schema — skip this tool but don't break the whole adapter.
        logger.warning(
            f"mcp_tool_adapter: cannot build args_schema for "
            f"'{schema.server_name}.{schema.name}': {exc}"
        )
        return None

    _server, _name = schema.server_name, schema.name
    _desc = schema.description

    class MCPTool(BaseTool):
        name: str = f"mcp_{_server}_{_name}"
        description: str = f"[MCP:{_server}] {_desc or _name}"[:500]
        args_schema: type = InputModel

        def _run(self, **kwargs) -> str:
            result = call_fn(_server, _name, kwargs)
            return result[:4000] if len(result) > 4000 else result

    return MCPTool()
