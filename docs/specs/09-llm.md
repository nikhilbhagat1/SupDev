# 09 LLM providers

**Purpose** Swap models/providers behind `LLMProvider.complete(system, messages, tools) -> LLMResponse`.

**Design** `AnthropicLLM` (`llm/anthropic.py`): platform-held key (`ANTHROPIC_API_KEY`), model from `SUPDEV_ANTHROPIC_MODEL` (default `claude-sonnet-5`), lazy client; converts our messages (tool results grouped into one user turn, `system_note`→`[platform note]` user text) and maps tool names `.`↔`__`. `FakeLLM` (`llm/fake.py`): scripted `LLMResponse`s or callables; records what it saw.
**Acceptance** Everything else is tested through `FakeLLM`.
**Test gaps** **`AnthropicLLM` has no tests and has never run against the live API**: message conversion (`to_anthropic_messages`), tool-name mapping, empty-assistant turns, error handling/retries, streaming. Add a conversion unit test and a manual smoke test first.
**Out of scope** Streaming tokens to the UI; prompt caching; per-tenant model selection (only env-level today); other providers.
