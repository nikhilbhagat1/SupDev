# Security model

| Rule (system prompt) | Enforced by | Proven in |
|---|---|---|
| A2 tenant policy only tightens | `Policy.merge` (∩ roles/envs/modes, ∪ denies, min budgets) | `test_tenant_policy_can_only_tighten` |
| A3 tenant isolation | tenant-keyed stores, per-tenant adapters, foreign `tenant_id` output blocked | `test_session_not_visible_across_tenants`, `test_foreign_tenant_data…` |
| A4 credentials | `SecretStore` resolved inside adapters; redactor on args/results/user text/audit | `test_secrets_never_reach_prompt_audit_or_model_context` |
| A5 untrusted content | `<untrusted>` envelope (cannot be closed), injection flags, approvals only via human action | `test_tool_output_injection…` |
| A6 approvals | `ApprovalService` (role check, hash-bound, void on change, no reuse across kinds) | `test_approval_void…`, `test_plan_approval_does_not_unlock_pr` |
| A7 minimization | bounded queries (time_range + scope), 7-day cap, result truncation, redaction | `test_query_denied_until_scope_is_bounded` |
| A8 budget | `BudgetTracker`; pause + ask; only lead/admin can extend | `test_budget_pause…` |
| A9 audit | hash-chained append-only records; "will write X to Y" notice before every write | `test_audit_chain_detects_tampering` |
| A11 prod read-only | gateway hard stop before any approval logic | `test_prod_write_denied_even_with_matching_approval_record` |
| DEV guardrails | protected branches, no force, CI/dependency files need own approval, commits local until PR approval | `test_ci_and_dependency…`, git adapter test |

| Admin/config safety | encrypted write-only secrets, hashed tokens, operator flags, SSRF guard, MCP tag validation, audited changes | `tests/integration/test_admin.py` |

## Known limits (be honest about them)
* `dev-header` auth is for local use only (the server refuses non-loopback binds with it). `token` auth is the minimum for shared use; plug in OIDC via the `supdev.auth` group for SSO.
* The master key sits next to the encrypted data when auto-generated into `/data`; inject `SUPDEV_MASTER_KEY` from a secret manager in production. No key rotation yet.
* The SSRF guard re-resolves DNS per request but is not immune to DNS-rebinding races; keep `SUPDEV_ALLOW_PRIVATE_URLS` off unless you need it.
* Prompt-injection detection is heuristic and only *flags*; the real defense is that no tool output can grant approvals or
  advance gates, and every write is gated.
* `local_exec` runs repository code on the platform host — run the platform in a sandbox without ambient credentials.
* Regex redaction is best-effort; add a stronger `supdev.redactors` plugin (e.g. a DLP service) for regulated data.
* The prod check trusts the adapter's environment tag. Review adapter/MCP configs; untagged MCP tools are never exposed.
* Approvals by chat text map to the single pending request; the UI button (with `seen_hash`) is the precise path.
