# Supdev — unified engineering agent

One app, one agent, two modes, built from [`docs/source_system_prompt.md`](docs/source_system_prompt.md):

* **DEV** — ticket/story/design → clarify → plan → *approved* code → tests → PR (7 phases)
* **SUPPORT** — production issue → triage → evidence → RCA → *approved* outputs (7 phases, prod is read-only)

The prompt states the rules; **the engine enforces them** (phase gates, approvals bound to artifact hashes, prod
read-only, redaction, tenant isolation, budgets, audit). The model proposes, the engine disposes.

## Quick start

```bash
uv sync
uv run pytest                      # 50+ tests incl. the security suite
uv run supdev plugins              # what got discovered
ANTHROPIC_API_KEY=... uv run supdev serve   # http://127.0.0.1:8000  (demo tenant, fake integrations)
```

Local dev auth trusts `X-Tenant-Id / X-User-Id / X-Role` (the UI has three inputs for it) and refuses to listen on
non-loopback addresses. For anything shared use `SUPDEV_AUTH=token`.

**Settings page (`/settings`, admin role):** LLM provider + API key, integrations (Jira, GitHub, Grafana, Loki, Rancher,
Figma, local test runner), MCP servers with per-tool tagging, policy, general, users & API tokens. Secrets are encrypted at
rest and write-only. See [`docs/configuration.md`](docs/configuration.md) and [spec 11](docs/specs/11-admin-settings.md).

## Docker

```bash
cp .env.example .env     # set SUPDEV_BOOTSTRAP_TOKEN (openssl rand -hex 32)
docker compose up --build
# open http://localhost:8100 , paste the token in "API token", then Settings
```
Status: the image has not been built yet — see [spec 12](docs/specs/12-docker-deploy.md) for what was and wasn't verified.

## Everything is a plugin

| Entry-point group | What | Built in |
|---|---|---|
| `supdev.llm` | LLM provider | `anthropic`, `fake` |
| `supdev.capabilities` | integration factories (per tenant) | `github`, `jira`, `grafana`, `loki`, `rancher`, `figma`, `local_exec`, `mcp`, `fake` |
| `supdev.modes` | modes (phases, gates, templates, tools) | `dev`, `support` |
| `supdev.storage` / `secrets` / `audit` / `auth` / `redactors` | infrastructure | `sqlite`,`memory` / `env` / `jsonl`,`memory` / `dev-header` / `regex` |

A third-party package adds any of these with an entry point — no core edits. See
[`docs/plugin-authoring.md`](docs/plugin-authoring.md) and `examples/supdev_example_plugin`.

## Layout

```
src/supdev/core/       runtime, approvals, policy, budget, audit, redaction, prompt builder, models
src/supdev/gateway/    ToolGateway — the single choke point (prod-RO, gates, budget, redact, untrusted envelope)
src/supdev/modes/      dev/ support/ router.py templates.py (D1–D3, S1–S2)
src/supdev/capabilities/  vendor-neutral tool contracts    adapters/  vendor implementations + MCP bridge
src/supdev/api/        FastAPI + SSE + admin API   settings/  tenant config, validation   web/  chat + settings UI   prompts/  split system prompt
tests/                 unit, integration, adapters, e2e (scripted LLM), security
```

See [`docs/architecture.md`](docs/architecture.md) and [`docs/security.md`](docs/security.md).
