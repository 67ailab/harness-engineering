from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from .runner import HarnessRunner
from .store import RunStore
from .tools import default_registry, load_source_documents
from .tracing import build_trace_summary


def load_eval_fixtures(path: str | Path) -> list[dict[str, Any]]:
    fixture_path = Path(path)
    with fixture_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Eval fixture file must contain a list of cases")
    return data


def _normalize_source_path(base_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def run_eval_case(
    fixture: dict[str, Any],
    *,
    fixtures_path: str | Path,
    runs_dir: str | Path = ".runs",
) -> dict[str, Any]:
    fixture_name = fixture.get("name") or fixture.get("topic") or "unnamed-eval"
    fixtures_dir = Path(fixtures_path).resolve().parent
    topic = fixture["topic"]
    source_file = _normalize_source_path(fixtures_dir, fixture["source_file"])
    source_documents = load_source_documents(str(source_file))

    store = RunStore(runs_dir)
    runner = HarnessRunner(store=store, registry=default_registry())
    state = runner.create_run(topic=topic, source_documents=source_documents)
    state = runner.run_until_pause_or_complete(state)

    if fixture.get("auto_approve") and state.status == "waiting_approval":
        runner.approve(state.run_id)
        state = runner.resume(state.run_id)

    trace_summary = build_trace_summary(state)
    checks: list[dict[str, Any]] = []

    def check(name: str, condition: bool, *, expected: Any = None, actual: Any = None) -> None:
        checks.append({
            "name": name,
            "passed": bool(condition),
            "expected": expected,
            "actual": actual,
        })

    expected_status = fixture.get("expected_status")
    if expected_status is not None:
        check("status", state.status == expected_status, expected=expected_status, actual=state.status)

    expected_current_step = fixture.get("expected_current_step")
    if expected_current_step is not None:
        check("current_step", state.current_step == expected_current_step, expected=expected_current_step, actual=state.current_step)

    required_events = fixture.get("required_events", [])
    observed_events = set(trace_summary["counts"]["by_event"].keys())
    for event_name in required_events:
        check(
            f"event:{event_name}",
            event_name in observed_events,
            expected=True,
            actual=(event_name in observed_events),
        )

    min_trace_events = fixture.get("min_trace_events")
    if min_trace_events is not None:
        check(
            "min_trace_events",
            trace_summary["counts"]["trace_events"] >= int(min_trace_events),
            expected=f">={int(min_trace_events)}",
            actual=trace_summary["counts"]["trace_events"],
        )

    expect_final_report = fixture.get("expect_final_report")
    final_report = state.artifacts.get("final_report", {})
    final_report_path = final_report.get("path")
    if expect_final_report is not None:
        exists = bool(final_report_path and Path(final_report_path).exists())
        check("final_report_exists", exists == bool(expect_final_report), expected=bool(expect_final_report), actual=exists)

    passed = all(item["passed"] for item in checks) if checks else True
    return {
        "name": fixture_name,
        "passed": passed,
        "run_id": state.run_id,
        "topic": state.topic,
        "status": state.status,
        "current_step": state.current_step,
        "checks": checks,
        "trace_summary": trace_summary,
    }


def run_eval_suite(fixtures_path: str | Path, runs_dir: str | Path = ".runs") -> dict[str, Any]:
    fixtures = load_eval_fixtures(fixtures_path)
    results = [run_eval_case(fixture, fixtures_path=fixtures_path, runs_dir=runs_dir) for fixture in fixtures]
    passed = sum(1 for result in results if result["passed"])
    failed = len(results) - passed
    return {
        "fixtures_path": str(Path(fixtures_path).resolve()),
        "runs_dir": str(Path(runs_dir).resolve()),
        "case_count": len(results),
        "passed": passed,
        "failed": failed,
        "results": results,
    }
