from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Any

from .mcp import call_tool
from .models import RunState, StepResult, now_iso
from .multi_agent import build_multi_agent_snapshot, planner_step, reviewer_handoffs
from .policy import PolicyDecision, PolicyEngine
from .reviewer import create_plan_from_env, review_from_env
from .store import RunStore
from .tools import ToolError, ToolRegistry, default_registry
from .tracing import add_trace


class RetryPolicy:
    def __init__(self, max_attempts: int = 2) -> None:
        self.max_attempts = max_attempts

    def call(self, tool_name: str, func, *args, **kwargs) -> StepResult:
        last_error = None
        started_at = now_iso()
        started_clock = perf_counter()
        for attempt in range(1, self.max_attempts + 1):
            try:
                output = func(*args, **kwargs)
                finished_at = now_iso()
                duration_ms = max(0, int((perf_counter() - started_clock) * 1000))
                return StepResult(
                    tool_name=tool_name,
                    ok=True,
                    output=output,
                    attempts=attempt,
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_ms=duration_ms,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
        finished_at = now_iso()
        duration_ms = max(0, int((perf_counter() - started_clock) * 1000))
        return StepResult(
            tool_name=tool_name,
            ok=False,
            output={},
            attempts=self.max_attempts,
            error=last_error,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
        )


class HarnessRunner:
    def __init__(self, store: RunStore | None = None, registry: ToolRegistry | None = None, policy: PolicyEngine | None = None) -> None:
        self.store = store or RunStore()
        self.registry = registry or default_registry()
        self.policy = policy or PolicyEngine(self.registry, store_root=self.store.root)
        self.retry = RetryPolicy(max_attempts=2)

    def _record_policy_decision(self, state: RunState, decision: PolicyDecision) -> None:
        state.artifacts.setdefault("policy_decisions", []).append(decision.to_dict())

    def _record_role_activity(self, state: RunState, *, role: str, action: str, payload: dict[str, Any] | None = None) -> None:
        payload = payload or {}
        state.artifacts["current_role"] = role
        state.artifacts.setdefault("role_executions", []).append(
            {
                "role": role,
                "action": action,
                "timestamp": state.updated_at,
                "payload": payload,
            }
        )
        add_trace(state, "role_activity", role=role, action=action, payload=payload)

    def _record_handoff(self, state: RunState, handoff: dict[str, Any]) -> None:
        state.artifacts.setdefault("handoffs", []).append(handoff)
        state.artifacts["multi_agent"] = build_multi_agent_snapshot(state.artifacts.get("handoffs", []))
        add_trace(
            state,
            "role_handoff",
            from_role=handoff.get("from_role"),
            to_role=handoff.get("to_role"),
            purpose=handoff.get("purpose"),
            payload=handoff.get("payload"),
        )

    def _estimate_step_metrics(self, tool_name: str, result: StepResult, **kwargs: Any) -> dict[str, Any]:
        output = result.output or {}
        metrics: dict[str, Any] = {
            "duration_ms": result.duration_ms,
            "attempts": result.attempts,
            "ok": result.ok,
        }
        if tool_name == "search_mock":
            metrics["match_count"] = len(output.get("matches", []))
            metrics["estimated_work_units"] = len(output.get("matches", []))
        elif tool_name == "extract_facts":
            facts = output.get("facts", [])
            metrics["fact_count"] = len(facts)
            metrics["estimated_output_chars"] = sum(len(item) for item in facts)
            metrics["estimated_work_units"] = len(facts)
        elif tool_name == "draft_report":
            markdown = output.get("markdown", "")
            provider = output.get("provider", "unknown")
            char_count = len(markdown)
            estimated_input_chars = len(kwargs.get("topic", "")) + sum(len(item) for item in kwargs.get("facts", []))
            estimated_input_tokens = max(1, estimated_input_chars // 4) if estimated_input_chars else 0
            estimated_output_tokens = max(1, char_count // 4) if char_count else 0
            estimated_total_tokens = estimated_input_tokens + estimated_output_tokens
            metrics.update({
                "provider": provider,
                "markdown_chars": char_count,
                "estimated_input_tokens": estimated_input_tokens,
                "estimated_output_tokens": estimated_output_tokens,
                "estimated_total_tokens": estimated_total_tokens,
            })
            if provider == "openai_compatible":
                metrics["cost_estimate"] = {
                    "status": "unpriced",
                    "reason": "The demo does not know provider-specific pricing, so token counts are estimated without claiming dollar costs.",
                    "estimated_input_tokens": estimated_input_tokens,
                    "estimated_output_tokens": estimated_output_tokens,
                    "estimated_total_tokens": estimated_total_tokens,
                }
            else:
                metrics["cost_estimate"] = {
                    "status": "local_or_mock",
                    "reason": "Mock/local draft generation records work volume but does not assign a synthetic dollar cost.",
                    "estimated_total_tokens": estimated_total_tokens,
                }
        elif tool_name == "finalize_report":
            metrics["output_path"] = output.get("path") or kwargs.get("output_path")
            metrics["bytes_written"] = output.get("bytes")
            metrics["estimated_work_units"] = 1
        return metrics

    def _execute(self, state: RunState, tool_name: str, **kwargs: Any) -> StepResult:
        tool = self.registry.get(tool_name)
        decision = self.policy.evaluate(tool_name, kwargs)
        self._record_policy_decision(state, decision)
        add_trace(
            state,
            "policy_checked",
            tool=tool_name,
            action_category=decision.action_category,
            allowed=decision.allowed,
            reason=decision.reason,
            risky=tool.risky,
            args=kwargs,
        )
        if not decision.allowed:
            result = StepResult(tool_name=tool_name, ok=False, output={}, attempts=0, error=decision.reason, metrics={"policy_allowed": False})
            state.step_results.append(result)
            state.status = "failed"
            state.pending_action = None
            state.requires_approval = False
            add_trace(
                state,
                "policy_denied",
                tool=tool_name,
                action_category=decision.action_category,
                reason=decision.reason,
            )
            self.store.save(state)
            return result
        add_trace(
            state,
            "tool_start",
            tool=tool_name,
            args=kwargs,
            risky=tool.risky,
            action_category=tool.action_category,
        )
        result = self.retry.call(tool_name, call_tool, self.registry, tool_name, kwargs)
        result.metrics = self._estimate_step_metrics(tool_name, result, **kwargs)
        state.step_results.append(result)
        if result.ok:
            add_trace(
                state,
                "tool_ok",
                tool=tool_name,
                output=result.output,
                attempts=result.attempts,
                duration_ms=result.duration_ms,
                metrics=result.metrics,
                action_category=tool.action_category,
            )
        else:
            add_trace(
                state,
                "tool_error",
                tool=tool_name,
                error=result.error,
                attempts=result.attempts,
                duration_ms=result.duration_ms,
                metrics=result.metrics,
                action_category=tool.action_category,
            )
        self.store.save(state)
        return result

    def create_run(self, topic: str, source_documents: list[dict[str, Any]], run_mode: str = "single") -> RunState:
        state = RunState.new(topic=topic, source_documents=source_documents, run_mode=run_mode)
        state.status = "running"
        state.artifacts["policy"] = self.policy.describe()

        if run_mode == "multi_agent":
            plan, planner_packet, handoff = planner_step(topic=topic, source_documents=source_documents)
            state.plan = plan or [
                "search_mock",
                "extract_facts",
                "draft_report",
                "finalize_report",
            ]
            state.artifacts["planner"] = planner_packet["provider"]
            state.artifacts["planner_packet"] = planner_packet
            self._record_role_activity(
                state,
                role="planner",
                action="build_plan",
                payload={"provider": planner_packet["provider"], "step_count": planner_packet["step_count"]},
            )
            self._record_handoff(state, handoff)
            add_trace(state, "run_created", topic=topic, plan=state.plan, planner=planner_packet["provider"], mode="multi_agent")
        else:
            plan, planner = create_plan_from_env(topic=topic, source_documents=source_documents)
            state.plan = plan or [
                "search_mock",
                "extract_facts",
                "draft_report",
                "finalize_report",
            ]
            state.artifacts["planner"] = planner
            add_trace(state, "run_created", topic=topic, plan=state.plan, planner=planner, mode="single")

        self.store.save(state)
        return state

    def run_until_pause_or_complete(self, state: RunState) -> RunState:
        if state.status == "completed":
            return state
        if state.current_step == "init":
            state.current_step = "search_mock"

        is_multi_agent = state.run_mode == "multi_agent"
        if is_multi_agent and not state.artifacts.get("handoffs"):
            state.artifacts["handoffs"] = []
        if is_multi_agent and not state.artifacts.get("role_executions"):
            state.artifacts["role_executions"] = []

        while True:
            if state.current_step == "search_mock":
                if is_multi_agent:
                    self._record_role_activity(state, role="executor", action="search_mock", payload={"topic": state.topic})
                result = self._execute(state, "search_mock", topic=state.topic, source_documents=state.source_documents)
                if not result.ok:
                    state.status = "failed"
                    break
                state.artifacts["matches"] = result.output["matches"]
                state.current_step = "extract_facts"
                self.store.save(state)
                continue

            if state.current_step == "extract_facts":
                if is_multi_agent:
                    self._record_role_activity(state, role="executor", action="extract_facts", payload={"match_count": len(state.artifacts.get("matches", []))})
                result = self._execute(state, "extract_facts", matches=state.artifacts.get("matches", []))
                if not result.ok:
                    state.status = "failed"
                    break
                state.artifacts["facts"] = result.output["facts"]
                state.current_step = "draft_report"
                self.store.save(state)
                continue

            if state.current_step == "draft_report":
                if is_multi_agent:
                    self._record_role_activity(state, role="executor", action="draft_report", payload={"fact_count": len(state.artifacts.get("facts", []))})
                result = self._execute(state, "draft_report", topic=state.topic, facts=state.artifacts.get("facts", []))
                if not result.ok:
                    state.status = "failed"
                    break
                state.artifacts["draft_markdown"] = result.output["markdown"]

                review = review_from_env(topic=state.topic, markdown=state.artifacts["draft_markdown"])
                if is_multi_agent:
                    handoffs = reviewer_handoffs(
                        topic=state.topic,
                        markdown=state.artifacts["draft_markdown"],
                        facts=state.artifacts.get("facts", []),
                        review=review,
                    )
                    for handoff in handoffs:
                        self._record_handoff(state, handoff)
                    self._record_role_activity(
                        state,
                        role="reviewer",
                        action="review_draft",
                        payload={"passed": bool(review.get("passed", False)), "reviewer": review.get("reviewer")},
                    )

                state.artifacts["review"] = review
                add_trace(state, "draft_reviewed", review=review)
                if not review.get("passed", False):
                    state.status = "failed"
                    state.pending_action = None
                    state.requires_approval = False
                    self.store.save(state)
                    break
                output_path = str(Path(self.store.run_dir(state.run_id)) / "final_report.md")
                draft_lines = state.artifacts["draft_markdown"].splitlines()
                policy_decision = self.policy.evaluate("finalize_report", {"markdown": state.artifacts["draft_markdown"], "output_path": output_path})
                self._record_policy_decision(state, policy_decision)
                add_trace(
                    state,
                    "policy_checked",
                    tool="finalize_report",
                    action_category=policy_decision.action_category,
                    allowed=policy_decision.allowed,
                    reason=policy_decision.reason,
                    risky=True,
                    args={"output_path": output_path},
                )
                if not policy_decision.allowed:
                    state.status = "failed"
                    state.pending_action = None
                    state.requires_approval = False
                    add_trace(
                        state,
                        "policy_denied",
                        tool="finalize_report",
                        action_category=policy_decision.action_category,
                        reason=policy_decision.reason,
                    )
                    self.store.save(state)
                    break
                pending_details = {
                    "action": "finalize_report",
                    "tool_name": "finalize_report",
                    "tool_risky": True,
                    "action_category": "filesystem_write",
                    "requested_by_step": "draft_report",
                    "reason": "finalize_report writes the reviewed markdown report to disk and is treated as a risky action in this harness.",
                    "policy": policy_decision.to_dict(),
                    "proposed_output_path": output_path,
                    "draft_preview": {
                        "line_count": len(draft_lines),
                        "char_count": len(state.artifacts["draft_markdown"]),
                        "excerpt": draft_lines[:12],
                    },
                    "review": review,
                    "next_commands": [
                        f"PYTHONPATH=src python3 -m harness_engineering.cli pending {state.run_id}",
                        f"PYTHONPATH=src python3 -m harness_engineering.cli approve {state.run_id}",
                        f"PYTHONPATH=src python3 -m harness_engineering.cli resume {state.run_id}",
                    ],
                }
                state.artifacts["pending_action_details"] = pending_details
                state.current_step = "finalize_report"
                state.requires_approval = True
                state.pending_action = "finalize_report"
                state.status = "waiting_approval"
                add_trace(state, "approval_required", action="finalize_report", pending_action=pending_details)
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
                if is_multi_agent:
                    self._record_role_activity(state, role="executor", action="finalize_report", payload={"approved": True})
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
        pending_details = state.artifacts.get("pending_action_details")
        if isinstance(pending_details, dict):
            pending_details["approved_at"] = state.updated_at
            state.artifacts["pending_action_details"] = pending_details
        add_trace(state, "approval_granted", action=state.pending_action)
        self.store.save(state)
        return state

    def resume(self, run_id: str) -> RunState:
        state = self.store.load(run_id)
        add_trace(state, "run_resumed", current_step=state.current_step, status=state.status)
        self.store.save(state)
        return self.run_until_pause_or_complete(state)
