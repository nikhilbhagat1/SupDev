# 08 Capabilities & adapters

**Purpose** Vendor-neutral tool contracts so workflows work with any provider (A11), plus reference implementations.

**Contracts** `capabilities/__init__.py`: `ticketing`, `source_control`, `design`, `execution`, `metrics`, `logs`, `cluster`, `traces`, `docs`. Each ToolSpec carries capability/access/environment/approval_kind/artifact_arg/bounded.
**Adapters** `jira` (REST v3, comment authors omitted), `github` (local clone + PR API; commits local until push; never force; path-traversal + protected-branch guards; token via env-config header, not argv/URL), `grafana` (Prometheus proxy, aggregates only), `loki` (limit ≤200, label-injection sanitised), `rancher` (k8s events, GET only), `figma`, `local_exec` (configured commands only, minimal env, `{"passed": bool}`), `mcp_bridge` (only tools tagged in config; stdio or streamable-HTTP; secrets from `SecretStore`), `fake` (tests/demo). Shared: `adapters/util.py` (`parse_time_range` ≤7 days, `Http` with lazy credential lookup).

**Acceptance → tests** (`tests/adapters/test_adapters.py`) `test_time_range_relative_iso_and_limits`, `test_label_selector_needs_scope_and_escapes`, `test_jira_get_item_and_comment`, `test_missing_credential_is_a_clear_error`, `test_loki_bounded_and_limited`, `test_commits_stay_local_until_push_and_protected_refused`, `test_open_pr_uses_api`, `test_local_exec_reports_pass_fail_and_only_configured_tools`, `test_mcp_only_exposes_tagged_tools_with_gates`.

**Test gaps** Grafana, Rancher, Figma adapters; Jira transitions/search/create_items; GitHub merge/delete/search/diff; MCP `call()` against a real server; real-API smoke tests (all HTTP tests use respx mocks, so real API response shapes are unverified).
**Out of scope** Datadog/PagerDuty/GitLab adapters; OAuth flows; per-adapter retries/backoff.
