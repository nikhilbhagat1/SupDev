# 04 Runtime, router & sessions

**Purpose** Turn loop, mode selection (B1), handoff/park (B2), prompt assembly, persistence.

**Requirements** Tag every message `[MODE · Phase n · Name]`; Bug tickets ask; only enabled modes; one active work item, others parked; SUPPORT→DEV handoff = new work item, RCA is context only, no inherited approvals; DEV→SUPPORT keeps DEV state.

**Design** `AgentRuntime` (`core/runtime.py`): `handle_message` (redact → route or approval-from-text → loop), `continue_turn` (after button approval), `grant`, `start_work_item`, `resume_work_item`, `extend_budget`. Loop ≤15 steps; ends on gate/`end_turn`, budget, or a tool-less reply. `modes/router.py`: ticket type → signals scoring → ask. `PromptBuilder`: Part A + B + active mode + style + *engine contract*; `{{VAR}}` substitution. Stores: `MemorySessionStore`, `SqliteSessionStore` keyed by (tenant, id).

**Acceptance → tests** Bug asks → `test_bug_ticket_asks_which_mode`; full flows → `test_full_dev_flow`, `test_full_support_flow_and_prod_is_read_only`; handoff → `test_handoff_starts_dev_with_fresh_gates`; isolation → `test_session_not_visible_across_tenants`, `test_session_is_tenant_isolated`.

**Also** tenants may come from the settings store (hot reload by version) and the LLM is chosen per tenant (`llm_for`) — see spec 11.

**Jira status sync (opt-in):** when the tenant enables it (Settings → Jira status sync, admin only, off by default), the *engine* — never the model — moves the linked Jira ticket (`work item ref`) after the agent moves the work item **forward** into a mapped phase (`AgentRuntime._sync_jira`; the mapping is **only** what the tenant chose — per-phase lists of THEIR status names, first one the workflow allows wins — so nothing is assumed about a workflow; modes flag phases with `PhaseSpec.hint` (`work` / `review`) purely to help Settings suggest statuses by category). Rules: announced first (`audit_notice`, A9) then audited (`phase_status_sync`); only for roles that may change ticket status; never on `go_back`; never reopens a Done ticket or moves a ticket backwards on the board; workflow transitions only (`JiraAdapter.transition_to_named`); a Jira failure is reported (`jira_sync_failed`) and audited but never blocks the phase change. Tests: `tests/e2e/test_jira_sync.py`, adapter `test_transition_to_named_*`, admin `test_jira_sync_setting_*`.

**Test gaps** No unit tests for `route()` signals/tie/enabled-mode filtering, `parse_choice`, tag enforcement, `resume_work_item`, DEV→SUPPORT switch, token-budget pause, SQLite store round-trip, prompt variable rendering (unknown vars left intact).
**Out of scope** Multi-session concurrency beyond a per-session lock in the API; streaming tokens (events are per step).
