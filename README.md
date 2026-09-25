# Supdev — unified engineering agent

One app, one agent, two modes, built from [`docs/source_system_prompt.md`](docs/source_system_prompt.md):

* **Development** — ticket/story/design → clarify → plan → *approved* code → tests → PR (7 phases)
* **Support** — production issue → triage → evidence → RCA → *approved* outputs (7 phases, prod is read-only)

The prompt states the rules; **the engine enforces them** (phase gates, approvals bound to artifact hashes, prod
read-only, redaction, tenant isolation, budgets, audit). The model proposes, the engine disposes.

## What you get

* **A Jira-style Kanban board** per mode. Cards are your Jira **stories, tasks and bugs** (epics and sub-tasks are never shown).
  Columns are your Jira board's real columns; drag a card (or use its **⋯** menu) to change its status **in Jira**. Statuses stay in
  sync (auto-refresh + **↻ Sync**). Filter by **Release** (includes work under an epic that carries the release) and by issue type.
  A **Releases** panel lists your Jira releases with progress. Switch to **Supdev phase** grouping to see work by workflow phase.
* **Start → chat, per ticket.** Press **Start** on a card: Supdev creates that ticket's own work item and chat in a panel on the left of
  the board. Every ticket has separate conversation and details. The agent stops at each gate and only a human can approve.
* **Optional Jira status sync**: when the agent moves a ticket forward (e.g. Plan Review → Development), Supdev can move the Jira ticket
  too (off by default; you choose the statuses; audited).
* **Settings** (`/settings`, admin): LLM provider + key, integrations (Jira, GitHub, Grafana, Loki, Rancher, Figma, local test runner) with a
  **Test connection** button, MCP servers with per-tool tagging, board issue types, Jira status sync, policy, tokens.
  Secrets are encrypted at rest and write-only.
* **Nothing about your workflow is hardcoded**: issue types, statuses and columns come from Jira; mode names from the plugin registry.

## Quick start

```bash
uv sync
uv run pytest                                # 127 tests incl. the security suite (scripted LLM, no network)
uv run supdev plugins                        # what got discovered
uv run supdev serve --port 8100              # http://127.0.0.1:8100 (demo tenant); add a key in Settings → LLM
```

Local dev auth trusts `X-Tenant-Id / X-User-Id / X-Role` (the avatar menu has the inputs) and **refuses to listen on non-loopback
addresses**. For anything shared use `SUPDEV_AUTH=token` (`supdev token --tenant T --user U --role admin`).
Configuration: [`docs/configuration.md`](docs/configuration.md).

**Keep secrets out of git.** The SQLite DB (holds encrypted tenant secrets), the audit log and the master key are gitignored; a common
layout is to put them in a `.secrets/` folder (`SUPDEV_DB=.secrets/supdev.db SUPDEV_AUDIT_PATH=.secrets/audit.jsonl
SUPDEV_MASTER_KEY_FILE=.secrets/.supdev_master_key`). Don't zip or share that folder.

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
| `supdev.modes` | modes (phases, gates, templates, tools; each has a `label`) | `dev`, `support` |
| `supdev.storage` / `secrets` / `audit` / `auth` / `redactors` | infrastructure | `sqlite`,`memory` / `db`,`env` / `jsonl`,`memory` / `dev-header`,`token` / `regex` |

A third-party package adds any of these with an entry point — no core edits. See
[`docs/plugin-authoring.md`](docs/plugin-authoring.md) and `examples/supdev_example_plugin`.

## Layout

```
src/supdev/core/       runtime, approvals, policy, budget, audit, redaction, crypto, prompt builder, models
src/supdev/gateway/    ToolGateway — the single choke point (prod-RO, gates, budget, redact, untrusted envelope)
src/supdev/modes/      dev/ support/ router.py templates.py (D1–D3, S1–S2)
src/supdev/capabilities/  vendor-neutral tool contracts    adapters/  vendor implementations + MCP bridge
src/supdev/api/        FastAPI + SSE, board/Jira endpoints, admin API   settings/  tenant config + validation
src/supdev/web/        board + ticket chat panel + settings UI (no build step)   prompts/  the split system prompt
tests/                 unit, integration, adapters, e2e (scripted LLM), security
docs/                  architecture, security, configuration, plugin authoring, specs/ (one per feature), CLAUDE.md at the root
```

See [`docs/architecture.md`](docs/architecture.md), [`docs/security.md`](docs/security.md) and the per-feature [`docs/specs/`](docs/specs/README.md).
