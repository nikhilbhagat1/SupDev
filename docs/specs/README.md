# Feature specs

Each spec: **Purpose · Requirements (source rule) · Design · Interfaces · Acceptance criteria → tests · Test gaps · Out of scope**.
Source rule ids (A1–A11, B, C, D, E) refer to `docs/source_system_prompt.md`.

| # | Spec | Code |
|---|---|---|
| 01 | [Plugin kernel](01-plugin-kernel.md) | `plugins/` |
| 02 | [Enforcement core](02-enforcement-core.md) | `core/{policy,approvals,budget,audit,redaction}.py` |
| 03 | [Tool gateway](03-tool-gateway.md) | `gateway/` |
| 04 | [Runtime, router & sessions](04-runtime-router.md) | `core/runtime.py`, `modes/router.py`, `core/prompt.py`, `storage/` |
| 05 | [DEV mode](05-dev-mode.md) | `modes/dev/` |
| 06 | [SUPPORT mode](06-support-mode.md) | `modes/support/` |
| 07 | [Templates D1–D3, S1–S2](07-templates.md) | `modes/templates.py` |
| 08 | [Capabilities & adapters](08-adapters.md) | `capabilities/`, `adapters/` |
| 09 | [LLM providers](09-llm.md) | `llm/` |
| 10 | [API & web UI](10-api-web.md) | `api/`, `web/` |
| 11 | [Admin settings](11-admin-settings.md) | `api/admin.py`, `settings/`, `secrets/db.py`, `auth/token.py`, `web/settings.*` |
| 12 | [Docker & deployment](12-docker-deploy.md) | `Dockerfile`, `docker-compose.yml` |
