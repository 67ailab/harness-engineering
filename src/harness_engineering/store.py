from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from .models import RunState, now_iso


class RunStore:
    def __init__(self, root: str | Path = ".runs") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def run_dir(self, run_id: str) -> Path:
        path = self.root / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def state_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "state.json"

    def trace_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "trace.json"

    def summary_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "summary.json"

    def _duration_seconds(self, started_at: str, finished_at: str) -> int | None:
        try:
            start = datetime.fromisoformat(started_at)
            end = datetime.fromisoformat(finished_at)
        except ValueError:
            return None
        return max(0, int((end - start).total_seconds()))

    def build_summary(self, state: RunState) -> dict[str, Any]:
        event_counts = Counter(event.event for event in state.trace)
        total_attempts = sum(result.attempts for result in state.step_results)
        tool_attempts = Counter()
        for result in state.step_results:
            tool_attempts[result.tool_name] += result.attempts

        last_error = next((result.error for result in reversed(state.step_results) if result.error), None)
        final_report = state.artifacts.get("final_report", {})
        planner = state.artifacts.get("planner")
        review = state.artifacts.get("review", {})

        next_commands: list[str] = []
        if state.status == "waiting_approval":
            next_commands = [
                f"PYTHONPATH=src python3 -m harness_engineering.cli approve {state.run_id}",
                f"PYTHONPATH=src python3 -m harness_engineering.cli resume {state.run_id}",
            ]
        elif state.status in {"created", "running"}:
            next_commands = [f"PYTHONPATH=src python3 -m harness_engineering.cli resume {state.run_id}"]

        return {
            "run_id": state.run_id,
            "topic": state.topic,
            "status": state.status,
            "current_step": state.current_step,
            "created_at": state.created_at,
            "updated_at": state.updated_at,
            "duration_seconds": self._duration_seconds(state.created_at, state.updated_at),
            "requires_approval": state.requires_approval,
            "approved": state.approved,
            "pending_action": state.pending_action,
            "planner": planner,
            "reviewer": review.get("reviewer"),
            "review_passed": review.get("passed"),
            "review_findings": review.get("findings", []),
            "step_count": len(state.step_results),
            "steps_succeeded": sum(1 for result in state.step_results if result.ok),
            "steps_failed": sum(1 for result in state.step_results if not result.ok),
            "total_attempts": total_attempts,
            "tool_attempts": dict(tool_attempts),
            "pause_count": event_counts.get("approval_required", 0) + event_counts.get("approval_still_required", 0),
            "resume_count": event_counts.get("run_resumed", 0),
            "approval_count": event_counts.get("approval_granted", 0),
            "trace_event_counts": dict(event_counts),
            "last_error": last_error,
            "artifacts": {
                "draft_present": "draft_markdown" in state.artifacts,
                "final_report_path": final_report.get("path"),
                "final_report_bytes": final_report.get("bytes"),
            },
            "paths": {
                "run_dir": str(self.run_dir(state.run_id)),
                "state": str(self.state_path(state.run_id)),
                "trace": str(self.trace_path(state.run_id)),
                "summary": str(self.summary_path(state.run_id)),
            },
            "can_resume": state.status in {"created", "running", "waiting_approval"},
            "next_commands": next_commands,
        }

    def load_trace(self, run_id: str) -> list[dict[str, Any]]:
        path = self.trace_path(run_id)
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def history(self, run_id: str, event: str | None = None, tail: int | None = None) -> dict[str, Any]:
        state = self.load(run_id)
        trace = [event_item.__dict__ for event_item in state.trace]
        if event:
            trace = [item for item in trace if item.get("event") == event]
        if tail is not None and tail >= 0:
            trace = trace[-tail:]
        return {
            "run_id": state.run_id,
            "status": state.status,
            "current_step": state.current_step,
            "trace": trace,
            "step_results": [result.__dict__ for result in state.step_results],
        }

    def save(self, state: RunState) -> None:
        state.updated_at = now_iso()
        path = self.state_path(state.run_id)
        with path.open("w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
        with self.trace_path(state.run_id).open("w", encoding="utf-8") as f:
            json.dump([event.__dict__ for event in state.trace], f, ensure_ascii=False, indent=2)
        with self.summary_path(state.run_id).open("w", encoding="utf-8") as f:
            json.dump(self.build_summary(state), f, ensure_ascii=False, indent=2)

    def load(self, run_id: str) -> RunState:
        path = self.state_path(run_id)
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return RunState.from_dict(data)

    def list_runs(self) -> list[str]:
        return sorted([p.name for p in self.root.iterdir() if p.is_dir()])

    def latest_run_id(self) -> str | None:
        runs = [(p.stat().st_mtime, p.name) for p in self.root.iterdir() if p.is_dir()]
        if not runs:
            return None
        runs.sort()
        return runs[-1][1]
