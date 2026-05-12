from __future__ import annotations

from pathlib import Path
from typing import Any

from .policy import PolicyEngine
from .tools import ToolRegistry, default_registry
from .workflow import build_workflow_definition


def build_reference_blueprint(
    registry: ToolRegistry | None = None,
    *,
    store_root: str | Path = ".runs",
    policy: PolicyEngine | None = None,
) -> dict[str, Any]:
    registry = registry or default_registry()
    policy = policy or PolicyEngine(registry, store_root=store_root)
    workflow = build_workflow_definition(registry)
    tools = sorted(registry.list(), key=lambda tool: tool.name)

    return {
        "name": "reference_agent_harness_blueprint",
        "version": 1,
        "runtime": {
            "entrypoint": "harness_engineering.cli",
            "runner_class": "HarnessRunner",
            "state_store": "RunStore",
            "default_runs_dir": str(Path(store_root)),
            "execution_model": workflow.get("current_implementation"),
            "durability": {
                "checkpoints": ["state.json", "trace.json", "summary.json", "memory.json", "trace_summary.json", "handoffs.json"],
                "resume_supported": True,
                "approval_pause_supported": True,
            },
        },
        "components": [
            {
                "id": "cli",
                "label": "CLI",
                "module": "src/harness_engineering/cli.py",
                "responsibilities": [
                    "parse commands",
                    "create runner",
                    "start, inspect, approve, resume, and export runtime views",
                ],
            },
            {
                "id": "runner",
                "label": "Harness runner",
                "module": "src/harness_engineering/runner.py",
                "class": "HarnessRunner",
                "responsibilities": [
                    "execute workflow steps",
                    "evaluate policy before tool execution",
                    "pause for approval",
                    "record step metrics and trace events",
                ],
            },
            {
                "id": "tools",
                "label": "Tool registry",
                "module": "src/harness_engineering/tools.py",
                "responsibilities": [
                    "register typed tools",
                    "classify action categories",
                    "define risky side effects",
                ],
            },
            {
                "id": "policy",
                "label": "Policy engine",
                "module": "src/harness_engineering/policy.py",
                "class": "PolicyEngine",
                "responsibilities": [
                    "check tool/action rules",
                    "enforce allowed output roots",
                    "persist policy decisions into runtime state",
                ],
            },
            {
                "id": "store",
                "label": "Run store",
                "module": "src/harness_engineering/store.py",
                "class": "RunStore",
                "responsibilities": [
                    "persist state and trace artifacts",
                    "build operator summaries",
                    "support inspect/history flows",
                ],
            },
            {
                "id": "provider",
                "label": "Model/provider adapter",
                "module": "src/harness_engineering/provider.py",
                "responsibilities": [
                    "load repo-local model config",
                    "check connectivity via doctor",
                    "generate draft markdown",
                ],
            },
            {
                "id": "memory",
                "label": "Memory snapshot",
                "module": "src/harness_engineering/memory.py",
                "responsibilities": [
                    "separate working, session, and retrieval memory",
                    "write machine-readable memory.json artifacts",
                ],
            },
            {
                "id": "tracing",
                "label": "Tracing and summaries",
                "module": "src/harness_engineering/tracing.py",
                "responsibilities": [
                    "record trace events",
                    "roll up observability and cost/performance summaries",
                ],
            },
            {
                "id": "workflow",
                "label": "Workflow definition",
                "module": "src/harness_engineering/workflow.py",
                "responsibilities": [
                    "define state-machine nodes and transitions",
                    "export workflow graph views",
                ],
            },
        ],
        "tooling": {
            "count": len(tools),
            "tools": [
                {
                    "name": tool.name,
                    "risky": tool.risky,
                    "action_category": tool.action_category,
                    "input_schema": tool.input_schema,
                }
                for tool in tools
            ],
            "action_categories": sorted({tool.action_category for tool in tools}),
        },
        "workflow": workflow,
        "artifacts": {
            "run_directory_template": str(Path(store_root) / "<run_id>"),
            "files": {
                "state": "state.json",
                "trace": "trace.json",
                "summary": "summary.json",
                "trace_summary": "trace_summary.json",
                "memory": "memory.json",
                "handoffs": "handoffs.json",
                "final_report": "final_report.md",
            },
        },
        "interfaces": {
            "mcp_style_tools": {
                "module": "src/harness_engineering/mcp.py",
                "available": True,
            },
            "multi_agent_mode": {
                "module": "src/harness_engineering/multi_agent.py",
                "available": True,
                "note": "Keeps a small linear workflow and records planner/executor/reviewer handoffs rather than spawning a swarm.",
            },
        },
        "policy": policy.describe(),
        "limitations": [
            "No true distributed workflow runtime or remote task queue.",
            "No provider-accurate token billing or pricing integration.",
            "Policy checks are application-level, not OS-level sandboxing.",
            "Multi-agent mode is explicit handoff recording, not parallel scheduling.",
        ],
    }


def blueprint_to_markdown(blueprint: dict[str, Any]) -> str:
    lines = [
        "# Reference Agent Harness Blueprint",
        "",
        "## Runtime",
        "",
        f"- Entrypoint: `{blueprint['runtime']['entrypoint']}`",
        f"- Runner: `{blueprint['runtime']['runner_class']}`",
        f"- Store: `{blueprint['runtime']['state_store']}`",
        f"- Execution model: `{blueprint['runtime']['execution_model']}`",
        f"- Default runs dir: `{blueprint['runtime']['default_runs_dir']}`",
        "",
        "## Components",
        "",
    ]

    for component in blueprint.get("components", []):
        lines.append(f"### {component['label']}")
        lines.append("")
        lines.append(f"- Module: `{component['module']}`")
        if component.get("class"):
            lines.append(f"- Class: `{component['class']}`")
        lines.append("- Responsibilities:")
        for item in component.get("responsibilities", []):
            lines.append(f"  - {item}")
        lines.append("")

    lines.extend([
        "## Tools",
        "",
        f"- Tool count: {blueprint['tooling']['count']}",
        f"- Action categories: {', '.join(blueprint['tooling']['action_categories'])}",
        "",
        "## Persisted artifacts",
        "",
        f"- Run directory: `{blueprint['artifacts']['run_directory_template']}`",
    ])

    for name, filename in blueprint.get("artifacts", {}).get("files", {}).items():
        lines.append(f"- {name}: `{filename}`")

    lines.extend([
        "",
        "## Limitations",
        "",
    ])
    for item in blueprint.get("limitations", []):
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def blueprint_to_mermaid(blueprint: dict[str, Any]) -> str:
    lines = [
        "flowchart TD",
        '    cli["CLI\\nsrc/harness_engineering/cli.py"]',
        '    runner["HarnessRunner\\nsrc/harness_engineering/runner.py"]',
        '    tools["ToolRegistry\\nsrc/harness_engineering/tools.py"]',
        '    policy["PolicyEngine\\nsrc/harness_engineering/policy.py"]',
        '    provider["Provider adapter\\nsrc/harness_engineering/provider.py"]',
        '    store[("RunStore artifacts\\nstate/trace/summary/memory")]',
        '    tracing["Tracing\\nsrc/harness_engineering/tracing.py"]',
        '    memory["Memory snapshot\\nsrc/harness_engineering/memory.py"]',
        '    workflow["Workflow definition\\nsrc/harness_engineering/workflow.py"]',
        '    final_report[["final_report.md"]]',
        "",
        "    cli --> runner",
        "    runner --> workflow",
        "    runner --> tools",
        "    runner --> policy",
        "    runner --> provider",
        "    runner --> tracing",
        "    runner --> memory",
        "    runner --> store",
        "    tools --> store",
        "    tracing --> store",
        "    memory --> store",
        "    runner --> final_report",
    ]
    if blueprint.get("interfaces", {}).get("multi_agent_mode", {}).get("available"):
        lines.extend([
            '    multiagent["Planner / Executor / Reviewer\\nmulti_agent.py"]',
            "    runner --> multiagent",
            "    multiagent --> store",
        ])
    return "\n".join(lines)
