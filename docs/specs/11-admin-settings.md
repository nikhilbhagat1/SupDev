# 11 Admin settings (LLM key, integrations, MCP servers, policy, tokens)

**Purpose** Let a tenant admin configure the platform from the UI (`/settings`) instead of env vars/files, safely.

**Requirements** LLM key per tenant (A4: platform-held, never shown); integrations + MCP servers configurable; MCP tools untagged ⇒ denied; prod tools read-only; tenant policy only tightens; changes apply without restart; every change audited; secrets encrypted at rest and write-only.

**Design**
- Config: `TenantDoc` (versioned JSON) in SQLite (`settings/store.py`); the runtime reloads a tenant when its version changes and drops cached adapters (`AgentRuntime.tenant_by_id`). `SUPDEV_CONFIG` JSON only *seeds* missing tenants.
- Secrets: `DbSecretStore` (`secrets/db.py`), Fernet with `SUPDEV_MASTER_KEY` (or a 0600 key file created on first secret). Ciphertext bound to (tenant, name). API returns secret *names* only; new values are added to the redactor's known-secret list.
- LLM: `llm_for(tenant)` uses the tenant's provider/model + `llm_api_key`; a tenant that picked a provider without a key fails (never silently uses the platform key). Default = platform LLM.
- Validation (`settings/validate.py`): per-integration schemas; URLs pass `core/netguard.check_url` (https, no creds, no private/loopback/link-local unless the OPERATOR allows) at save time **and on every request**; MCP tool tags checked (capability known, prod ⇒ read only, writes need `approval_kind`); `workdir` is platform-managed.
- Operator flags (`core/flags.py`, env, tenant admins cannot change): `SUPDEV_ALLOW_PRIVATE_URLS`, `SUPDEV_ALLOW_HTTP`, `SUPDEV_ALLOW_STDIO_MCP` + `SUPDEV_MCP_ALLOWED_COMMANDS`, `SUPDEV_ALLOW_LOCAL_EXEC`. Off by default because stdio MCP and local test execution run code on the platform host.
- API `/api/admin/*` (`api/admin.py`): admin = write, lead = read; endpoints: config, general, policy, llm (+test, delete key), integrations/{name} (+test, delete), mcp/discover, secrets delete, tokens. `ping()` on adapters powers "Test connection". MCP discovery lists remote tools but exposes nothing until tagged and saved.
- Auth: `TokenAuth` (`auth/token.py`) bearer tokens stored as SHA-256; `supdev token create`, `SUPDEV_BOOTSTRAP_TOKEN` for the first admin; `serve` refuses `dev-header` auth on a non-loopback host.
- UI: `web/settings.{html,js,css}` — tabs LLM · Integrations · MCP servers · Policy · General · Users & tokens; DOM APIs only (no innerHTML).

**Acceptance → tests** (`tests/integration/test_admin.py`) `test_token_auth_and_rbac`, `test_token_lifecycle_hashed_shown_once_revocable`, `test_secrets_write_only_encrypted_and_never_audited`, `test_blank_secret_keeps_existing_and_reports_missing`, `test_saved_integration_reaches_runtime_without_restart`, `test_tenant_admin_cannot_touch_another_tenant`, `test_policy_from_settings_can_only_tighten`, `test_per_tenant_llm_key_is_used_and_never_falls_back_silently`, `test_ssrf_guard_blocks_internal_addresses`, `test_code_execution_features_are_off_unless_operator_enables`, `test_mcp_tag_rules_prod_read_only_and_writes_need_gate`, `test_mcp_bridge_end_to_end_against_a_real_server` (real stdio MCP server), `test_discover_endpoint_lists_tools_but_exposes_nothing`, `test_bootstrap_token_and_tenant_from_env`. Also exercised manually in a browser (LLM/Integrations/MCP tabs, discover → tag → save; secret confirmed encrypted in the DB).

**Test gaps** No automated browser tests; per-integration `ping()`/test-connection against real vendors; Grafana/Rancher/Figma save+test paths; token/password rotation; master-key rotation (not implemented); concurrent edits (last write wins per section); the SSRF check is not immune to DNS rebinding.
**Out of scope** SSO/OIDC login, per-user (vs per-role) permissions, external secret managers (write a `supdev.secrets` plugin), master-key rotation, MCP OAuth flows, versioned config history/rollback UI (the audit log records who changed what, without values).
