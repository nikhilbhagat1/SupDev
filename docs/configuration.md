# Configuration

**Most settings are in the UI: `/settings`** (admin edits, lead views) — sections: **LLM provider · Integrations** (each with **Test connection**, which tests the form as typed without saving) **· MCP servers · Board** (issue types per board) **· Jira status sync · Policy · General · Users & tokens**.
Environment variables are for the *platform operator*. `SUPDEV_CONFIG` (tenants JSON) only **seeds** tenants that don't exist yet.

## Operator environment
| Variable | Purpose |
|---|---|
| `SUPDEV_AUTH` | `dev-header` (local only; `serve` refuses non-loopback) or `token` (bearer tokens; container default) |
| `SUPDEV_BOOTSTRAP_TOKEN` / `SUPDEV_BOOTSTRAP_TENANT` | creates the first admin token for that tenant (default tenant `default`) if none exists |
| `SUPDEV_MASTER_KEY` / `SUPDEV_MASTER_KEY_FILE` | Fernet key that encrypts tenant secrets at rest (env preferred; file auto-created 0600 on first secret) |
| `SUPDEV_HOST`, `SUPDEV_PORT` | bind address / port for `supdev serve` (defaults `127.0.0.1` / `8000`; flags override) |
| `SUPDEV_DB`, `SUPDEV_AUDIT_PATH`, `SUPDEV_WORKDIR_ROOT` | SQLite file, audit JSONL, git clone root. Keep the DB, audit log and key file out of git (e.g. a `.secrets/` folder) |
| `ANTHROPIC_API_KEY`, `SUPDEV_LLM`, `SUPDEV_ANTHROPIC_MODEL` | optional platform-default LLM (tenants can bring their own) |
| `SUPDEV_ALLOW_PRIVATE_URLS` | allow internal/private IP endpoints for integrations (SSRF guard off) |
| `SUPDEV_ALLOW_HTTP` | allow plain `http://` integration URLs |
| `SUPDEV_ALLOW_STDIO_MCP`, `SUPDEV_MCP_ALLOWED_COMMANDS` | let tenants run local MCP server processes (= code execution on the host) |
| `SUPDEV_ALLOW_LOCAL_EXEC` | let tenants run repo tests/lint on the host (= code execution; sandbox it) |
| `SUPDEV_STORAGE`, `SUPDEV_AUDIT`, `SUPDEV_SECRETS`, `SUPDEV_PLUGINS` | choose plugins / load extra ones |

CLI: `supdev serve [--host --port]`, `supdev token --tenant T --user U --role admin`, `supdev plugins`.

## Jira notes
* `project` may be the project **key** (e.g. `SCRUM`) or its name; it is resolved to the key. The board needs Jira read access; drag-to-move and
  status sync need permission to transition issues.
* **Board** types default to every issue type the project has (never epics/sub-tasks). **Jira status sync** is off by default and only moves
  tickets to statuses *you* map in Settings (phase numbers are 1-based; several names = first one your workflow allows wins).

## Seed file (optional)
Credentials are **not** in this file: set them in Settings, or as `SUPDEV_SECRET__<TENANT>__<NAME>` env vars (fallback).

```json
{"tenants": [{
  "id": "acme", "name": "Acme", "environments": ["dev", "staging", "prod"],
  "policy": {"enabled_modes": ["dev", "support"], "denied_tools": [], "budget": {"tool_calls": 40},
             "max_plan_iterations": 3, "free_text": "No changes to billing code."},
  "repo_context": "monorepo, python 3.12", "conventions": "branch: feature/<TICKET>-slug",
  "service_catalog": "checkout — owner: payments-oncall",
  "board": {"types": {"dev": null, "support": ["Task", "Bug"]}},
  "jira_sync": {"enabled": false, "map": {"dev": {"5": ["In Progress"]}}},
  "integrations": {
    "jira":    {"base_url": "https://acme.atlassian.net", "secret": "jira_token", "email_secret": "jira_email", "project": "OPS"},
    "github":  {"repo": "acme/shop", "secret": "github_token", "default_branch": "main"},
    "local_exec": {"workdir": "/var/lib/supdev/work/acme/acme__shop", "commands": {"tests": "pytest -q", "lint": "ruff check .", "typecheck": "mypy ."}},
    "grafana": {"instances": [{"env": "prod", "url": "https://grafana.acme.io", "secret": "grafana_token", "datasource_uid": "prom"}]},
    "loki":    {"url": "https://loki.acme.io", "env": "prod", "secret": "loki_token"},
    "rancher": {"url": "https://rancher.acme.io", "env": "prod", "secret": "rancher_token", "cluster_id": "c-abc"},
    "figma":   {"secret": "figma_token"},
    "mcp":     {"servers": [{"name": "x", "command": "uvx", "args": ["some-mcp"], "tools": {"tool": {"capability": "logs", "access": "read", "environment": "prod", "bounded": true}}}]}
  }}]}
```
