"""SUPPORT / RCA mode (Part D): investigator and advisor, never an operator."""

from __future__ import annotations

from typing import Any

from ...core.models import Access, ApprovalKind, Hypothesis, ToolSpec
from ...plugins.base import Denial, ExitResult, InternalResult, ModeContext, PhaseSpec, ToolHandler
from ..base import ModeBase, _spec, fail

P_TRIAGE, P_INTAKE, P_CLARIFY, P_EVIDENCE, P_HYPO, P_REVIEW, P_OUTPUTS = range(7)
QUERY_CAPS = {"metrics", "logs", "cluster", "traces", "dependencies"}
SCOPE_FIELDS = ["symptom", "services", "environment", "region", "start_time", "ongoing", "impact",
                "suspected_changes"]
REQUIRED_KNOWN = ("services", "environment", "start_time")


def _scope_bounded(ctx: ModeContext) -> list[str]:
    sc = ctx.work_item.scope
    return [k for k in REQUIRED_KNOWN if sc.get(k) in (None, "", [])]


def _exit_triage(ctx: ModeContext) -> ExitResult:
    if "triage" not in ctx.work_item.context:
        return ExitResult(False, "record the triage first (engine.record_triage)")
    return ExitResult(True)


def _exit_intake(ctx: ModeContext) -> ExitResult:
    missing = [k for k in SCOPE_FIELDS if k not in ctx.work_item.scope]
    if missing:
        return ExitResult(False, "mark each field Known or Unknown via engine.record_scope: " + ", ".join(missing))
    return ExitResult(True)


def _exit_clarify(ctx: ModeContext) -> ExitResult:
    if (m := _scope_bounded(ctx)):
        return ExitResult(False, "scope is not bounded; still unknown: " + ", ".join(m) + " (ask with S1)")
    return ExitResult(True)


def _exit_evidence(ctx: ModeContext) -> ExitResult:
    if not ctx.work_item.evidence:
        return ExitResult(False, "no evidence recorded (engine.record_evidence needs real tool-call ids)")
    return ExitResult(True)


def _exit_hypo(ctx: ModeContext) -> ExitResult:
    wi, mx = ctx.work_item, ctx.policy.max_hypothesis_rounds
    rounds = wi.counters.get("hypothesis_rounds", 0)
    if rounds < 1 or len(wi.hypotheses) < 2:
        return ExitResult(False, "form 2–4 competing hypotheses first (engine.record_hypotheses)")
    if not any(h.confidence == "high" for h in wi.hypotheses) and rounds < mx:
        return ExitResult(False, f"no hypothesis is High confidence yet (round {rounds}/{mx}); run the "
                                 "confirming/refuting queries and update, or reach the round limit")
    return ExitResult(True)


def _exit_review(ctx: ModeContext) -> ExitResult:
    if ctx.approvals.valid(ctx.work_item, ApprovalKind.ACCEPT_RCA) is None:
        return ExitResult(False, "the user has not accepted the RCA as presented")
    return ExitResult(True)


def _exit_outputs(ctx: ModeContext) -> ExitResult:
    return ExitResult(True)


class SupportMode(ModeBase):
    name = "support"
    label = "Support"
    prompt_file = "support_mode.md"
    router_signals = ["incident", "outage", "alert", "production", "prod ", "rca", "investigate",
                      "why did", "what happened", "latency", "errors", "5xx", "down"]
    ticket_types = {"incident", "problem", "support"}
    generic_kinds = {ApprovalKind.TICKET_COMMENT, ApprovalKind.TICKET_STATUS, ApprovalKind.POSTMORTEM,
                     ApprovalKind.FOLLOWUP_TICKETS}
    template_phases = {"s1": {P_CLARIFY}, "s2": {P_REVIEW}}

    def phases(self) -> list[PhaseSpec]:
        return [
            PhaseSpec("triage", "Triage", _exit_triage),
            PhaseSpec("intake", "Intake", _exit_intake),
            PhaseSpec("clarification", "Clarification", _exit_clarify),
            PhaseSpec("evidence", "Evidence collection", _exit_evidence, hint="work"),
            PhaseSpec("hypotheses", "Timeline & hypotheses", _exit_hypo),
            PhaseSpec("rca_review", "RCA summary review", _exit_review),
            PhaseSpec("outputs", "Outputs", _exit_outputs),
        ]

    # -- guardrails ----------------------------------------------------------------------
    def check_tool(self, ctx: ModeContext, spec: ToolSpec, args: dict[str, Any]) -> Denial | None:
        if spec.internal:
            return None
        ph = ctx.work_item.phase
        if spec.access == Access.READ and spec.capability in QUERY_CAPS:
            if (m := _scope_bounded(ctx)):
                return Denial("before querying production, the scope must be bounded — unknown: "
                              + ", ".join(m) + ". Record what the ticket tells you (engine.record_scope) "
                              "or ask with the S1 template.")
            return None
        if spec.access != Access.READ:
            if spec.capability not in ("ticketing", "docs"):
                return Denial(f"in SUPPORT mode you are an investigator, not an operator: "
                              f"'{spec.capability}' writes are refused")
            if ph != P_OUTPUTS:
                return Denial("outputs (ticket comment/postmortem/follow-ups/status) are only offered "
                              "after the RCA is accepted, in the Outputs phase")
        return None

    # -- SUPPORT internal tools ----------------------------------------------------------
    def mode_tools(self) -> list[tuple[ToolSpec, ToolHandler]]:
        return [
            (_spec("engine.record_triage", "Phase 0: record whether the issue is ongoing and the "
                   "mitigation options for HUMANS to execute (you never execute them).",
                   {"ongoing": {"type": "boolean"}, "impact": {"type": "string"},
                    "likely_cause": {"type": "string"},
                    "mitigations": {"type": "array", "items": {"type": "object", "properties": {
                        "action": {"type": "string"}, "risk": {"type": "string"},
                        "runbook": {"type": "string"}, "rollback": {"type": "string"}}}},
                    "escalate_to": {"type": "string"}}, ["ongoing"]), self._triage),
            (_spec("engine.record_scope", "Record intake fields; set null for Unknown. Fields: "
                   + ", ".join(SCOPE_FIELDS), {"fields": {"type": "object"}}, ["fields"]), self._scope),
            (_spec("engine.record_evidence", "Record a finding backed by a real, successful tool call "
                   "(source/query/time range are copied from that call — you cannot invent them).",
                   {"tool_call_id": {"type": "string"}, "finding": {"type": "string"}},
                   ["tool_call_id", "finding"]), self._evidence),
            (_spec("engine.record_hypotheses", "Upsert 2–4 competing hypotheses. Set new_round=true "
                   "each time you finish a round of confirm/refute queries.",
                   {"hypotheses": {"type": "array", "items": {"type": "object"}},
                    "new_round": {"type": "boolean"}}, ["hypotheses"]), self._hypotheses),
            (_spec("engine.flag_security_signal", "Security/data-loss/data-exposure signal: stops the "
                   "normal flow and alerts the user.", {"summary": {"type": "string"}},
                   ["summary"]), self._security),
            (_spec("engine.propose_handoff", "After the RCA is accepted: propose handing a code fix to "
                   "DEV mode (needs user approval; DEV gates are NOT inherited).",
                   {"proposed_fix": {"type": "string"}}, ["proposed_fix"]), self._handoff),
        ]

    async def _triage(self, ctx: ModeContext, args: dict[str, Any]) -> InternalResult:
        if ctx.work_item.phase != P_TRIAGE:
            return fail("triage is recorded in Phase 1")
        ctx.work_item.context["triage"] = args
        return InternalResult(ok=True, content="Triage recorded. If ongoing: lead with impact and human "
                                               "mitigations, recommend escalation, and continue the deep RCA only if the user says so.")

    async def _scope(self, ctx: ModeContext, args: dict[str, Any]) -> InternalResult:
        fields = args.get("fields") or {}
        bad = [k for k in fields if k not in SCOPE_FIELDS]
        if bad:
            return fail("unknown fields: " + ", ".join(bad))
        ctx.work_item.scope.update(fields)
        m = _scope_bounded(ctx)
        return InternalResult(ok=True, content="Scope updated. Still unknown for a bounded scope: "
                              + (", ".join(m) or "nothing — bounded"))

    async def _evidence(self, ctx: ModeContext, args: dict[str, Any]) -> InternalResult:
        from ...core.models import Evidence

        wi = ctx.work_item
        entry = next((e for e in wi.call_log if e.call_id == args.get("tool_call_id")), None)
        if entry is None or not entry.ok or entry.access != Access.READ.value or entry.capability == "engine":
            return fail("tool_call_id must reference a successful read call from this work item; "
                        "never invent evidence — 'not yet determined' is a valid answer")
        ev = Evidence(id=f"E{len(wi.evidence) + 1}", tool_call_id=entry.call_id, source_tool=entry.tool,
                      query=entry.args, time_range=str(entry.args.get("time_range") or ""),
                      finding=str(args.get("finding", ""))[:1000])
        wi.evidence.append(ev)
        return InternalResult(ok=True, content=f"Recorded as {ev.id}. Cite it in hypotheses/RCA claims.")

    async def _hypotheses(self, ctx: ModeContext, args: dict[str, Any]) -> InternalResult:
        wi, mx = ctx.work_item, ctx.policy.max_hypothesis_rounds
        known = {e.id for e in wi.evidence}
        by_id = {h.id: h for h in wi.hypotheses}
        for raw in args.get("hypotheses", []):
            try:
                h = Hypothesis.model_validate(raw)
            except Exception as exc:  # noqa: BLE001
                return fail(f"bad hypothesis: {exc}")
            if h.confidence not in ("high", "medium", "low"):
                return fail("confidence must be high|medium|low")
            unknown = (set(h.supporting) | set(h.contradicting)) - known
            if unknown:
                return fail(f"{h.id}: unknown evidence ids {sorted(unknown)}")
            by_id[h.id] = h
        if not 2 <= len(by_id) <= 4:
            return fail("keep 2–4 competing hypotheses")
        if args.get("new_round"):
            if wi.counters.get("hypothesis_rounds", 0) >= mx:
                return fail(f"round limit {mx} reached: report what is known, ruled out, and missing data")
            wi.counters["hypothesis_rounds"] = wi.counters.get("hypothesis_rounds", 0) + 1
        wi.hypotheses = list(by_id.values())
        return InternalResult(ok=True, content=f"{len(wi.hypotheses)} hypotheses; round "
                              f"{wi.counters.get('hypothesis_rounds', 0)}/{mx}.")

    async def _security(self, ctx: ModeContext, args: dict[str, Any]) -> InternalResult:
        ctx.work_item.context["security_signal"] = args.get("summary", "")
        ctx.emit("flag", {"kind": "security_signal", "summary": args.get("summary", "")})
        return InternalResult(ok=True, end_turn=True,
                              display=f"[SUPPORT · Phase {ctx.work_item.phase + 1}] ⚠️ Possible security / "
                                      f"data-exposure signal: {args.get('summary', '')}\nI've paused the normal "
                                      "flow. Please follow your security/incident process. I will post nothing "
                                      "broadly visible without your approval.",
                              content="Security signal raised; flow paused. Wait for the user.")

    async def _handoff(self, ctx: ModeContext, args: dict[str, Any]) -> InternalResult:
        wi = ctx.work_item
        if ctx.approvals.valid(wi, ApprovalKind.ACCEPT_RCA) is None:
            return fail("hand off only after the RCA has been accepted")
        rca = wi.artifacts.get(ApprovalKind.ACCEPT_RCA.value)
        content = {"to": "dev", "rca": rca, "evidence": [e.model_dump() for e in wi.evidence],
                   "proposed_fix": args.get("proposed_fix", "")}
        return self.raise_request(ctx, ApprovalKind.HANDOFF, content,
                                  "DEV starts at Phase 1; the RCA does not count as clarification or plan approval")

    # -- template hooks ------------------------------------------------------------------
    def after_render(self, ctx: ModeContext, name: str, dump: dict[str, Any], text: str):  # type: ignore[override]
        wi = ctx.work_item
        if name == "s2":
            known = {e.id for e in wi.evidence}
            cited: set[str] = set()
            for c in [dump["root_cause"], dump.get("trigger"), *dump.get("contributing", [])]:
                if c:
                    cited |= set(c["evidence"])
            for t in dump["timeline"]:
                cited |= set(t["evidence"])
            if cited - known:
                return fail("RCA cites evidence ids that do not exist: " + ", ".join(sorted(cited - known)))
            ap = ctx.approvals.request(wi, ApprovalKind.ACCEPT_RCA, dump)
            ctx.emit("approval_request", {"id": ap.id, "kind": "accept_rca", "hash": ap.artifact_hash,
                                          "roles": ctx.policy.roles_for(ApprovalKind.ACCEPT_RCA)})
        return None
