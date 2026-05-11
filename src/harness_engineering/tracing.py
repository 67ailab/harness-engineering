from __future__ import annotations

from collections import Counter
from typing import Any

from .models import TraceEvent, now_iso


def add_trace(state, event: str, **detail: Any) -> None:
    state.trace.append(TraceEvent(timestamp=now_iso(), event=event, detail=detail))
    state.updated_at = now_iso()


def build_trace_summary(state) -> dict[str, Any]:
    event_counts = Counter(item.event for item in state.trace)
    tool_counts = Counter(result.tool_name for result in state.step_results)
    attempt_counts = Counter()
    action_category_counts = Counter()
    duration_by_tool = Counter()
    metrics_by_tool: dict[str, dict[str, Any]] = {}
    policy_checked = 0
    policy_denied = 0
    for result in state.step_results:
        attempt_counts[result.tool_name] += result.attempts
        duration_by_tool[result.tool_name] += result.duration_ms or 0
    for item in state.trace:
        detail = item.detail or {}
        category = detail.get("action_category")
        if category:
            action_category_counts[category] += 1
        if item.event == "policy_checked":
            policy_checked += 1
        if item.event == "policy_denied":
            policy_denied += 1

    role_activity_counts = Counter()
    handoff_pairs = Counter()
    for item in state.trace:
        detail = item.detail or {}
        if item.event == "role_activity":
            role = detail.get("role")
            if role:
                role_activity_counts[role] += 1
        if item.event == "role_handoff":
            pair = f"{detail.get('from_role', '?')}->{detail.get('to_role', '?')}"
            handoff_pairs[pair] += 1

    for tool_name, durations in duration_by_tool.items():
        matching = [result for result in state.step_results if result.tool_name == tool_name]
        total_estimated_tokens = sum((result.metrics or {}).get("estimated_total_tokens", 0) for result in matching)
        total_bytes_written = sum(((result.metrics or {}).get("bytes_written") or 0) for result in matching)
        model_providers = sorted({(result.metrics or {}).get("provider") for result in matching if (result.metrics or {}).get("provider")})
        metrics_by_tool[tool_name] = {
            "count": len(matching),
            "total_duration_ms": durations,
            "average_duration_ms": int(durations / len(matching)) if matching else 0,
            "total_estimated_tokens": total_estimated_tokens,
            "total_bytes_written": total_bytes_written,
            "providers": model_providers,
        }

    latest_event = state.trace[-1].event if state.trace else None
    first_event_at = state.trace[0].timestamp if state.trace else None
    last_event_at = state.trace[-1].timestamp if state.trace else None
    failures = [
        {
            "tool_name": result.tool_name,
            "attempts": result.attempts,
            "error": result.error,
        }
        for result in state.step_results
        if not result.ok
    ]
    policy_decisions = state.artifacts.get("policy_decisions", [])
    latest_policy_decision = policy_decisions[-1] if policy_decisions else None

    total_duration_ms = sum(result.duration_ms or 0 for result in state.step_results)
    model_steps = [result for result in state.step_results if result.tool_name == "draft_report"]
    total_estimated_tokens = sum((result.metrics or {}).get("estimated_total_tokens", 0) for result in model_steps)
    total_estimated_input_tokens = sum((result.metrics or {}).get("estimated_input_tokens", 0) for result in model_steps)
    total_estimated_output_tokens = sum((result.metrics or {}).get("estimated_output_tokens", 0) for result in model_steps)
    cost_statuses = sorted({((result.metrics or {}).get("cost_estimate") or {}).get("status") for result in model_steps if ((result.metrics or {}).get("cost_estimate") or {}).get("status")})

    return {
        "run_id": state.run_id,
        "status": state.status,
        "current_step": state.current_step,
        "counts": {
            "trace_events": len(state.trace),
            "steps": len(state.step_results),
            "by_event": dict(event_counts),
            "by_tool": dict(tool_counts),
            "attempts_by_tool": dict(attempt_counts),
            "by_action_category": dict(action_category_counts),
        },
        "performance": {
            "total_duration_ms": total_duration_ms,
            "by_tool": metrics_by_tool,
        },
        "cost": {
            "model_step_count": len(model_steps),
            "estimated_input_tokens": total_estimated_input_tokens,
            "estimated_output_tokens": total_estimated_output_tokens,
            "estimated_total_tokens": total_estimated_tokens,
            "status": cost_statuses[0] if len(cost_statuses) == 1 else ("mixed" if cost_statuses else "not_applicable"),
            "note": "Token counts are coarse estimates derived from character counts. The demo does not claim provider-accurate billing.",
        },
        "timeline": {
            "first_event_at": first_event_at,
            "last_event_at": last_event_at,
            "latest_event": latest_event,
        },
        "approval": {
            "required": state.requires_approval,
            "approved": state.approved,
            "pending_action": state.pending_action,
            "approval_events": event_counts.get("approval_required", 0) + event_counts.get("approval_still_required", 0),
            "granted_events": event_counts.get("approval_granted", 0),
        },
        "multi_agent": {
            "enabled": getattr(state, "run_mode", "single") == "multi_agent",
            "handoff_count": event_counts.get("role_handoff", 0),
            "role_activity_by_role": dict(role_activity_counts),
            "handoff_pairs": dict(handoff_pairs),
            "current_role": state.artifacts.get("current_role"),
        },
        "policy": {
            "configured": bool(state.artifacts.get("policy")),
            "checks": policy_checked,
            "denials": policy_denied,
            "latest_decision": latest_policy_decision,
        },
        "review": state.artifacts.get("review", {}),
        "artifacts": {
            "draft_present": "draft_markdown" in state.artifacts,
            "final_report_path": state.artifacts.get("final_report", {}).get("path"),
        },
        "failures": failures,
    }
