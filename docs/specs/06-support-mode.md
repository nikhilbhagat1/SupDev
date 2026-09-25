# 06 SUPPORT mode

**Purpose** Investigator/advisor, never operator (Part D).

**Phases (idx)** 0 Triage · 1 Intake · 2 Clarification · 3 Evidence · 4 Timeline & hypotheses · 5 RCA review · 6 Outputs.
**Hints** Evidence collection is flagged `work` (Settings can suggest a Jira "in progress" status for it).
**Exit checks** triage recorded · all intake fields marked Known/Unknown · scope bounded (services, environment, start_time known) · ≥1 evidence · ≥2 hypotheses and (a High one **or** round limit reached) · valid `accept_rca` approval · outputs free.
**Engine tools** `record_triage` (mitigations are for humans), `record_scope`, `record_evidence` (real successful read call ids only; source/query/range copied), `record_hypotheses` (2–4, evidence ids validated, `new_round` capped by `max_hypothesis_rounds`), `flag_security_signal` (pauses flow), `propose_handoff` (only after accepted RCA).
**Guardrails** query capabilities (metrics/logs/cluster/traces) denied until scope bounded; only ticketing/docs writes and only in Outputs phase; all other writes refused; S2 must cite existing evidence ids; `Confirmed`/`Likely` claims need evidence.

**Acceptance → tests** `test_full_support_flow_and_prod_is_read_only`, `test_support_never_writes_to_prod_or_source_control`, `test_query_denied_until_scope_is_bounded`, `test_evidence_cannot_be_invented`, `test_handoff_starts_dev_with_fresh_gates`.

**Test gaps** Hypothesis validation (unknown evidence ids, 2–4 count, round cap); S2 citing non-existent evidence; security-signal flow; S1 clarification path; triage-ongoing behaviour (the prompt asks for lead-with-impact; only recorded, not enforced); timeline UTC/local conversion (tenant timezone is stored but unused).
**Out of scope** Deep RCA gating on "user says so" when ongoing; postmortem doc generation (only the gated `docs.write_postmortem` tool exists); follow-up ticket UX.
