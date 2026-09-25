# Architecture

```
Web UI ──SSE──► FastAPI ──► AgentRuntime ──► LLMProvider (plugin)
                              │  ├─ ModeRouter → Mode plugin (DEV / SUPPORT): phases, exit checks, guardrails, tools
                              │  ├─ PromptBuilder (Part A + B + active mode + style + engine contract)
                              │  └─ ToolGateway ──► CapabilityAdapter plugins (per tenant)
                              └─ SessionStore / AuditSink / SecretStore / Redactor (plugins)
```

## Turn loop (`core/runtime.py`)
1. Load session (tenant-scoped), redact the user message (secrets are masked + flagged), append it.
2. No active work item → deterministic router (ticket type, signals) or ask; **Bug** always asks.
3. Explicit chat approval ("approved", "yes, go ahead"…) is parsed **server-side** and only grants when exactly one
   request is pending and the user's role may approve it.
4. Loop (≤15 steps): build prompt → LLM → run each tool call through the **ToolGateway** → stop when a tool ends the turn
   (a gate/template was presented), the budget pauses, or the model answers without tools.

## Phase machine
A `Mode` returns `PhaseSpec(key, title, exit_check)`. The model calls `engine.advance_phase`; the engine evaluates the exit
check against real state (requirements resolved, approval valid for the *current* artifact hash, recorded tool-call ids…).
`engine.go_back` is always allowed. Templates (`modes/templates.py`) are Pydantic models rendered by the engine.

## Approvals
`request()` stores the artifact and its hash; only `grant()` (authenticated human, role-checked) marks it approved;
`valid()` recomputes the hash of the artifact *currently on record* — any change voids the approval. Write tools declare
`approval_kind` (+ `artifact_arg` to bind the approval to the exact value written); `artifact_arg` approvals are one-shot.

## Honesty plumbing (A10)
Evidence, test runs and RCA claims must reference real tool-call ids from the work item's call log; source, query and time
range are copied by the engine, not typed by the model. `Confirmed`/`Likely` claims must cite evidence ids.

## Handoff
SUPPORT→DEV (after `accept_rca`) and DEV→SUPPORT (`propose_mode_switch`) are approvals. Handoff creates a **new** work item
with *no* inherited approvals; the RCA arrives wrapped as untrusted context. The previous item is parked/kept.
