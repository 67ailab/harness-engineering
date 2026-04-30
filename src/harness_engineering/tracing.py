from __future__ import annotations

from typing import Any

from .models import TraceEvent, now_iso


def add_trace(state, event: str, **detail: Any) -> None:
    state.trace.append(TraceEvent(timestamp=now_iso(), event=event, detail=detail))
    state.updated_at = now_iso()
