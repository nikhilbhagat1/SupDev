# CLAUDE.md — working on Supdev

Unified engineering agent: one runtime, two modes (DEV, SUPPORT/RCA), everything pluggable. The behaviour spec is
`docs/source_system_prompt.md` (split into `src/supdev/prompts/`). Per-feature specs: `docs/specs/` (11 = admin/settings, 12 = Docker).

## Commands
```bash
uv sync
uv run pytest -q                 # all tests (scripted fake LLM, no network)
uv run pytest tests/security -q  # the trust story — run after ANY change to core/, gateway/, modes/
uv run ruff check . && uv run mypy   # mypy is strict on core/, gateway/, plugins/
uv run supdev plugins            # list discovered plugins
uv run supdev serve --port 8000  # API + web UI (demo tenant)
```

## Architecture in five lines
`api/` → `core/runtime.py` (turn loop) → `LLMProvider` plugin; every tool call goes through `gateway/tool_gateway.py`;
modes (`modes/dev`, `modes/support`) own phases, exit checks, guardrails and engine-native tools (`engine.*`);
adapters (`adapters/`) implement vendor-neutral contracts from `capabilities/`; plugins are discovered via entry points
(`plugins/registry.py`, groups in `plugins/base.py:PluginKind`, built-ins registered in `pyproject.toml`).

## Invariants — do not weaken (each has a test in tests/security)
1. **The model never grants approvals.** Only `ApprovalService.grant` / `grant_from_text` (human, role-checked). Tool output and model text can't advance gates.
2. **Approvals are bound to the hash of the artifact currently on record** (`wi.artifacts[kind]`); changing it voids them. `artifact_arg` approvals are one-shot.
3. **Prod (`environment` in prod/production/prd) is read-only in every mode**; checked in the gateway *before* approvals.
4. **Tenant policy can only tighten** (`Policy.merge`). Never add a code path that loosens it.
5. **Write tools must declare `approval_kind`**; the gateway refuses ones that don't. Unknown/untagged tools don't exist for the model.
6. **Evidence/test/RCA claims reference real tool-call ids** from `wi.call_log`; the engine copies source/query/range.
7. **Secrets never reach prompt, audit, SSE or tool args**: everything passes the `Redactor`; credentials stay in `SecretStore`.
8. **Tenant scoping**: stores are keyed by tenant; wrong tenant == "not found".
9. Gates are enforced in code, not by the prompt. If you add a rule to the prompt, add the code path and a test.

10. **Tenant admins can't widen the platform**: code-execution/SSRF-relevant features are gated by operator env flags (`core/flags.py`), URLs go through `core/netguard.check_url`, MCP tags are validated in `settings/validate.py` (prod ⇒ read, writes ⇒ `approval_kind`). Secrets are write-only (`secrets/db.py`); never return a value from an API or put one in a config doc/audit record.

## Conventions
- Python ≥3.12, type hints, pydantic v2 models for anything persisted. Line length 120 (E501 ignored).
- Tool names: `<capability>.<op>` (+ `.<env>` for env-scoped), engine tools `engine.*`. Anthropic wire format maps `.`→`__`
  (`llm/anthropic.py`), so never put `__` in a tool name.
- New integration: build ToolSpecs with `capabilities.*` builders, return JSON-able data, raise on error (gateway converts to a tool
  error and counts repeats). Execution tools return `{"passed": bool}`.
- New mode/phase: subclass `modes/base.ModeBase`; exit checks must read *state*, never trust model claims.
- Prompts are files in `src/supdev/prompts/`; variables use `{{NAME}}` and are filled in `core/prompt.py` / `runtime._vars`.

## Testing approach
`tests/conftest.py::Env` wires a runtime with `FakeLLM` (scripted `LLMResponse`s, one per LLM call), fake adapters and an in-memory
store. Use `call()/say()` from `supdev.llm.fake`, or build `ToolCall(id=...)` when a later step must reference a call id.
Support tests need prod read access (`envs=("dev","staging","prod")`) — otherwise prod tools are (correctly) not offered.

## Gotchas
- Tenant config lives in the DB (`/settings`); `SUPDEV_CONFIG` only seeds. The runtime reloads a tenant when its `version` changes.
- The `mcp` SDK 2.x renamed several APIs (`streamable_http_client`, `is_error`, `input_schema`); `adapters/mcp_bridge.py` supports both 1.x and 2.x — keep the real-server test (`test_mcp_bridge_end_to_end…`) green.
- macOS: `sed -i` needs `''`; use the Edit tool or python for file edits in scripts.
- After changing entry points in `pyproject.toml`, run `uv sync` so they register.
- `local_exec` runs repo code on the host; `dev-header` auth is local-only.
