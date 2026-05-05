from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness_engineering.cli import main as cli_main
from harness_engineering.mcp import call_tool, call_tool_mcp, registry_to_mcp_tools, validate_tool_arguments
from harness_engineering.memory import build_memory_snapshot, retrieve_memory
from harness_engineering.provider import build_report_markdown
from harness_engineering.reviewer import build_plan, review_markdown
from harness_engineering.runner import HarnessRunner
from harness_engineering.store import RunStore
from harness_engineering.tools import ToolError, default_registry, load_source_documents
from harness_engineering.workflow import build_workflow_definition, workflow_to_mermaid


SAMPLE_DOCS = [
    {
        "title": "Tool contracts",
        "content": "Typed tool contracts reduce ambiguity and make traces easier to interpret.",
    },
    {
        "title": "Approval gates",
        "content": "Risky actions should require explicit approval and should resume cleanly after approval.",
    },
]


class HarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = RunStore(self.root / ".runs")
        self.runner = HarnessRunner(store=self.store, registry=default_registry())

    def test_run_pauses_for_approval_then_completes(self) -> None:
        with patch("harness_engineering.runner.create_plan_from_env", return_value=([
            "Search source documents for topic: approval gated harness",
            "Extract concise facts from relevant matches",
            "Draft a markdown report from the facts",
            "Require human approval before writing the final report to disk",
        ], "mock")), patch("harness_engineering.runner.review_from_env", return_value={"reviewer": "mock", "passed": True, "findings": []}), patch("harness_engineering.tools.create_client_from_env", return_value=None):
            state = self.runner.create_run("approval gated harness", SAMPLE_DOCS)
            state = self.runner.run_until_pause_or_complete(state)
        self.assertEqual(state.status, "waiting_approval")
        self.assertTrue(state.requires_approval)
        self.assertEqual(state.current_step, "finalize_report")

        approved = self.runner.approve(state.run_id)
        self.assertTrue(approved.approved)

        resumed = self.runner.resume(state.run_id)
        self.assertEqual(resumed.status, "completed")
        final_path = Path(resumed.artifacts["final_report"]["path"])
        self.assertTrue(final_path.exists())
        self.assertIn("Report:", final_path.read_text(encoding="utf-8"))

    def test_store_roundtrip(self) -> None:
        state = self.runner.create_run("roundtrip", SAMPLE_DOCS)
        loaded = self.store.load(state.run_id)
        self.assertEqual(loaded.run_id, state.run_id)
        self.assertEqual(loaded.topic, "roundtrip")

    def test_retry_flaky_tool_succeeds(self) -> None:
        flag = self.root / "flaky.flag"
        result = self.runner.retry.call(
            "flaky_echo",
            default_registry().get("flaky_echo").handler,
            message="hello",
            fail_once=True,
            state_file=str(flag),
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.attempts, 2)

    def test_load_source_documents(self) -> None:
        path = self.root / "sources.json"
        path.write_text(json.dumps(SAMPLE_DOCS), encoding="utf-8")
        docs = load_source_documents(str(path))
        self.assertEqual(len(docs), 2)

    def test_interactive_cli_completes_when_approved(self) -> None:
        path = self.root / "sources.json"
        path.write_text(json.dumps(SAMPLE_DOCS), encoding="utf-8")
        with patch("harness_engineering.runner.create_plan_from_env", return_value=([
            "Search source documents for topic: interactive harness",
            "Extract concise facts from relevant matches",
            "Draft a markdown report from the facts",
            "Require human approval before writing the final report to disk",
        ], "mock")), patch("harness_engineering.runner.review_from_env", return_value={"reviewer": "mock", "passed": True, "findings": []}), patch("harness_engineering.tools.create_client_from_env", return_value=None), patch("builtins.input", return_value="y"):
            code = cli_main([
                "interactive",
                "--topic",
                "interactive harness",
                "--source-file",
                str(path),
                "--runs-dir",
                str(self.root / ".runs"),
            ])
        self.assertEqual(code, 0)
        run_id = self.store.latest_run_id()
        self.assertIsNotNone(run_id)
        state = self.store.load(run_id)
        self.assertEqual(state.status, "completed")

    def test_build_report_markdown_without_provider(self) -> None:
        markdown = build_report_markdown("demo", ["fact one"])
        self.assertIn("# Report: demo", markdown)
        self.assertIn("fact one", markdown)

    def test_doctor_returns_mock_status_by_default(self) -> None:
        with patch("harness_engineering.cli.doctor_check", return_value={"status": "mock", "message": "Using mock provider"}):
            code = cli_main(["doctor"])
        self.assertEqual(code, 0)

    def test_mock_planner_and_reviewer(self) -> None:
        plan = build_plan("demo topic", SAMPLE_DOCS)
        self.assertEqual(len(plan), 4)
        review = review_markdown("demo topic", "# Report: demo\n\n## Key Findings\n\n- x\n\n## Harness Notes\n\n- y")
        self.assertTrue(review["passed"])

    def test_registry_exports_mcp_style_descriptors(self) -> None:
        tools = registry_to_mcp_tools(default_registry())
        search_tool = next(item for item in tools if item["name"] == "search_mock")
        self.assertEqual(search_tool["inputSchema"]["type"], "object")
        self.assertFalse(search_tool["meta"]["risky"])

    def test_call_tool_validates_arguments(self) -> None:
        registry = default_registry()
        result = call_tool(registry, "extract_facts", {"matches": []})
        self.assertEqual(result["facts"], [])
        with self.assertRaises(ToolError):
            validate_tool_arguments(registry.get("extract_facts"), {"matches": [], "extra": True})

    def test_call_tool_mcp_returns_structured_error(self) -> None:
        registry = default_registry()
        result = call_tool_mcp(registry, "extract_facts", {"matches": "wrong"})
        self.assertTrue(result["isError"])
        self.assertIn("Invalid arguments", result["structuredContent"]["error"])

    def test_cli_mcp_tools_and_call(self) -> None:
        code = cli_main(["mcp-tools"])
        self.assertEqual(code, 0)
        code = cli_main(["mcp-call", "extract_facts", '{"matches": []}'])
        self.assertEqual(code, 0)

    def test_workflow_definition_marks_approval_and_terminal_states(self) -> None:
        workflow = build_workflow_definition(default_registry())
        self.assertEqual(workflow["entry_state"], "init")
        waiting = next(node for node in workflow["nodes"] if node["id"] == "waiting_approval")
        finalize = next(node for node in workflow["nodes"] if node["id"] == "finalize_report")
        self.assertTrue(waiting["approval_required"])
        self.assertTrue(finalize["risky"])
        self.assertIn("failed", workflow["terminal_states"])

    def test_workflow_mermaid_contains_approval_gate(self) -> None:
        workflow = build_workflow_definition(default_registry())
        mermaid = workflow_to_mermaid(workflow)
        self.assertIn("flowchart TD", mermaid)
        self.assertIn("waiting_approval{Await human approval}", mermaid)
        self.assertIn("waiting_approval -->|approval_granted / approve() marks the pending action as approved| finalize_report", mermaid)

    def test_cli_workflow_json_and_mermaid(self) -> None:
        code = cli_main(["workflow"])
        self.assertEqual(code, 0)
        code = cli_main(["workflow", "--format", "mermaid"])
        self.assertEqual(code, 0)

    def test_store_builds_summary_for_waiting_run(self) -> None:
        with patch("harness_engineering.runner.create_plan_from_env", return_value=([
            "Search source documents for topic: summary harness",
            "Extract concise facts from relevant matches",
            "Draft a markdown report from the facts",
            "Require human approval before writing the final report to disk",
        ], "mock")), patch("harness_engineering.runner.review_from_env", return_value={"reviewer": "mock", "passed": True, "findings": []}), patch("harness_engineering.tools.create_client_from_env", return_value=None):
            state = self.runner.create_run("summary harness", SAMPLE_DOCS)
            state = self.runner.run_until_pause_or_complete(state)
        summary = self.store.build_summary(state)
        self.assertEqual(summary["status"], "waiting_approval")
        self.assertTrue(summary["requires_approval"])
        self.assertEqual(summary["pending_action"], "finalize_report")
        self.assertEqual(summary["paths"]["summary"], str(self.store.summary_path(state.run_id)))
        self.assertEqual(len(summary["next_commands"]), 2)

    def test_history_filters_trace_events(self) -> None:
        with patch("harness_engineering.runner.create_plan_from_env", return_value=([
            "Search source documents for topic: history harness",
            "Extract concise facts from relevant matches",
            "Draft a markdown report from the facts",
            "Require human approval before writing the final report to disk",
        ], "mock")), patch("harness_engineering.runner.review_from_env", return_value={"reviewer": "mock", "passed": True, "findings": []}), patch("harness_engineering.tools.create_client_from_env", return_value=None):
            state = self.runner.create_run("history harness", SAMPLE_DOCS)
            state = self.runner.run_until_pause_or_complete(state)
        history = self.store.history(state.run_id, event="approval_required")
        self.assertEqual(len(history["trace"]), 1)
        self.assertEqual(history["trace"][0]["event"], "approval_required")

    def test_cli_summary_and_history(self) -> None:
        path = self.root / "sources.json"
        path.write_text(json.dumps(SAMPLE_DOCS), encoding="utf-8")
        with patch("harness_engineering.runner.create_plan_from_env", return_value=([
            "Search source documents for topic: cli summary harness",
            "Extract concise facts from relevant matches",
            "Draft a markdown report from the facts",
            "Require human approval before writing the final report to disk",
        ], "mock")), patch("harness_engineering.runner.review_from_env", return_value={"reviewer": "mock", "passed": True, "findings": []}), patch("harness_engineering.tools.create_client_from_env", return_value=None):
            code = cli_main([
                "start",
                "--topic",
                "cli summary harness",
                "--source-file",
                str(path),
                "--runs-dir",
                str(self.root / ".runs"),
            ])
        self.assertEqual(code, 0)
        code = cli_main(["summary", "--latest", "--runs-dir", str(self.root / ".runs")])
        self.assertEqual(code, 0)
        code = cli_main(["history", "--latest", "--runs-dir", str(self.root / ".runs"), "--tail", "3"])
        self.assertEqual(code, 0)

    def test_memory_snapshot_separates_layers(self) -> None:
        with patch("harness_engineering.runner.create_plan_from_env", return_value=([
            "Search source documents for topic: memory architecture",
            "Extract concise facts from relevant matches",
            "Draft a markdown report from the facts",
            "Require human approval before writing the final report to disk",
        ], "mock")), patch("harness_engineering.runner.review_from_env", return_value={"reviewer": "mock", "passed": True, "findings": []}), patch("harness_engineering.tools.create_client_from_env", return_value=None):
            state = self.runner.create_run("memory architecture", SAMPLE_DOCS)
            state = self.runner.run_until_pause_or_complete(state)
        snapshot = build_memory_snapshot(state, query="approval", top_k=2)
        self.assertIn("working_context", snapshot["layers"])
        self.assertIn("session_state", snapshot["layers"])
        self.assertIn("retrieval_memory", snapshot["layers"])
        self.assertEqual(snapshot["layers"]["working_context"]["current_step"], "finalize_report")
        self.assertEqual(snapshot["layers"]["session_state"]["status"], "waiting_approval")
        self.assertLessEqual(len(snapshot["layers"]["retrieval_memory"]["results"]), 2)

    def test_retrieval_memory_prefers_matching_entries(self) -> None:
        with patch("harness_engineering.runner.create_plan_from_env", return_value=([
            "Search source documents for topic: approval gates",
            "Extract concise facts from relevant matches",
            "Draft a markdown report from the facts",
            "Require human approval before writing the final report to disk",
        ], "mock")), patch("harness_engineering.runner.review_from_env", return_value={"reviewer": "mock", "passed": True, "findings": []}), patch("harness_engineering.tools.create_client_from_env", return_value=None):
            state = self.runner.create_run("approval gates", SAMPLE_DOCS)
            state = self.runner.run_until_pause_or_complete(state)
        retrieval = retrieve_memory(state, query="approval", top_k=3)
        self.assertGreaterEqual(len(retrieval["results"]), 1)
        self.assertTrue(any("approval" in item["content"].lower() for item in retrieval["results"]))

    def test_store_writes_memory_snapshot_file(self) -> None:
        with patch("harness_engineering.runner.create_plan_from_env", return_value=([
            "Search source documents for topic: memory file",
            "Extract concise facts from relevant matches",
            "Draft a markdown report from the facts",
            "Require human approval before writing the final report to disk",
        ], "mock")), patch("harness_engineering.runner.review_from_env", return_value={"reviewer": "mock", "passed": True, "findings": []}), patch("harness_engineering.tools.create_client_from_env", return_value=None):
            state = self.runner.create_run("memory file", SAMPLE_DOCS)
            state = self.runner.run_until_pause_or_complete(state)
        memory_path = self.store.memory_path(state.run_id)
        self.assertTrue(memory_path.exists())
        saved = json.loads(memory_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["run_id"], state.run_id)
        self.assertIn("retrieval_memory", saved["layers"])

    def test_cli_memory_command(self) -> None:
        path = self.root / "sources.json"
        path.write_text(json.dumps(SAMPLE_DOCS), encoding="utf-8")
        with patch("harness_engineering.runner.create_plan_from_env", return_value=([
            "Search source documents for topic: cli memory harness",
            "Extract concise facts from relevant matches",
            "Draft a markdown report from the facts",
            "Require human approval before writing the final report to disk",
        ], "mock")), patch("harness_engineering.runner.review_from_env", return_value={"reviewer": "mock", "passed": True, "findings": []}), patch("harness_engineering.tools.create_client_from_env", return_value=None):
            code = cli_main([
                "start",
                "--topic",
                "cli memory harness",
                "--source-file",
                str(path),
                "--runs-dir",
                str(self.root / ".runs"),
            ])
        self.assertEqual(code, 0)
        code = cli_main([
            "memory",
            "--latest",
            "--runs-dir",
            str(self.root / ".runs"),
            "--query",
            "approval",
            "--top-k",
            "2",
        ])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
