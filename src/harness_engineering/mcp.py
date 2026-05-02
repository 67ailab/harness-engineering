from __future__ import annotations

from typing import Any
import json

from .tools import Tool, ToolError, ToolRegistry


_JSON_TYPE_MAP = {
    "str": {"type": "string"},
    "bool": {"type": "boolean"},
    "int": {"type": "integer"},
    "float": {"type": "number"},
    "dict": {"type": "object"},
    "list": {"type": "array"},
    "list[str]": {"type": "array", "items": {"type": "string"}},
    "list[dict]": {"type": "array", "items": {"type": "object"}},
}


def _schema_fragment(type_name: str) -> dict[str, Any]:
    normalized = type_name.strip().lower()
    if normalized in _JSON_TYPE_MAP:
        return dict(_JSON_TYPE_MAP[normalized])
    return {
        "type": "string",
        "description": f"Original type hint: {type_name}",
    }


def tool_to_mcp_descriptor(tool: Tool) -> dict[str, Any]:
    properties = {name: _schema_fragment(type_name) for name, type_name in tool.input_schema.items()}
    return {
        "name": tool.name,
        "description": tool.description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": list(tool.input_schema.keys()),
            "additionalProperties": False,
        },
        "annotations": {
            "title": tool.name,
            "readOnlyHint": not tool.risky,
            "openWorldHint": False,
        },
        "meta": {
            "risky": tool.risky,
        },
    }


def registry_to_mcp_tools(registry: ToolRegistry) -> list[dict[str, Any]]:
    return [tool_to_mcp_descriptor(tool) for tool in registry.list()]


def _matches_type(value: Any, type_name: str) -> bool:
    normalized = type_name.strip().lower()
    if normalized == "str":
        return isinstance(value, str)
    if normalized == "bool":
        return isinstance(value, bool)
    if normalized == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if normalized == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if normalized == "dict":
        return isinstance(value, dict)
    if normalized == "list":
        return isinstance(value, list)
    if normalized == "list[str]":
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    if normalized == "list[dict]":
        return isinstance(value, list) and all(isinstance(item, dict) for item in value)
    return True


def validate_tool_arguments(tool: Tool, arguments: dict[str, Any]) -> None:
    missing = [name for name in tool.input_schema if name not in arguments]
    if missing:
        raise ToolError(f"Missing required arguments for {tool.name}: {', '.join(missing)}")

    extras = sorted(set(arguments) - set(tool.input_schema))
    if extras:
        raise ToolError(f"Unexpected arguments for {tool.name}: {', '.join(extras)}")

    mismatches = []
    for name, type_name in tool.input_schema.items():
        value = arguments.get(name)
        if not _matches_type(value, type_name):
            mismatches.append(f"{name} expected {type_name}, got {type(value).__name__}")
    if mismatches:
        raise ToolError(f"Invalid arguments for {tool.name}: {'; '.join(mismatches)}")


def call_tool(registry: ToolRegistry, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    tool = registry.get(tool_name)
    validate_tool_arguments(tool, arguments)
    return tool.handler(**arguments)


def call_tool_mcp(registry: ToolRegistry, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        result = call_tool(registry=registry, tool_name=tool_name, arguments=arguments)
        return {
            "tool": tool_name,
            "isError": False,
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, ensure_ascii=False, indent=2),
                }
            ],
            "structuredContent": result,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "tool": tool_name,
            "isError": True,
            "content": [
                {
                    "type": "text",
                    "text": str(exc),
                }
            ],
            "structuredContent": {
                "error": str(exc),
            },
        }
