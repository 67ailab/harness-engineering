from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .evals import run_eval_suite
from .mcp import call_tool_mcp, registry_to_mcp_tools
from .memory import build_memory_snapshot
from .policy import PolicyEngine
from .provider import doctor_check
from .runner import HarnessRunner
from .store import RunStore
from .tools import default_registry, load_source_documents
from .tracing import build_trace_summary
from .workflow import build_workflow_definition, workflow_to_mermaid


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="harness-engineering", description="Approval-gated harness engineering demo")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="Start a new run")
    start.add_argument("--topic", required=True)
    start.add_argument("--source-file", required=True)
    start.add_argument("--runs-dir", default=".runs")
    start.add_argument("--policy-file", help="Optional JSON policy file for tool/action rules")
    start.add_argument("--multi-agent", action="store_true", help="Run in multi-agent mode with explicit role handoffs")

    inspect = sub.add_parser("inspect", help="Inspect a run")
    inspect.add_argument("run_id", nargs="?")
    inspect.add_argument("--latest", action="store_true")
    inspect.add_argument("--runs-dir", default=".runs")

    summary = sub.add_parser("summary", help="Print a run summary with resume metadata")
    summary.add_argument("run_id", nargs="?")
    summary.add_argument("--latest", action="store_true")
    summary.add_argument("--runs-dir", default=".runs")

    history = sub.add_parser("history", help="Print trace history for a run")
    history.add_argument("run_id", nargs="?")
    history.add_argument("--latest", action="store_true")
    history.add_argument("--runs-dir", default=".runs")
    history.add_argument("--event", help="Filter history to a specific event name")
    history.add_argument("--tail", type=int, help="Show only the last N trace events")

    trace_summary = sub.add_parser("trace-summary", help="Print a compact trace/observability summary for a run")
    trace_summary.add_argument("run_id", nargs="?")
    trace_summary.add_argument("--latest", action="store_true")
    trace_summary.add_argument("--runs-dir", default=".runs")

    memory = sub.add_parser("memory", help="Inspect working, session, and retrieval memory layers for a run")
    memory.add_argument("run_id", nargs="?")
    memory.add_argument("--latest", action="store_true")
    memory.add_argument("--runs-dir", default=".runs")
    memory.add_argument("--query", help="Optional retrieval query; defaults to the run topic")
    memory.add_argument("--top-k", type=int, default=5, help="Maximum retrieval results to include")

    pending = sub.add_parser("pending", help="Inspect the current pending approval action for a run")
    pending.add_argument("run_id", nargs="?")
    pending.add_argument("--latest", action="store_true")
    pending.add_argument("--runs-dir", default=".runs")

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
    interactive.add_argument("--policy-file", help="Optional JSON policy file for tool/action rules")
    interactive.add_argument("--multi-agent", action="store_true", help="Run in multi-agent mode with explicit role handoffs")

    handoffs = sub.add_parser("handoffs", help="Inspect role handoffs in a multi-agent run")
    handoffs.add_argument("run_id", nargs="?")
    handoffs.add_argument("--latest", action="store_true")
    handoffs.add_argument("--runs-dir", default=".runs")

    mcp_tools = sub.add_parser("mcp-tools", help="Print MCP-style tool descriptors for the default registry")
    mcp_tools.add_argument("--pretty", action="store_true")

    mcp_call = sub.add_parser("mcp-call", help="Call a registered tool through the MCP-style adapter")
    mcp_call.add_argument("tool_name")
    mcp_call.add_argument("arguments_json")

    workflow = sub.add_parser("workflow", help="Inspect the harness workflow graph")
    workflow.add_argument("--format", choices=["json", "mermaid"], default="json")
    workflow.add_argument("--pretty", action="store_true")

    policy = sub.add_parser("policy", help="Inspect the effective policy for tools and action categories")
    policy.add_argument("--runs-dir", default=".runs")
    policy.add_argument("--policy-file", help="Optional JSON policy file to load instead of the built-in default")
    policy.add_argument("--pretty", action="store_true")

    evals = sub.add_parser("evals", help="Run lightweight trace-aware eval fixtures")
    evals.add_argument("--fixtures", default="sample_data/evals/basic.json")
    evals.add_argument("--runs-dir", default=".runs")

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


def _build_runner(*, runs_dir: str, policy_file: str | None = None, policy_config: dict | None = None) -> HarnessRunner:
    store = RunStore(runs_dir)
    registry = default_registry()
    if policy_config is not None:
        policy = PolicyEngine(registry, store_root=store.root, config=policy_config, config_path=policy_config.get("policy_file"))
    else:
        policy = PolicyEngine.from_file(registry, store_root=store.root, path=policy_file) if policy_file else PolicyEngine(registry, store_root=store.root)
    return HarnessRunner(store=store, registry=registry, policy=policy)


def _build_runner_for_existing_run(*, runs_dir: str, run_id: str) -> HarnessRunner:
    store = RunStore(runs_dir)
    state = store.load(run_id)
    policy_config = state.artifacts.get("policy") if isinstance(state.artifacts.get("policy"), dict) else None
    return _build_runner(runs_dir=runs_dir, policy_config=policy_config)


def cmd_start(args) -> int:
    store = RunStore(args.runs_dir)
    runner = _build_runner(runs_dir=args.runs_dir, policy_file=getattr(args, "policy_file", None))
    source_documents = load_source_documents(args.source_file)
    run_mode = "multi_agent" if getattr(args, "multi_agent", False) else "single"
    state = runner.create_run(topic=args.topic, source_documents=source_documents, run_mode=run_mode)
    state = runner.run_until_pause_or_complete(state)
    print(f"Run created: {state.run_id}")
    print(f"Run mode: {state.run_mode}")
    print(f"Status: {state.status}")
    if state.status == "waiting_approval":
        print("Approval required before finalize_report can write the final markdown file.")
        print(f"Next: python3 -m harness_engineering.cli approve {state.run_id}")
        print(f"Then: python3 -m harness_engineering.cli resume {state.run_id}")
    if run_mode == "multi_agent":
        print(f"Inspect handoffs: python3 -m harness_engineering.cli handoffs {state.run_id}")
    return 0


def cmd_inspect(args) -> int:
    store = RunStore(args.runs_dir)
    run_id = _resolve_run_id(store, args.run_id, args.latest)
    state = store.load(run_id)
    print(json.dumps(state.to_dict(), indent=2, ensure_ascii=False))
    return 0


def cmd_summary(args) -> int:
    store = RunStore(args.runs_dir)
    run_id = _resolve_run_id(store, args.run_id, args.latest)
    state = store.load(run_id)
    print(json.dumps(store.build_summary(state), indent=2, ensure_ascii=False))
    return 0


def cmd_history(args) -> int:
    store = RunStore(args.runs_dir)
    run_id = _resolve_run_id(store, args.run_id, args.latest)
    print(json.dumps(store.history(run_id, event=args.event, tail=args.tail), indent=2, ensure_ascii=False))
    return 0


def cmd_trace_summary(args) -> int:
    store = RunStore(args.runs_dir)
    run_id = _resolve_run_id(store, args.run_id, args.latest)
    state = store.load(run_id)
    print(json.dumps(build_trace_summary(state), indent=2, ensure_ascii=False))
    return 0


def cmd_memory(args) -> int:
    store = RunStore(args.runs_dir)
    run_id = _resolve_run_id(store, args.run_id, args.latest)
    state = store.load(run_id)
    print(json.dumps(build_memory_snapshot(state, query=args.query, top_k=args.top_k), indent=2, ensure_ascii=False))
    return 0


def cmd_pending(args) -> int:
    store = RunStore(args.runs_dir)
    run_id = _resolve_run_id(store, args.run_id, args.latest)
    state = store.load(run_id)
    pending = state.artifacts.get("pending_action_details") or {
        "action": state.pending_action,
        "status": state.status,
        "requires_approval": state.requires_approval,
        "message": "No structured pending-action details are available for this run.",
    }
    print(json.dumps({
        "run_id": state.run_id,
        "status": state.status,
        "requires_approval": state.requires_approval,
        "pending_action": state.pending_action,
        "details": pending,
    }, indent=2, ensure_ascii=False))
    return 0


def cmd_approve(args) -> int:
    runner = _build_runner_for_existing_run(runs_dir=args.runs_dir, run_id=args.run_id)
    state = runner.approve(args.run_id)
    print(f"Approved run {state.run_id}. Current status: {state.status}")
    return 0


def cmd_resume(args) -> int:
    runner = _build_runner_for_existing_run(runs_dir=args.runs_dir, run_id=args.run_id)
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


def cmd_handoffs(args) -> int:
    store = RunStore(args.runs_dir)
    run_id = _resolve_run_id(store, args.run_id, args.latest)
    state = store.load(run_id)

    if state.run_mode != "multi_agent":
        print(json.dumps({
            "run_id": state.run_id,
            "run_mode": state.run_mode,
            "message": "This run is not in multi-agent mode. No handoffs recorded.",
        }, indent=2, ensure_ascii=False))
        return 0

    handoffs = state.artifacts.get("handoffs", [])
    role_executions = state.artifacts.get("role_executions", [])

    print(json.dumps({
        "run_id": state.run_id,
        "run_mode": state.run_mode,
        "current_role": state.artifacts.get("current_role"),
        "handoff_count": len(handoffs),
        "role_execution_count": len(role_executions),
        "handoffs": handoffs,
        "role_executions": role_executions,
    }, indent=2, ensure_ascii=False))
    return 0


def cmd_interactive(args) -> int:
    store = RunStore(args.runs_dir)
    runner = _build_runner(runs_dir=args.runs_dir, policy_file=getattr(args, "policy_file", None))
    source_documents = load_source_documents(args.source_file)
    run_mode = "multi_agent" if getattr(args, "multi_agent", False) else "single"
    state = runner.create_run(topic=args.topic, source_documents=source_documents, run_mode=run_mode)
    state = runner.run_until_pause_or_complete(state)

    provider_used = state.step_results[-1].output.get("provider") if state.step_results else None
    print(f"Interactive demo run: {state.run_id}")
    if provider_used:
        print(f"Draft provider: {provider_used}")
    print(f"Status: {state.status}")
    if state.status == "waiting_approval":
        print()
        pending = state.artifacts.get("pending_action_details", {})
        print(f"Pending action: {pending.get('action', 'finalize_report')}")
        print(f"Reason: {pending.get('reason', 'No reason recorded')}")
        if pending.get("proposed_output_path"):
            print(f"Output path: {pending.get('proposed_output_path')}")
        preview = pending.get("draft_preview", {})
        if preview:
            print(f"Draft preview: {preview.get('line_count')} lines, {preview.get('char_count')} chars")
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


def cmd_policy(args) -> int:
    registry = default_registry()
    policy = PolicyEngine.from_file(registry, store_root=args.runs_dir, path=args.policy_file) if args.policy_file else PolicyEngine(registry, store_root=args.runs_dir)
    indent = 2 if args.pretty else None
    print(json.dumps(policy.describe(), indent=indent, ensure_ascii=False))
    return 0


def cmd_evals(args) -> int:
    result = run_eval_suite(fixtures_path=args.fixtures, runs_dir=args.runs_dir)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("failed", 0) == 0 else 1


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
