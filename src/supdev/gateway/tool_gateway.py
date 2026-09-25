"""ToolGateway: the single choke point between the model and the outside world.

Order of checks for every call (first failure wins):
  1. tool exists for this tenant/mode          (unknown tools simply do not exist)
  2. tenant policy (denied tools/access/env)   (A2)
  3. PROD READ-ONLY hard stop                  (A11 — no approval can override)
  4. mode guardrails / phase gates             (Part C/D)
  5. approval gate, bound to artifact hash     (A6)
  6. bounded-query + budget                    (A7, A8)
  then: redact args -> audit notice -> call -> redact result -> injection scan -> wrap -> audit.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from ..core.approvals import ApprovalService
from ..core.audit import AuditRecord
from ..core.budget import BudgetTracker
from ..core.errors import BudgetExceeded
from ..core.models import (
    Access,
    ToolCall,
    ToolCallLogEntry,
    ToolResult,
    ToolSpec,
    canonical_hash,
)
from ..plugins.base import (
    AuditSink,
    CapabilityAdapter,
    InternalResult,
    Mode,
    ModeContext,
    Redactor,
    ToolHandler,
)

MAX_RESULT_CHARS = 8000
CALL_LOG_CAP = 200
PROD_NAMES = {"prod", "production", "prd"}
SCOPE_KEYS = ("service", "namespace", "labels", "scope", "selector")


def is_prod(env: str | None) -> bool:
    return bool(env) and env.lower() in PROD_NAMES  # type: ignore[union-attr]


class ToolGateway:
    def __init__(
        self,
        *,
        adapters: list[CapabilityAdapter],
        internal: list[tuple[ToolSpec, ToolHandler]],
        redactor: Redactor,
        audit: AuditSink,
        approvals: ApprovalService,
        emit: Callable[[str, dict[str, Any]], None],
        tenant_id: str,
        call_timeout: float = 60.0,
    ) -> None:
        self._adapters: dict[str, CapabilityAdapter] = {}
        self._specs: dict[str, ToolSpec] = {}
        for ad in adapters:
            for spec in ad.tools():
                if spec.name in self._specs:
                    continue  # first registered wins; duplicates never shadow
                self._specs[spec.name] = spec
                self._adapters[spec.name] = ad
        self._internal: dict[str, ToolHandler] = {}
        for spec, handler in internal:
            spec.internal = True
            self._specs[spec.name] = spec
            self._internal[spec.name] = handler
        self.redactor, self.audit, self.approvals, self.emit = redactor, audit, approvals, emit
        self.tenant_id = tenant_id
        self.call_timeout = call_timeout

    # -- what the model is offered -------------------------------------------------------
    def offered(self, ctx: ModeContext) -> list[ToolSpec]:
        out = []
        for spec in self._specs.values():
            if spec.internal or ctx.policy.tool_allowed(spec) is None:
                out.append(spec)
        return out

    def tools_prompt_block(self, ctx: ModeContext) -> str:
        """Generated {{AVAILABLE_TOOLS}}: capability-tagged, vendor-neutral view (A11)."""
        groups: dict[str, list[ToolSpec]] = {}
        for s in self.offered(ctx):
            if not s.internal:
                groups.setdefault(s.capability, []).append(s)
        if not groups:
            return "(no external tools connected)"
        lines = []
        for _cap, specs in sorted(groups.items()):
            for s in sorted(specs, key=lambda x: x.name):
                lines.append(f"- {s.tag()} — `{s.name}`")
        return "\n".join(lines)

    # -- invocation ----------------------------------------------------------------------
    async def invoke(self, ctx: ModeContext, mode: Mode, call: ToolCall) -> ToolResult:
        wi, sess = ctx.work_item, ctx.session
        spec = self._specs.get(call.name)

        def deny(reason: str, flag: str = "denied") -> ToolResult:
            self._audit(ctx, "tool_denied", spec, call, {"reason": reason})
            self._log(ctx, call, ok=False)
            self.emit("tool_result", {"name": call.name, "ok": False, "denied": reason})
            return ToolResult(call_id=call.id, name=call.name, ok=False,
                              content=f"DENIED: {reason}", flags=[flag])

        if spec is None:
            return deny(f"tool '{call.name}' is not available", "unknown_tool")
        if not spec.internal:
            if (why := ctx.policy.tool_allowed(spec)) is not None:
                return deny(why, "policy")
            if is_prod(spec.environment) and spec.access != Access.READ:  # A11: no override
                return deny(
                    f"production is read-only; '{spec.name}' ({spec.access.value}) is refused in "
                    "every mode. Give this to a human as a recommendation with steps, risk and rollback.",
                    "prod_read_only",
                )
        if (not spec.internal and spec.access not in (Access.READ, Access.EXECUTE)
                and spec.approval_kind is None):
            return deny(f"'{spec.name}' is a write tool without an approval gate (adapter "
                        "misconfiguration); refused", "misconfigured")
        if (d := mode.check_tool(ctx, spec, call.args)) is not None:
            return deny(d.reason, "mode_guardrail")

        approval = None
        if spec.approval_kind is not None:
            art = call.args.get(spec.artifact_arg) if spec.artifact_arg else None
            if spec.artifact_arg and spec.artifact_arg not in call.args:
                return deny(f"missing argument '{spec.artifact_arg}'", "bad_args")
            approval = self.approvals.valid(wi, spec.approval_kind, art)
            if approval is None:
                roles = ", ".join(ctx.policy.roles_for(spec.approval_kind)) or "no role"
                return deny(
                    f"'{spec.name}' needs an approval of kind '{spec.approval_kind.value}' for exactly "
                    f"the artifact shown (present it first; roles that can approve: {roles}). "
                    "Approvals are void if the artifact changed.",
                    "approval_required",
                )

        budget = BudgetTracker(sess, ctx.policy.budget)
        if spec.bounded and not spec.internal:
            problem = self._unbounded(call.args, ctx.policy.default_lookback)
            if problem:
                return deny(problem, "unbounded_query")
        try:
            budget.charge("tool_calls")
            if spec.bounded:
                budget.charge("query_units")
        except BudgetExceeded as exc:
            self._audit(ctx, "budget_paused", spec, call, {"budget": exc.kind})
            self.emit("budget_paused", {"kind": exc.kind, "used": budget.used(exc.kind),
                                        "limit": budget.limit(exc.kind)})
            self._log(ctx, call, ok=False)
            return ToolResult(
                call_id=call.id, name=call.name, ok=False, end_turn=True, flags=["budget"],
                content=f"BUDGET: {exc}. Stop and ask the user whether to extend the budget.",
            )

        # ---- execute
        if spec.internal:
            self.emit("tool_call", {"name": call.name, "internal": True})
            res: InternalResult = await self._internal[spec.name](ctx, call.args)
            self._log(ctx, call, ok=res.ok)
            return ToolResult(call_id=call.id, name=call.name, ok=res.ok, content=res.content,
                              end_turn=res.end_turn, display=res.display)

        args, arg_findings = self.redactor.redact_obj(call.args)
        if not spec.is_read:
            self.emit("audit_notice", {"line": f"Will write to {spec.capability}/{spec.vendor}: "
                                               f"{spec.name} — {self._brief(args)}"})
        elif not arg_findings:
            args = call.args
        flags = [f"arg_secret:{f}" for f in arg_findings]
        if arg_findings:
            self.emit("flag", {"kind": "secret_in_args", "tool": spec.name})

        self.emit("tool_call", {"name": call.name, "args": args})
        try:
            raw = await asyncio.wait_for(self._adapters[spec.name].call(spec.name, args),
                                         self.call_timeout)
        except Exception as exc:  # noqa: BLE001 — adapter failures become tool errors
            msg, _ = self.redactor.redact(f"{type(exc).__name__}: {exc}")
            return self._failure(ctx, spec, call, msg)

        if isinstance(raw, dict) and raw.get("tenant_id") not in (None, self.tenant_id):  # A3
            self.emit("flag", {"kind": "possible_tenant_isolation_bug", "tool": spec.name})
            self._audit(ctx, "isolation_violation", spec, call, {})
            self._log(ctx, call, ok=False)
            return ToolResult(call_id=call.id, name=call.name, ok=False, end_turn=True,
                              flags=["isolation"],
                              content="BLOCKED: result appeared to contain another tenant's data. "
                                      "Stop and report a possible isolation bug to the user.")

        text = raw if isinstance(raw, str) else json.dumps(raw, default=str, indent=1)
        truncated = len(text) > MAX_RESULT_CHARS
        text = text[:MAX_RESULT_CHARS] + ("\n…[truncated; narrow the query]" if truncated else "")
        text, findings = self.redactor.redact(text)
        if findings:
            flags += [f"exposed_secret:{f}" for f in findings]
            self.emit("flag", {"kind": "secret_exposed_in_content", "tool": spec.name,
                               "types": sorted(set(findings))})
        from .injection import scan
        from .untrusted import wrap_untrusted

        inj = scan(text)
        if inj:
            flags += [f"injection:{i}" for i in inj]
            self.emit("flag", {"kind": "possible_prompt_injection", "source": spec.name, "patterns": inj})
        if approval is not None and spec.artifact_arg:
            self.approvals.consume(approval)  # one-shot: bound to the exact arg value
        self._audit(ctx, "tool_call", spec, call, {"args_hash": canonical_hash(args), "flags": flags})
        passed = raw.get("passed") if isinstance(raw, dict) and isinstance(raw.get("passed"), bool) else None
        self._log(ctx, call, ok=True, passed=passed)
        self.emit("tool_result", {"name": call.name, "ok": True, "flags": flags})
        return ToolResult(call_id=call.id, name=call.name, ok=True, flags=flags,
                          content=wrap_untrusted(spec.name, text, inj))

    # -- helpers -------------------------------------------------------------------------
    @staticmethod
    def _unbounded(args: dict[str, Any], lookback: str) -> str | None:
        if not args.get("time_range"):
            return (f"query needs an explicit `time_range` (default lookback {lookback}); "
                    "narrow time range first")
        if not any(args.get(k) for k in SCOPE_KEYS):
            return "query needs a scope (service, namespace or labels); unscoped queries are refused"
        return None

    def _failure(self, ctx: ModeContext, spec: ToolSpec, call: ToolCall, msg: str) -> ToolResult:
        self._log(ctx, call, ok=False)
        self._audit(ctx, "tool_error", spec, call, {"error": msg[:300]})
        key = "fail:" + canonical_hash([call.name, call.args, msg])[:12]
        n = ctx.work_item.counters[key] = ctx.work_item.counters.get(key, 0) + 1
        content = f"ERROR: {msg}"
        end = False
        if n >= 3:  # A10: same failure 3 times => stop and ask for help
            ctx.session.force_no_tools = True
            end = True
            content += ("\nSTOP: this exact failure has happened 3 times. Do not retry. Summarize what "
                        "you tried and ask the user for help.")
            self.emit("stuck", {"tool": call.name})
        self.emit("tool_result", {"name": call.name, "ok": False, "error": msg[:200]})
        return ToolResult(call_id=call.id, name=call.name, ok=False, content=content, end_turn=end)

    def _log(self, ctx: ModeContext, call: ToolCall, ok: bool, passed: bool | None = None) -> None:
        args, _ = self.redactor.redact_obj(call.args)
        spec = self._specs.get(call.name)
        log = ctx.work_item.call_log
        log.append(ToolCallLogEntry(
            call_id=call.id, tool=call.name, args=args, ok=ok, passed=passed,
            capability=spec.capability if spec else "", access=spec.access.value if spec else ""))
        del log[:-CALL_LOG_CAP]

    def _audit(self, ctx: ModeContext, kind: str, spec: ToolSpec | None, call: ToolCall,
               detail: dict[str, Any]) -> None:
        d, _ = self.redactor.redact_obj(detail)
        self.audit.write(AuditRecord(
            tenant_id=ctx.principal.tenant_id, session_id=ctx.session.id, user_id=ctx.principal.user_id,
            kind=kind, system=(spec.capability if spec else ""), action=call.name, detail=d))

    @staticmethod
    def _brief(args: dict[str, Any]) -> str:
        s = json.dumps(args, default=str)
        return s if len(s) <= 160 else s[:157] + "..."
