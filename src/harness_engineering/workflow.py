from __future__ import annotations

from typing import Any

from .tools import ToolRegistry


TERMINAL_STATES = {"done", "failed"}


def build_workflow_definition(registry: ToolRegistry) -> dict[str, Any]:
    risky_tools = {tool.name for tool in registry.list() if tool.risky}
    nodes = [
        {
            "id": "init",
            "kind": "entry",
            "label": "Run created",
            "terminal": False,
        },
        {
            "id": "search_mock",
            "kind": "tool",
            "label": "Search source documents",
            "tool": "search_mock",
            "risky": "search_mock" in risky_tools,
            "approval_required": False,
            "terminal": False,
        },
        {
            "id": "extract_facts",
            "kind": "tool",
            "label": "Extract concise facts",
            "tool": "extract_facts",
            "risky": "extract_facts" in risky_tools,
            "approval_required": False,
            "terminal": False,
        },
        {
            "id": "draft_report",
            "kind": "tool",
            "label": "Draft markdown report",
            "tool": "draft_report",
            "risky": "draft_report" in risky_tools,
            "approval_required": False,
            "terminal": False,
        },
        {
            "id": "waiting_approval",
            "kind": "approval_gate",
            "label": "Await human approval",
            "pending_action": "finalize_report",
            "approval_required": True,
            "terminal": False,
        },
        {
            "id": "finalize_report",
            "kind": "tool",
            "label": "Write final markdown to disk",
            "tool": "finalize_report",
            "risky": "finalize_report" in risky_tools,
            "approval_required": True,
            "terminal": False,
        },
        {
            "id": "done",
            "kind": "terminal",
            "label": "Run completed",
            "terminal": True,
        },
        {
            "id": "failed",
            "kind": "terminal",
            "label": "Run failed",
            "terminal": True,
        },
    ]
    transitions = [
        {
            "from": "init",
            "to": "search_mock",
            "event": "start",
            "condition": "run_until_pause_or_complete enters the first step",
        },
        {
            "from": "search_mock",
            "to": "extract_facts",
            "event": "tool_ok",
            "condition": "matches extracted successfully",
        },
        {
            "from": "search_mock",
            "to": "failed",
            "event": "tool_error",
            "condition": "search_mock failed after retries",
        },
        {
            "from": "extract_facts",
            "to": "draft_report",
            "event": "tool_ok",
            "condition": "facts extracted successfully",
        },
        {
            "from": "extract_facts",
            "to": "failed",
            "event": "tool_error",
            "condition": "extract_facts failed after retries",
        },
        {
            "from": "draft_report",
            "to": "waiting_approval",
            "event": "review_passed",
            "condition": "review passes and finalize_report needs approval",
            "approval_gate": True,
            "pending_action": "finalize_report",
        },
        {
            "from": "draft_report",
            "to": "failed",
            "event": "review_failed",
            "condition": "review rejects the draft or reviewer output is invalid",
        },
        {
            "from": "waiting_approval",
            "to": "waiting_approval",
            "event": "resume_without_approval",
            "condition": "run is resumed before approval is granted",
            "approval_gate": True,
            "pending_action": "finalize_report",
        },
        {
            "from": "waiting_approval",
            "to": "finalize_report",
            "event": "approval_granted",
            "condition": "approve() marks the pending action as approved",
            "approval_gate": True,
            "pending_action": "finalize_report",
        },
        {
            "from": "finalize_report",
            "to": "done",
            "event": "tool_ok",
            "condition": "final report written successfully",
        },
        {
            "from": "finalize_report",
            "to": "failed",
            "event": "tool_error",
            "condition": "finalize_report failed after retries",
        },
    ]
    return {
        "entry_state": "init",
        "current_implementation": "linear_state_machine_with_approval_gate",
        "terminal_states": sorted(TERMINAL_STATES),
        "nodes": nodes,
        "transitions": transitions,
    }


def workflow_to_mermaid(workflow: dict[str, Any]) -> str:
    node_lines = ["flowchart TD"]
    for node in workflow.get("nodes", []):
        node_id = node["id"]
        label = node.get("label", node_id)
        if node.get("kind") == "approval_gate":
            node_lines.append(f'    {node_id}{{{label}}}')
        elif node_id in TERMINAL_STATES or node.get("terminal"):
            node_lines.append(f'    {node_id}([{label}])')
        else:
            node_lines.append(f'    {node_id}[{label}]')

    edge_lines = []
    for transition in workflow.get("transitions", []):
        event = transition.get("event", "next")
        condition = transition.get("condition")
        label = event if not condition else f"{event} / {condition}"
        edge_lines.append(f'    {transition["from"]} -->|{label}| {transition["to"]}')

    return "\n".join(node_lines + edge_lines)
