# 09 LLM providers

**Purpose** Swap models/providers behind `LLMProvider.complete(system, messages, tools) -> LLMResponse`.

**Design** `AnthropicLLM` (`llm/anthropic.py`): key from `ANTHROPIC_API_KEY` (platform default) **or per tenant** (`with_config(api_key, model)`, key from the encrypted secret `llm_api_key`, chosen in Settings → LLM); model from the tenant, else `SUPDEV_ANTHROPIC_MODEL` (default `claude-sonnet-5`); lazy client; converts our messages (tool results grouped into one user turn, `system_note`→`[platform note]` user text) and maps tool names `.`↔`__`. `FakeLLM` (`llm/fake.py`): scripted `LLMResponse`s or callables; records what it saw; `with_config` records the per-tenant key for tests. `AgentRuntime.llm_for(tenant)` picks the provider; a tenant that chose a provider without a key gets an error, never the platform's key.
**Acceptance** Per-tenant selection is tested (`test_per_tenant_llm_key_is_used_and_never_falls_back_silently`); everything else runs through `FakeLLM`.
**Verification status** `AnthropicLLM` has been called against the live API once (the request was accepted as far as billing: the account returned "credit balance too low"), so **request validity and response parsing are still unverified**.
**Test gaps** **`AnthropicLLM` has no automated tests**: message conversion (`to_anthropic_messages`), tool-name mapping, empty-assistant turns, error handling/retries, streaming. Add a conversion unit test and a manual smoke test with a funded key first. A model name that isn't an Anthropic model (e.g. typed by mistake in Settings) is not validated.
**Out of scope** Streaming tokens to the UI; prompt caching; other providers (add via the `supdev.llm` entry point).
