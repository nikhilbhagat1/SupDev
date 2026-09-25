# 03 Tool gateway

**Purpose** Single choke point between the model and the world (A3, A5, A7–A11, DEV/SUPPORT guardrails).

**Order of checks (first failure wins)** 1 tool exists (else `not available`) → 2 tenant policy → 3 **prod read-only** → 4 write-tool-without-gate refusal → 5 mode guardrails (`Mode.check_tool`) → 6 approval gate (`approval_kind`, `artifact_arg`) → 7 bounded-query rules → 8 budget → execute: redact args, audit notice for writes, call (timeout), foreign-`tenant_id` block, truncate (8000 chars), redact result, injection scan, wrap `<untrusted>`, consume one-shot approval, audit, call log.
Repeated identical failure ×3 → `stuck`, forces a tool-less summary turn (A10).

**Interfaces** `ToolGateway(adapters, internal, redactor, audit, approvals, emit, tenant_id)`; `offered(ctx)` (what the model sees), `tools_prompt_block(ctx)` (generated `{{AVAILABLE_TOOLS}}`), `invoke(ctx, mode, call) -> ToolResult`.

**Acceptance → tests**
- prod write refused even with matching approval → `test_prod_write_denied_even_with_matching_approval_record`; SUPPORT never writes prod/source control → `test_support_never_writes_to_prod_or_source_control`
- injection flagged, envelope unbreakable, gates unmoved → `test_injection_detected_and_envelope_cannot_be_closed`, `test_tool_output_injection_does_not_advance_gates_and_is_flagged`
- foreign tenant data blocked → `test_foreign_tenant_data_in_tool_result_is_blocked`
- denied tool not offered → `test_tenant_denied_tool_is_not_even_offered`
- budget pause → `test_budget_pause_asks_before_continuing`; stuck → `test_same_failure_three_times_stops_and_forces_summary`
- unbounded query denied → `test_query_denied_until_scope_is_bounded` (+ `time_range` denial in `test_full_support_flow_and_prod_is_read_only`)
- approval bound to exact written text → `test_full_support_flow_and_prod_is_read_only` (different comment body denied)

**Test gaps** Adapter timeout path; result truncation; `misconfigured` (write tool w/o gate) path; unknown-tool denial; argument-secret flagging (`arg_secret`).
**Out of scope** Rate limiting per adapter; parallel tool execution; result caching.
