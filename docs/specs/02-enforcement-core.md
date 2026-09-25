# 02 Enforcement core (policy, approvals, budget, audit, redaction)

**Purpose** Turn Part A rules into code so they don't depend on model compliance.

**Requirements** A2 tenant policy only tightens · A4/A7 redaction · A6 approvals · A8 budget · A9 audit.

**Design**
- `Policy` (`core/policy.py`): approver roles per `ApprovalKind`, denied tools/access, allowed envs/modes, budget, iteration limits. `merge(base, tenant)` = intersect roles/envs/modes, union denies, min limits.
  Default roles: developer/lead/admin → start_planning, plan, pr; lead/admin → merge, destructive, dependency, ci_change, deploy; sre/lead/admin → accept_rca, postmortem, followup_tickets.
- `ApprovalService` (`core/approvals.py`): `request` stores artifact + hash; `grant` requires role + current hash (+ optional `seen_hash`); `grant_from_text` only for strict phrases and exactly one pending request; `valid(kind, artifact?)` recomputes hash; `consume` for one-shot.
- `BudgetTracker`: `tool_calls`, `query_units`, `tokens`; `extend` only from user action (lead/admin).
- `AuditRecord` + `MemoryAuditSink`/`JsonlAuditSink`: per-tenant hash chain; `verify()`.
- `RegexRedactor`: secrets (keys, tokens, JWT, private keys, `password=…`) flagged; PII (email/phone/card) masked; known secret values from `SecretStore`.

**Acceptance → tests** (`tests/security/test_platform_rules.py`)
- tenant can't loosen → `test_tenant_policy_can_only_tighten`
- unauthorised role gets the roles that can → `test_role_that_cannot_approve_is_named_roles_returned`
- change voids approval → `test_approval_void_when_artifact_changes`; stale view rejected → `test_stale_view_cannot_be_approved`
- only explicit text → `test_only_explicit_approval_text_counts`; ambiguity → `test_ambiguous_chat_approval_is_not_a_grant`
- plan ≠ PR → `test_plan_approval_does_not_unlock_pr`
- redaction → `test_redaction_of_secrets_and_pii`; end-to-end no leak → `test_secrets_never_reach_prompt_audit_or_model_context`
- audit tamper detection → `test_audit_chain_detects_tampering`

**Redaction precision:** phone numbers only match structured forms and cards must pass a Luhn check, so dates, timestamps, IPs, versions, epochs and trace ids survive (`test_redaction_keeps_dates_timestamps_ips_and_ids`) — over-redaction would corrupt evidence.

**Test gaps** `JsonlAuditSink` persistence/restart chain; `extend_budget` role check; `Policy.merge` for `enabled_modes`; PII-only redaction not flagged; regex false-positive rate (phone/card patterns are broad).
**Out of scope** Signed/WORM audit storage; DLP-grade redaction; per-user (vs per-role) approver lists.
