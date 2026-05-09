from __future__ import annotations

from typing import Any

from .models import now_iso
from .reviewer import create_plan_from_env


ROLE_DESCRIPTIONS = {
    "planner": "Defines the next workflow steps and hands a constrained plan to the executor.",
    "executor": "Runs the harness tools and manages state, approvals, and policy checks.",
    "reviewer": "Checks the draft output and returns a pass/fail decision with findings.",
}


def _compact_text(value: str, limit: int = 220) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _doc_titles(source_documents: list[dict[str, Any]], limit: int = 5) -> list[str]:
    return [doc.get("title", "untitled") for doc in source_documents[:limit]]


def build_handoff(*, from_role: str, to_role: str, purpose: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": now_iso(),
        "from_role": from_role,
        "to_role": to_role,
        "purpose": purpose,
        "payload": payload,
    }


def planner_step(topic: str, source_documents: list[dict[str, Any]]) -> tuple[list[str], dict[str, Any], dict[str, Any]]:
    plan, provider = create_plan_from_env(topic=topic, source_documents=source_documents)
    planner_packet = {
        "role": "planner",
        "provider": provider,
        "goal": f"Create a small workflow for topic: {topic}",
        "source_document_titles": _doc_titles(source_documents),
        "step_count": len(plan),
        "plan": list(plan),
    }
    handoff = build_handoff(
        from_role="planner",
        to_role="executor",
        purpose="Hand the approved workflow outline to the runtime executor.",
        payload={
            "topic": topic,
            "step_count": len(plan),
            "plan": list(plan),
            "source_document_titles": planner_packet["source_document_titles"],
        },
    )
    return plan, planner_packet, handoff


def reviewer_handoffs(topic: str, markdown: str, facts: list[str], review: dict[str, Any]) -> list[dict[str, Any]]:
    inbound = build_handoff(
        from_role="executor",
        to_role="reviewer",
        purpose="Ask the reviewer to check the draft before the risky write step.",
        payload={
            "topic": topic,
            "fact_count": len(facts),
            "draft_preview": _compact_text(markdown, limit=260),
        },
    )
    outbound = build_handoff(
        from_role="reviewer",
        to_role="executor",
        purpose="Return the review decision so the executor can either stop or request approval.",
        payload={
            "topic": topic,
            "passed": bool(review.get("passed", False)),
            "findings": list(review.get("findings", [])),
            "reviewer": review.get("reviewer"),
        },
    )
    return [inbound, outbound]


def build_multi_agent_snapshot(handoffs: list[dict[str, Any]]) -> dict[str, Any]:
    roles = sorted({item.get("from_role") for item in handoffs} | {item.get("to_role") for item in handoffs})
    return {
        "mode": "multi_agent",
        "roles": [
            {
                "name": role,
                "description": ROLE_DESCRIPTIONS.get(role, ""),
            }
            for role in roles
            if role
        ],
        "handoff_count": len(handoffs),
        "handoffs": handoffs,
    }
