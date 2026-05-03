from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .mcp import call_tool_mcp, registry_to_mcp_tools
from .provider import doctor_check
from .runner import HarnessRunner
from .store import RunStore
from .tools import default_registry, load_source_documents
from .workflow import build_workflow_definition, workflow_to_mermaid


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="harness-engineering", description="Approval-gated harness engineering demo")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="Start a new run")
    start.add_argument("--topic", required=True)
    start.add_argument("--source-file", required=True)
    start.add_argument("--runs-dir", default=".runs")

    inspect = sub.add_parser("inspect", help="Inspect a run")
    inspect.add_argument("run_id", nargs="?")
    inspect.add_argument("--latest", action="store_true")
    inspect.add_argument("--runs-dir", default=".runs")

    approve = sub.add_parser("approve", help="Approve the pending action for a run")
    approve.add_argument("run_id")
    approve.add_argument("--runs-dir", default=".runs")

    resume = sub.add_parser("resume", help="Resume a run")
    resume.add_argument("run_id")
    resume.add_argument("--runs-dir", default=".runs")

    list_cmd = sub.add_parser("list", help="List runs")
    list_cmd.add_argument("--runs-dir", default=".runs")

    interactive = sub.add_parser("interactive", help="Run an interactive approval-driven demo")
    interactive.add_argument("--topic", required=True)
    interactive.add_argument("--source-file", required=True)
    interactive.add_argument("--runs-dir", default=".runs")

    mcp_tools = sub.add_parser("mcp-tools", help="Print MCP-style tool descriptors for the default registry")
    mcp_tools.add_argument("--pretty", action="store_true")

    mcp_call = sub.add_parser("mcp-call", help="Call a registered tool through the MCP-style adapter")
    mcp_call.add_argument("tool_name")
    mcp_call.add_argument("arguments_json")

    workflow = sub.add_parser("workflow", help="Inspect the harness workflow graph")
    workflow.add_argument("--format", choices=["json", "mermaid"], default="json")
    workflow.add_argument("--pretty", action="store_true")

    sub.add_parser("doctor", help="Check provider/model connectivity and configuration")

    return parser


def _resolve_run_id(store: RunStore, run_id: str | None, latest: bool) -> str:
    if latest:
        found = store.latest_run_id()
        if not found:
            raise SystemExit("No runs found")
        return found
    if run_id:
        return run_id
    raise SystemExit("Provide run_id or use --latest")


def cmd_start(args) -> int:
    store = RunStore(args.runs_dir)
    runner = HarnessRunner(store=store)
    source_documents = load_source_documents(args.source_file)
    state = runner.create_run(topic=args.topic, source_documents=source_documents)
    state = runner.run_until_pause_or_complete(state)
    print(f"Run created: {state.run_id}")
    print(f"Status: {state.status}")
    if state.status == "waiting_approval":
        print("Approval required before finalize_report can write the final markdown file.")
        print(f"Next: python3 -m harness_engineering.cli approve {state.run_id}")
        print(f"Then: python3 -m harness_engineering.cli resume {state.run_id}")
    return 0


def cmd_inspect(args) -> int:
    store = RunStore(args.runs_dir)
    run_id = _resolve_run_id(store, args.run_id, args.latest)
    state = store.load(run_id)
    print(json.dumps(state.to_dict(), indent=2, ensure_ascii=False))
    return 0


def cmd_approve(args) -> int:
    store = RunStore(args.runs_dir)
    runner = HarnessRunner(store=store)
    state = runner.approve(args.run_id)
    print(f"Approved run {state.run_id}. Current status: {state.status}")
    return 0


def cmd_resume(args) -> int:
    store = RunStore(args.runs_dir)
    runner = HarnessRunner(store=store)
    state = runner.resume(args.run_id)
    print(f"Run {state.run_id} status: {state.status}")
    if state.status == "completed":
        final_info = state.artifacts.get("final_report", {})
        print(f"Final report: {final_info.get('path')}")
    return 0


def cmd_list(args) -> int:
    store = RunStore(args.runs_dir)
    runs = store.list_runs()
    if not runs:
        print("No runs found")
        return 0
    for run_id in runs:
        try:
            state = store.load(run_id)
            print(f"{run_id}  {state.status}  {state.topic}")
        except FileNotFoundError:
            print(f"{run_id}  <missing state>")
    return 0


def cmd_interactive(args) -> int:
    store = RunStore(args.runs_dir)
    runner = HarnessRunner(store=store)
    source_documents = load_source_documents(args.source_file)
    state = runner.create_run(topic=args.topic, source_documents=source_documents)
    state = runner.run_until_pause_or_complete(state)

    provider_used = state.step_results[-1].output.get("provider") if state.step_results else None
    print(f"Interactive demo run: {state.run_id}")
    if provider_used:
        print(f"Draft provider: {provider_used}")
    print(f"Status: {state.status}")
    if state.status == "waiting_approval":
        print()
        print("Pending action: finalize_report")
        print("Preview of draft report:")
        print("=" * 60)
        print(state.artifacts.get("draft_markdown", ""))
        print("=" * 60)
        answer = input("Approve writing final_report.md? [y/N]: ").strip().lower()
        if answer in {"y", "yes"}:
            runner.approve(state.run_id)
            state = runner.resume(state.run_id)
            print(f"Completed. Final report: {state.artifacts.get('final_report', {}).get('path')}")
        else:
            print("Approval withheld. Run is safely checkpointed and can be resumed later.")
            print(f"Resume later with: PYTHONPATH=src python3 -m harness_engineering.cli resume {state.run_id}")
    return 0


def cmd_mcp_tools(args) -> int:
    tools = registry_to_mcp_tools(default_registry())
    indent = 2 if args.pretty else None
    print(json.dumps({"tools": tools}, indent=indent, ensure_ascii=False))
    return 0


def cmd_mcp_call(args) -> int:
    registry = default_registry()
    arguments = json.loads(args.arguments_json)
    result = call_tool_mcp(registry=registry, tool_name=args.tool_name, arguments=arguments)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if not result.get("isError") else 1


def cmd_workflow(args) -> int:
    workflow = build_workflow_definition(default_registry())
    if args.format == "mermaid":
        print(workflow_to_mermaid(workflow))
        return 0
    indent = 2 if args.pretty else None
    print(json.dumps(workflow, indent=indent, ensure_ascii=False))
    return 0


def cmd_doctor(args) -> int:
    result = doctor_check()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("status") in {"ok", "mock"} else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command.replace("-", "_")
    handler = globals().get(f"cmd_{command}")
    if not handler:
        parser.error(f"Unknown command: {args.command}")
    return handler(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
