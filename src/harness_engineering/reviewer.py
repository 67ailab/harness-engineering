from __future__ import annotations

from typing import Any

from .provider import OpenAICompatibleClient, create_client_from_env


def build_plan(topic: str, source_documents: list[dict[str, Any]], client: OpenAICompatibleClient | None = None) -> list[str]:
    if client is None:
        return [
            f"Search source documents for topic: {topic}",
            "Extract concise facts from relevant matches",
            "Draft a markdown report from the facts",
            "Require human approval before writing the final report to disk",
        ]

    sources = "\n".join(f"- {doc.get('title', 'untitled')}: {doc.get('content', '')[:180]}" for doc in source_documents[:5])
    response = client.chat(
        system_prompt="You are a careful planning assistant. Return plain text bullets only.",
        user_prompt=(
            f"Create a 4-step plan for a small approval-gated research harness about: {topic}.\n"
            "Keep it concrete, short, and aligned to this workflow: search, extract, draft, approval/finalize.\n"
            f"Source context:\n{sources}"
        ),
        temperature=0.1,
    )
    bullets = []
    for line in response.splitlines():
        cleaned = line.strip().lstrip("-*0123456789. ").strip()
        if cleaned:
            bullets.append(cleaned)
    return bullets[:4] or [
        f"Search source documents for topic: {topic}",
        "Extract concise facts from relevant matches",
        "Draft a markdown report from the facts",
        "Require human approval before writing the final report to disk",
    ]


def review_markdown(topic: str, markdown: str, client: OpenAICompatibleClient | None = None) -> dict[str, Any]:
    if client is None:
        findings = []
        if "## Key Findings" not in markdown:
            findings.append("Missing '## Key Findings' section.")
        if "## Harness Notes" not in markdown:
            findings.append("Missing '## Harness Notes' section.")
        return {"reviewer": "mock", "passed": not findings, "findings": findings}

    response = client.chat(
        system_prompt="You are a strict markdown reviewer. Return JSON only.",
        user_prompt=(
            "Review this markdown for a harness-engineering demo report. "
            "Check: title present, Key Findings section present, Harness Notes section present, no obvious invented claims beyond supplied content. "
            "Return JSON with keys: passed (boolean), findings (array of strings).\n\n"
            f"Topic: {topic}\n\nMarkdown:\n{markdown}"
        ),
        temperature=0.0,
    )
    import json
    try:
        parsed = json.loads(response)
        return {
            "reviewer": "openai_compatible",
            "passed": bool(parsed.get("passed", False)),
            "findings": list(parsed.get("findings", [])),
        }
    except Exception:
        return {
            "reviewer": "openai_compatible",
            "passed": False,
            "findings": ["Reviewer returned non-JSON output", response[:400]],
        }


def create_plan_from_env(topic: str, source_documents: list[dict[str, Any]]) -> tuple[list[str], str]:
    client = create_client_from_env()
    plan = build_plan(topic=topic, source_documents=source_documents, client=client)
    return plan, ("openai_compatible" if client is not None else "mock")


def review_from_env(topic: str, markdown: str) -> dict[str, Any]:
    client = create_client_from_env()
    result = review_markdown(topic=topic, markdown=markdown, client=client)
    return result
