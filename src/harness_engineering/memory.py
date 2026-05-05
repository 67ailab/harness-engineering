from __future__ import annotations

from collections import Counter
from typing import Any

from .models import RunState


MEMORY_LAYER_NAMES = {
    "working_context": "short-lived execution context needed for the next step",
    "session_state": "durable per-run state needed for pause/resume and operator inspection",
    "retrieval_memory": "relevant facts and source snippets fetched on demand instead of stuffed into working context",
}


def _compact_text(value: str, limit: int = 220) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _matches_query(text: str, query_words: set[str]) -> int:
    lowered = text.lower()
    return sum(1 for word in query_words if word and word in lowered)


def build_working_context(state: RunState) -> dict[str, Any]:
    facts = list(state.artifacts.get("facts", []))
    draft = state.artifacts.get("draft_markdown", "")
    review = state.artifacts.get("review", {})
    return {
        "description": MEMORY_LAYER_NAMES["working_context"],
        "topic": state.topic,
        "current_step": state.current_step,
        "status": state.status,
        "requires_approval": state.requires_approval,
        "pending_action": state.pending_action,
        "plan_outline": list(state.plan),
        "fact_count": len(facts),
        "facts_preview": facts[:3],
        "draft_preview": _compact_text(draft) if draft else None,
        "review_passed": review.get("passed"),
        "review_findings_preview": list(review.get("findings", []))[:3],
    }


def build_session_state(state: RunState) -> dict[str, Any]:
    trace_counts = Counter(event.event for event in state.trace)
    last_error = next((result.error for result in reversed(state.step_results) if result.error), None)
    return {
        "description": MEMORY_LAYER_NAMES["session_state"],
        "run_id": state.run_id,
        "topic": state.topic,
        "status": state.status,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
        "current_step": state.current_step,
        "approved": state.approved,
        "requires_approval": state.requires_approval,
        "pending_action": state.pending_action,
        "plan": list(state.plan),
        "artifact_keys": sorted(state.artifacts.keys()),
        "step_results": [
            {
                "tool_name": result.tool_name,
                "ok": result.ok,
                "attempts": result.attempts,
                "error": result.error,
            }
            for result in state.step_results
        ],
        "trace_event_counts": dict(trace_counts),
        "last_error": last_error,
    }


def retrieve_memory(state: RunState, query: str | None = None, top_k: int = 5) -> dict[str, Any]:
    raw_query = (query or state.topic).strip()
    query_words = {word.strip(".,:;!?()[]{}\"'").lower() for word in raw_query.split() if word.strip()}

    entries: list[dict[str, Any]] = []
    for index, doc in enumerate(state.source_documents):
        title = doc.get("title", f"source-{index + 1}")
        content = doc.get("content", "")
        score = _matches_query(f"{title} {content}", query_words)
        if score > 0:
            entries.append(
                {
                    "kind": "source_document",
                    "title": title,
                    "score": score,
                    "content": _compact_text(content, limit=280),
                }
            )

    for fact in state.artifacts.get("facts", []):
        score = _matches_query(fact, query_words)
        if score > 0:
            entries.append(
                {
                    "kind": "extracted_fact",
                    "title": "fact",
                    "score": score,
                    "content": _compact_text(fact, limit=220),
                }
            )

    entries.sort(key=lambda item: (item["score"], item["kind"] == "extracted_fact"), reverse=True)
    return {
        "description": MEMORY_LAYER_NAMES["retrieval_memory"],
        "query": raw_query,
        "top_k": top_k,
        "results": entries[:top_k],
    }


def build_memory_snapshot(state: RunState, query: str | None = None, top_k: int = 5) -> dict[str, Any]:
    return {
        "run_id": state.run_id,
        "layers": {
            "working_context": build_working_context(state),
            "session_state": build_session_state(state),
            "retrieval_memory": retrieve_memory(state, query=query, top_k=top_k),
        },
    }
