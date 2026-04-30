from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
import uuid


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass
class TraceEvent:
    timestamp: str
    event: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepResult:
    tool_name: str
    ok: bool
    output: dict[str, Any]
    attempts: int = 1
    error: str | None = None


@dataclass
class RunState:
    run_id: str
    topic: str
    status: str
    created_at: str
    updated_at: str
    current_step: str
    requires_approval: bool = False
    approved: bool = False
    pending_action: str | None = None
    plan: list[str] = field(default_factory=list)
    source_documents: list[dict[str, Any]] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    trace: list[TraceEvent] = field(default_factory=list)
    step_results: list[StepResult] = field(default_factory=list)

    @classmethod
    def new(cls, topic: str, source_documents: list[dict[str, Any]]) -> "RunState":
        timestamp = now_iso()
        return cls(
            run_id=str(uuid.uuid4()),
            topic=topic,
            status="created",
            created_at=timestamp,
            updated_at=timestamp,
            current_step="init",
            source_documents=source_documents,
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["trace"] = [asdict(item) for item in self.trace]
        data["step_results"] = [asdict(item) for item in self.step_results]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunState":
        trace = [TraceEvent(**item) for item in data.get("trace", [])]
        step_results = [StepResult(**item) for item in data.get("step_results", [])]
        copied = dict(data)
        copied["trace"] = trace
        copied["step_results"] = step_results
        return cls(**copied)
