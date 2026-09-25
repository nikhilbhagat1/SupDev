"""Scripted LLM for tests and offline demos. Each step is an LLMResponse or a callable
`(system, messages, tools) -> LLMResponse`, consumed in order."""
from __future__ import annotations

from collections.abc import Callable

from ..core.models import LLMResponse, Message, ToolCall, ToolSpec, Usage

Step = LLMResponse | Callable[[str, list[Message], list[ToolSpec]], LLMResponse]


def say(text: str) -> LLMResponse:
    return LLMResponse(text=text, usage=Usage(input_tokens=10, output_tokens=10))


def call(name: str, text: str = "", **args: object) -> LLMResponse:
    return LLMResponse(text=text, tool_calls=[ToolCall(name=name, args=dict(args))],
                       usage=Usage(input_tokens=10, output_tokens=10))


class FakeLLM:
    name = "fake"
    api_key: str | None = None
    model: str | None = None

    def __init__(self, script: list[Step] | None = None) -> None:
        self.script: list[Step] = list(script or [])
        self.seen: list[tuple[str, list[Message], list[str]]] = []  # for assertions

    def with_config(self, api_key: str | None = None, model: str | None = None) -> FakeLLM:
        clone = FakeLLM(self.script)  # shares the script list; records the per-tenant credentials for tests
        clone.script = self.script
        clone.api_key, clone.model = api_key, model
        return clone

    async def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        self.seen.append((system, list(messages), [t.name for t in tools]))
        if not self.script:
            return say("(fake llm: script exhausted)")
        step = self.script.pop(0)
        return step(system, messages, tools) if callable(step) else step
