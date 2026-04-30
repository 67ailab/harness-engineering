from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import RunState, StepResult
from .reviewer import create_plan_from_env, review_from_env
from .store import RunStore
from .tools import ToolError, ToolRegistry, default_registry
from .tracing import add_trace


class RetryPolicy:
    def __init__(self, max_attempts: int = 2) -> None:
        self.max_attempts = max_attempts

    def call(self, tool_name: str, func, *args, **kwargs) -> StepResult:
        last_error = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                output = func(*args, **kwargs)
                return StepResult(tool_name=tool_name, ok=True, output=output, attempts=attempt)
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
        return StepResult(tool_name=tool_name, ok=False, output={}, attempts=self.max_attempts, error=last_error)


class HarnessRunner:
    def __init__(self, store: RunStore | None = None, registry: ToolRegistry | None = None) -> None:
        self.store = store or RunStore()
        self.registry = registry or default_registry()
        self.retry = RetryPolicy(max_attempts=2)

    def _execute(self, state: RunState, tool_name: str, **kwargs: Any) -> StepResult:
        tool = self.registry.get(tool_name)
        add_trace(state, "tool_start", tool=tool_name, args=kwargs)
        result = self.retry.call(tool_name, tool.handler, **kwargs)
        state.step_results.append(result)
        if result.ok:
            add_trace(state, "tool_ok", tool=tool_name, output=result.output, attempts=result.attempts)
        else:
            add_trace(state, "tool_error", tool=tool_name, error=result.error, attempts=result.attempts)
        self.store.save(state)
        return result

    def create_run(self, topic: str, source_documents: list[dict[str, Any]]) -> RunState:
        state = RunState.new(topic=topic, source_documents=source_documents)
        state.status = "running"
        plan, planner = create_plan_from_env(topic=topic, source_documents=source_documents)
        state.plan = plan or [
            "search_mock",
            "extract_facts",
            "draft_report",
            "finalize_report",
        ]
        state.artifacts["planner"] = planner
        add_trace(state, "run_created", topic=topic, plan=state.plan, planner=planner)
        self.store.save(state)
        return state

    def run_until_pause_or_complete(self, state: RunState) -> RunState:
        if state.status == "completed":
            return state
        if state.current_step == "init":
            state.current_step = "search_mock"
        while True:
            if state.current_step == "search_mock":
                result = self._execute(state, "search_mock", topic=state.topic, source_documents=state.source_documents)
                if not result.ok:
                    state.status = "failed"
                    break
                state.artifacts["matches"] = result.output["matches"]
                state.current_step = "extract_facts"
                self.store.save(state)
                continue

            if state.current_step == "extract_facts":
                result = self._execute(state, "extract_facts", matches=state.artifacts.get("matches", []))
                if not result.ok:
                    state.status = "failed"
                    break
                state.artifacts["facts"] = result.output["facts"]
                state.current_step = "draft_report"
                self.store.save(state)
                continue

            if state.current_step == "draft_report":
                result = self._execute(state, "draft_report", topic=state.topic, facts=state.artifacts.get("facts", []))
                if not result.ok:
                    state.status = "failed"
                    break
                state.artifacts["draft_markdown"] = result.output["markdown"]
                review = review_from_env(topic=state.topic, markdown=state.artifacts["draft_markdown"])
                state.artifacts["review"] = review
                add_trace(state, "draft_reviewed", review=review)
                if not review.get("passed", False):
                    state.status = "failed"
                    state.pending_action = None
                    state.requires_approval = False
                    self.store.save(state)
                    break
                state.current_step = "finalize_report"
                state.requires_approval = True
                state.pending_action = "finalize_report"
                state.status = "waiting_approval"
                add_trace(state, "approval_required", action="finalize_report")
                self.store.save(state)
                break

            if state.current_step == "finalize_report":
                if not state.approved:
                    state.requires_approval = True
                    state.pending_action = "finalize_report"
                    state.status = "waiting_approval"
                    add_trace(state, "approval_still_required", action="finalize_report")
                    self.store.save(state)
                    break
                output_path = str(Path(self.store.run_dir(state.run_id)) / "final_report.md")
                result = self._execute(state, "finalize_report", markdown=state.artifacts["draft_markdown"], output_path=output_path)
                if not result.ok:
                    state.status = "failed"
                    break
                state.artifacts["final_report"] = result.output
                state.current_step = "done"
                state.status = "completed"
                state.requires_approval = False
                state.pending_action = None
                add_trace(state, "run_completed", final_report=result.output)
                self.store.save(state)
                break

            if state.current_step == "done":
                state.status = "completed"
                self.store.save(state)
                break

            raise ToolError(f"Unknown step: {state.current_step}")
        self.store.save(state)
        return state

    def approve(self, run_id: str) -> RunState:
        state = self.store.load(run_id)
        state.approved = True
        state.requires_approval = False
        state.status = "running"
        add_trace(state, "approval_granted", action=state.pending_action)
        self.store.save(state)
        return state

    def resume(self, run_id: str) -> RunState:
        state = self.store.load(run_id)
        add_trace(state, "run_resumed", current_step=state.current_step, status=state.status)
        self.store.save(state)
        return self.run_until_pause_or_complete(state)
