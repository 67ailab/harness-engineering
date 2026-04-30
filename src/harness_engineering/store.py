from __future__ import annotations

import json
from pathlib import Path

from .models import RunState


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

    def save(self, state: RunState) -> None:
        state.updated_at = state.updated_at
        path = self.state_path(state.run_id)
        with path.open("w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
        with self.trace_path(state.run_id).open("w", encoding="utf-8") as f:
            json.dump([event.__dict__ for event in state.trace], f, ensure_ascii=False, indent=2)

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
