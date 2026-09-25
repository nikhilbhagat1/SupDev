"""Events streamed to the UI (SSE) and captured by tests."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

from .models import now


class Event(BaseModel):
    type: str  # assistant | tool_call | tool_result | audit_notice | flag | approval_request |
    #            approval_granted | phase | mode | budget_paused | stuck | error | done
    data: dict[str, Any] = Field(default_factory=dict)
    ts: float = Field(default_factory=now)


class EventSink(Protocol):
    def __call__(self, event: Event) -> None: ...


class ListSink:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def __call__(self, event: Event) -> None:
        self.events.append(event)

    def of(self, type_: str) -> list[Event]:
        return [e for e in self.events if e.type == type_]
