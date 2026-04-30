from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness_engineering.cli import main as cli_main
from harness_engineering.provider import build_report_markdown
from harness_engineering.runner import HarnessRunner
from harness_engineering.store import RunStore
from harness_engineering.tools import default_registry, load_source_documents


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
        with patch("builtins.input", return_value="y"):
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
        code = cli_main(["doctor"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
