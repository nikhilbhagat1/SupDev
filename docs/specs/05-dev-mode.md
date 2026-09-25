# 05 DEV mode

**Purpose** ticket/story/design → clarify → plan → approved code → tests → PR (Part C).

**Phases (idx)** 0 Intake · 1 Clarification · 2 Planning · 3 Plan Review · 4 Development · 5 Testing · 6 PR Approval.
**Exit checks** intake: ≥1 requirement · clarify: no blocking (Ambiguous or un-accepted Assumed) **and** valid `start_planning` approval · plan: `plan_full` saved with every acceptance criterion covered · review: valid `plan` approval · dev: ≥1 successful commit · test: last recorded run passed (verified from tool output) and every AC mapped · PR: PR opened.
**Engine tools** `set_requirements` (moving Ambiguous/Assumed→Clear needs `resolved_by_message` = a real user message index), `request_start_planning`, `save_plan` (re-saving after presentation voids plan approval), `record_test_run` (call_ids must be successful `execution` calls; `passed` comes from their results), `propose_mode_switch`, plus common `advance_phase/go_back/present/request_approval`.
**Guardrails** branch/commit only in Development/Testing and with valid plan approval; push/PR only at the PR gate for the previewed branch; protected/`release/*` branches refused; `force` refused; commits touching CI files / dependency manifests need `ci_change` / `dependency` approval of exactly those paths; D2 iteration cap (`max_plan_iterations`) → `continue_plan_iterations` approval; D3 requires a passing recorded test run.

**Acceptance → tests** `test_full_dev_flow`, `test_cannot_plan_while_ambiguous`, `test_model_cannot_self_resolve_ambiguity`, `test_code_changes_denied_before_plan_approval_and_on_protected_branch`, `test_ci_and_dependency_changes_need_their_own_approval`, git adapter `test_commits_stay_local_until_push_and_protected_refused`.

**Test gaps** Plan-iteration limit/continue flow; `save_plan` AC self-check failure; plan change after approval voiding it end-to-end; `go_back`; D3 rejected without passing tests; review-comment restart of Phase 5; `propose_mode_switch`; push to a different branch than previewed.
**Out of scope** Merge/deploy/delete flows (tools exist, gated by per-action approvals, no dedicated UX); PR review-comment ingestion.
