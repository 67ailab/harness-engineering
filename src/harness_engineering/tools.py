from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
import json
import random


class ToolError(RuntimeError):
    pass


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, str]
    risky: bool
    handler: Callable[..., dict[str, Any]]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        return self._tools[name]

    def list(self) -> list[Tool]:
        return list(self._tools.values())


def search_mock(topic: str, source_documents: list[dict[str, Any]]) -> dict[str, Any]:
    matches = []
    words = {w.strip(".,:;!?()[]{}\"'").lower() for w in topic.split() if w.strip()}
    for doc in source_documents:
        text = doc.get("content", "")
        lowered = text.lower()
        score = sum(1 for word in words if word and word in lowered)
        if score > 0:
            matches.append({"title": doc.get("title", "untitled"), "content": text, "score": score})
    matches.sort(key=lambda item: item["score"], reverse=True)
    return {"matches": matches[:5]}


def extract_facts(matches: list[dict[str, Any]]) -> dict[str, Any]:
    facts: list[str] = []
    for match in matches:
        title = match["title"]
        content = match["content"]
        sentences = [segment.strip() for segment in content.replace("\n", " ").split(".") if segment.strip()]
        for sentence in sentences[:3]:
            facts.append(f"[{title}] {sentence}.")
    return {"facts": facts[:10]}


def draft_report(topic: str, facts: list[str]) -> dict[str, Any]:
    lines = [f"# Report: {topic}", "", "## Key Findings", ""]
    if not facts:
        lines.append("- No facts were extracted from the provided sources.")
    else:
        lines.extend([f"- {fact}" for fact in facts])
    lines.extend(["", "## Harness Notes", "", "- This report was generated via a checkpointed, approval-gated local harness demo."])
    return {"markdown": "\n".join(lines)}


def finalize_report(markdown: str, output_path: str) -> dict[str, Any]:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    return {"path": str(path), "bytes": path.stat().st_size}


def flaky_echo(message: str, fail_once: bool = False, state_file: str | None = None) -> dict[str, Any]:
    if fail_once and state_file:
        flag = Path(state_file)
        if not flag.exists():
            flag.write_text("failed-once", encoding="utf-8")
            raise ToolError("Simulated transient failure")
    return {"message": message, "random_sample": random.randint(1, 9)}


def default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(Tool("search_mock", "Search mock documents for topic-relevant text.", {"topic": "str", "source_documents": "list"}, False, search_mock))
    registry.register(Tool("extract_facts", "Extract concise facts from matched documents.", {"matches": "list"}, False, extract_facts))
    registry.register(Tool("draft_report", "Draft a markdown report from extracted facts.", {"topic": "str", "facts": "list[str]"}, False, draft_report))
    registry.register(Tool("finalize_report", "Write the final markdown report to disk.", {"markdown": "str", "output_path": "str"}, True, finalize_report))
    registry.register(Tool("flaky_echo", "A test tool that can fail once and succeed on retry.", {"message": "str"}, False, flaky_echo))
    return registry


def load_source_documents(path: str) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Source file must contain a list of documents")
    return data
