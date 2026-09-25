"""Anthropic provider. The API key is platform-held (A4): read from the SecretStore/env by the
platform and never exposed to the model, logs or prompts."""
from __future__ import annotations

import os
from typing import Any

from ..core.errors import PluginError
from ..core.models import LLMResponse, Message, ToolCall, ToolSpec, Usage

DEFAULT_MODEL = "claude-sonnet-5"  # override per tenant/deployment via SUPDEV_ANTHROPIC_MODEL


def _wire(name: str) -> str:  # Anthropic tool names may not contain '.'
    return name.replace(".", "__")


def _unwire(name: str) -> str:
    return name.replace("__", ".")


def to_anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def push(role: str, blocks: list[dict[str, Any]]) -> None:
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})

    for m in messages:
        if m.role == "user":
            push("user", [{"type": "text", "text": m.text}])
        elif m.role == "system_note":
            push("user", [{"type": "text", "text": f"[platform note] {m.text}"}])
        elif m.role == "assistant":
            blocks: list[dict[str, Any]] = []
            if m.text:
                blocks.append({"type": "text", "text": m.text})
            for tc in m.tool_calls:
                blocks.append({"type": "tool_use", "id": tc.id, "name": _wire(tc.name), "input": tc.args})
            if blocks:
                push("assistant", blocks)
        elif m.role == "tool":
            push("user", [{"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.text}])
    return out


class AnthropicLLM:
    name = "anthropic"

    def __init__(self, api_key: str | None = None, model: str | None = None, max_tokens: int = 4096):
        self._api_key = api_key
        self.model = model or os.environ.get("SUPDEV_ANTHROPIC_MODEL", DEFAULT_MODEL)
        self.max_tokens = max_tokens
        self._client: Any = None

    def with_config(self, api_key: str | None = None, model: str | None = None) -> AnthropicLLM:
        return AnthropicLLM(api_key=api_key, model=model or self.model, max_tokens=self.max_tokens)

    def _c(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover
                raise PluginError("anthropic SDK not installed") from exc
            key = self._api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise PluginError("ANTHROPIC_API_KEY is not configured on the platform")
            self._client = anthropic.AsyncAnthropic(api_key=key)
        return self._client

    async def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": to_anthropic_messages(messages),
        }
        if tools:
            kwargs["tools"] = [
                {"name": _wire(t.name), "description": f"{t.description}\n({t.tag()})",
                 "input_schema": t.input_schema}
                for t in tools
            ]
        resp = await self._c().messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [ToolCall(id=b.id, name=_unwire(b.name), args=dict(b.input))
                 for b in resp.content if b.type == "tool_use"]
        return LLMResponse(text=text, tool_calls=calls,
                           usage=Usage(input_tokens=resp.usage.input_tokens,
                                       output_tokens=resp.usage.output_tokens))
