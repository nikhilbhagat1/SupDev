"""AgentRuntime: composition root + the turn loop.

The model proposes (text + tool calls); the engine disposes (ToolGateway, PhaseMachine, approvals).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..gateway.tool_gateway import ToolGateway
from ..gateway.untrusted import wrap_untrusted
from ..modes.router import parse_choice, route
from ..plugins.base import (
    AuditSink,
    CapabilityAdapter,
    LLMProvider,
    ModeContext,
    PluginKind,
    Redactor,
    SessionStore,
    TenantConfig,
)
from ..plugins.registry import PluginRegistry
from .approvals import ApprovalService
from .audit import AuditRecord
from .budget import BudgetTracker
from .errors import ApprovalError, PolicyViolation
from .events import Event
from .models import (
    Approval,
    ApprovalKind,
    Message,
    Principal,
    Session,
    WorkItem,
)
from .policy import Policy
from .prompt import PromptBuilder

MAX_STEPS = 15
_TAG = re.compile(r"^\s*\[(DEV|SUPPORT|ROUTER)\b")


@dataclass
class TenantSetup:
    tenant_id: str
    name: str
    policy: Policy = field(default_factory=Policy)  # tenant policy: can only tighten
    integrations: dict[str, dict[str, Any]] = field(default_factory=dict)
    adapters: list[CapabilityAdapter] | None = None  # explicit override (tests/embedding)
    environments: list[str] = field(default_factory=lambda: ["dev", "staging"])
    timezone: str = "UTC"
    repo_context: str = ""
    conventions: str = ""
    service_catalog: str = ""
    version: int = 0  # settings-store version; a change makes the runtime reload the tenant
    llm: dict[str, Any] = field(default_factory=dict)  # {provider, model}; empty => platform default


Emit = Callable[[Event], None]


class AgentRuntime:
    def __init__(
        self,
        registry: PluginRegistry,
        *,
        llm: LLMProvider,
        store: SessionStore,
        audit: AuditSink,
        secrets: Any,
        redactor: Redactor,
        platform_policy: Policy | None = None,
        settings: Any | None = None,
    ) -> None:
        self.registry, self.llm, self.store, self.audit = registry, llm, store, audit
        self.secrets, self.redactor = secrets, redactor
        self.platform_policy = platform_policy or Policy()
        self.settings = settings  # SettingsStore (optional): DB-backed tenant config, hot-reloaded by version
        self._llms: dict[tuple[str, str, str, str], LLMProvider] = {}
        self.tenants: dict[str, TenantSetup] = {}
        self.prompts = PromptBuilder()
        self._adapters: dict[str, list[CapabilityAdapter]] = {}

    # ------------------------------------------------------------------ tenants / setup
    def add_tenant(self, t: TenantSetup) -> None:
        self.tenants[t.tenant_id] = t
        if hasattr(self.secrets, "all_values"):
            for v in self.secrets.all_values(t.tenant_id):
                self.redactor.add_known_secret(v)

    def _tenant(self, p: Principal) -> TenantSetup:
        return self.tenant_by_id(p.tenant_id)

    def tenant_by_id(self, tenant_id: str) -> TenantSetup:
        t = self.tenants.get(tenant_id)
        if self.settings is not None:
            ver = self.settings.version(tenant_id)
            if ver is not None and (t is None or t.version != ver):
                t = self.settings.load_setup(tenant_id)
                if t is not None:
                    self.tenants[tenant_id] = t
                    for v in (self.secrets.all_values(tenant_id) if hasattr(self.secrets, "all_values") else []):
                        self.redactor.add_known_secret(v)
                    self._adapters.pop(tenant_id, None)  # integrations changed: rebuild lazily
        if t is None:
            raise PolicyViolation("unknown tenant")
        return t

    def llm_for(self, t: TenantSetup) -> LLMProvider:
        """Tenant-specific LLM (own provider/model/key from Settings) or the platform default. A tenant that chose a
        provider but has no key gets an error — it never silently spends the platform's key."""
        prov = t.llm.get("provider")
        if not prov:
            return self.llm
        key = self.secrets.get(t.tenant_id, "llm_api_key")
        if not key:
            raise PolicyViolation("LLM API key is not configured for this tenant (Settings → LLM)")
        model = t.llm.get("model") or ""
        ck = (t.tenant_id, prov, model, hashlib.sha256(key.encode()).hexdigest()[:16])
        if ck not in self._llms:
            plugin = self.registry.get(PluginKind.LLM, prov)
            self._llms[ck] = plugin.with_config(api_key=key, model=model or None) if hasattr(plugin, "with_config") else plugin
        return self._llms[ck]

    def policy_for(self, t: TenantSetup) -> Policy:
        eff = self.platform_policy.merge(t.policy)
        eff.allowed_environments = (
            set(t.environments) if eff.allowed_environments is None
            else eff.allowed_environments & set(t.environments)
        )
        return eff

    def adapters_for(self, t: TenantSetup) -> list[CapabilityAdapter]:
        if t.adapters is not None:
            return t.adapters
        if t.tenant_id not in self._adapters:
            cfg, out = TenantConfig(t.tenant_id, t.integrations), []
            for name, factory in self.registry.all(PluginKind.CAPABILITY).items():
                if name in t.integrations:
                    out.extend(factory.create(cfg, self.secrets))
            self._adapters[t.tenant_id] = out
        return self._adapters[t.tenant_id]

    # ------------------------------------------------------------------ sessions
    def create_session(self, principal: Principal) -> Session:
        self._tenant(principal)
        s = Session(tenant_id=principal.tenant_id, user_id=principal.user_id)
        self.store.save(s)
        return s

    def get_session(self, principal: Principal, session_id: str) -> Session:
        s = self.store.get(principal.tenant_id, session_id)  # tenant-scoped: foreign id == not found
        if s is None:
            raise PolicyViolation("session not found")
        return s

    def modes(self) -> dict[str, Any]:
        return self.registry.all(PluginKind.MODE)

    def start_work_item(self, principal: Principal, session: Session, *, mode: str, ref: str | None = None,
                        title: str = "", context: dict[str, Any] | None = None) -> WorkItem:
        """Explicit user action: begins (or switches to) a work item; parks any current one."""
        pol = self.policy_for(self._tenant(principal))
        if mode not in self.modes() or (pol.enabled_modes is not None and mode not in pol.enabled_modes):
            raise PolicyViolation(f"mode '{mode}' is not enabled")
        if session.active is not None:
            session.active.status = "parked"
            session.parked.append(session.active)
        wi = WorkItem(mode=mode, ref=ref, title=title, context=context or {})
        session.active = wi
        self._audit(principal, session, "mode_switch", detail={"mode": mode, "ref": ref})
        return wi

    def resume_work_item(self, principal: Principal, session: Session, wi_id: str) -> WorkItem:
        target = next((w for w in session.parked if w.id == wi_id), None)
        if target is None:
            raise PolicyViolation("no such parked work item")
        session.parked.remove(target)
        if session.active is not None:
            session.active.status = "parked"
            session.parked.append(session.active)
        target.status = "active"
        session.active = target
        return target

    def extend_budget(self, principal: Principal, session: Session, kind: str, n: int) -> None:
        if principal.role.value not in ("lead", "admin"):
            raise ApprovalError("only a lead/admin can extend the budget")
        BudgetTracker(session, {}).extend(kind, n)
        self._audit(principal, session, "budget_extended", detail={"kind": kind, "n": n})
        self.store.save(session)

    # ------------------------------------------------------------------ approvals
    def grant(self, principal: Principal, session: Session, approval_id: str,
              seen_hash: str | None = None) -> Approval:
        wi = session.active
        if wi is None:
            raise ApprovalError("no active work item")
        svc = ApprovalService(self.policy_for(self._tenant(principal)))
        ap = svc.grant(wi, principal, approval_id, seen_hash)
        self._after_grant(principal, session, wi, ap)
        self.store.save(session)
        return ap

    def _after_grant(self, principal: Principal, session: Session, wi: WorkItem, ap: Approval) -> None:
        self._audit(principal, session, "approval_granted", detail={"kind": ap.kind.value, "hash": ap.artifact_hash})
        session.messages.append(Message(
            role="system_note",
            text=f"A human with role '{ap.granted_role.value if ap.granted_role else '?'}' explicitly "
                 f"approved '{ap.kind.value}' (artifact hash {ap.artifact_hash[:8]})."))
        art = wi.artifacts.get(ap.kind.value)
        if ap.kind == ApprovalKind.HANDOFF and isinstance(art, dict):
            ap.consumed = True
            wi.status = "handed_off"
            session.parked.append(wi)
            new = WorkItem(mode="dev", ref=wi.ref, title=wi.title,
                           context={"handoff_from": wi.id, "rca": art.get("rca"),
                                    "evidence": art.get("evidence"), "proposed_fix": art.get("proposed_fix")})
            session.active = new
            session.messages.append(Message(role="system_note", text=(
                "Handoff approved. Starting DEV Phase 1. The RCA is CONTEXT only — it is not "
                "clarification, plan approval or any other approval. All DEV gates apply.\n"
                + wrap_untrusted("handoff.rca", json.dumps(new.context, default=str)[:6000]))))
        elif ap.kind == ApprovalKind.MODE_SWITCH and isinstance(art, dict):
            ap.consumed = True
            self.start_work_item(principal, session, mode=str(art.get("to", "support")),
                                 title=wi.title, context={"from_dev": wi.id, "reason": art.get("reason")})

    # ------------------------------------------------------------------ the turn
    async def handle_message(self, principal: Principal, session_id: str, text: str, emit: Emit,
                             ticket_type: str | None = None, ref: str | None = None) -> None:
        session = self.get_session(principal, session_id)
        tenant = self._tenant(principal)
        clean, found = self.redactor.redact(text)  # A4: secrets in user content are masked everywhere
        if found:
            emit(Event(type="flag", data={"kind": "secret_in_user_message", "types": sorted(set(found))}))
        session.messages.append(Message(role="user", text=clean))

        if session.active is None:
            decided = self._route(principal, session, tenant, clean, ticket_type, ref, emit)
            if not decided:
                self.store.save(session)
                emit(Event(type="done"))
                return
        elif session.active is not None:
            svc = ApprovalService(self.policy_for(tenant))
            try:
                ap = svc.grant_from_text(session.active, principal, clean)
            except ApprovalError as exc:
                emit(Event(type="error", data={"message": str(exc)}))
                session.messages.append(Message(role="system_note", text=f"Approval refused: {exc}"))
                ap = None
            if ap is not None:
                self._after_grant(principal, session, session.active, ap)
                emit(Event(type="approval_granted", data={"kind": ap.kind.value, "by": principal.user_id}))
        await self._loop(principal, session, tenant, emit)

    async def continue_turn(self, principal: Principal, session_id: str, emit: Emit) -> None:
        """Resume after a button-based approval (no new user text)."""
        session = self.get_session(principal, session_id)
        await self._loop(principal, session, self._tenant(principal), emit)

    def _route(self, principal: Principal, session: Session, tenant: TenantSetup, text: str,
               ticket_type: str | None, ref: str | None, emit: Emit) -> bool:
        pol = self.policy_for(tenant)
        modes = self.modes()
        chosen: str | None = None
        if session.pending_router_question:
            chosen = parse_choice(text, modes)
            if chosen is None:
                self._say(session, "[ROUTER · Mode selection] Please reply \"dev\" or \"support\".", emit)
                return False
            if pol.enabled_modes is not None and chosen not in pol.enabled_modes:
                self._say(session, f"[ROUTER · Mode selection] The {chosen} mode is not enabled for your tenant.", emit)
                return False
        else:
            d = route(text, ticket_type, modes, pol.enabled_modes)
            if d.mode is None:
                session.pending_router_question = True
                self._say(session, f"[ROUTER · Mode selection] {d.question}", emit)
                return False
            chosen = d.mode
        session.pending_router_question = False
        title = next((m.text for m in session.messages if m.role == "user"), text)[:120]
        wi = self.start_work_item(principal, session, mode=chosen, ref=ref, title=title)
        emit(Event(type="mode", data={"mode": chosen, "work_item": wi.id}))
        return True

    def _say(self, session: Session, text: str, emit: Emit) -> None:
        session.messages.append(Message(role="assistant", text=text))
        emit(Event(type="assistant", data={"text": text}))

    async def _loop(self, principal: Principal, session: Session, tenant: TenantSetup, emit: Emit) -> None:
        pol = self.policy_for(tenant)
        for _ in range(MAX_STEPS):
            wi = session.active
            if wi is None:
                break
            mode = self.registry.get(PluginKind.MODE, wi.mode)
            approvals = ApprovalService(pol)

            def _emit(t: str, d: dict[str, Any], _s: Session = session) -> None:
                if t == "approval_request":
                    self._audit(principal, _s, "approval_requested", detail=d)
                emit(Event(type=t, data=d))

            ctx = ModeContext(session=session, work_item=wi, principal=principal, policy=pol,
                              approvals=approvals, emit=_emit)
            gw = ToolGateway(adapters=self.adapters_for(tenant), internal=mode.internal_tools(),
                             redactor=self.redactor, audit=self.audit, approvals=approvals,
                             emit=_emit, tenant_id=principal.tenant_id)
            budget = BudgetTracker(session, pol.budget)
            if budget.used("tokens") >= budget.limit("tokens"):
                emit(Event(type="budget_paused", data={"kind": "tokens"}))
                self._say(session, self._tag(mode, wi) + " Token budget reached. A lead/admin must extend it "
                          "before I continue.", emit)
                break
            tools = [] if session.force_no_tools else gw.offered(ctx)
            system = self.prompts.build(mode_file=mode.prompt_file, variables=self._vars(
                principal, tenant, pol, session, gw, ctx))
            resp = await self.llm_for(tenant).complete(system, session.messages, tools)
            budget.record_tokens(resp.usage.input_tokens + resp.usage.output_tokens)
            text = self._enforce_tag(resp.text, mode, wi) if resp.text else ""
            session.messages.append(Message(role="assistant", text=text, tool_calls=resp.tool_calls))
            if text:
                emit(Event(type="assistant", data={"text": text}))
            if not resp.tool_calls:
                session.force_no_tools = False
                break
            end, displays = False, []
            for tc in resp.tool_calls:
                res = await gw.invoke(ctx, mode, tc)
                session.messages.append(Message(role="tool", text=res.content, tool_call_id=tc.id,
                                                tool_name=tc.name))
                if res.display:
                    displays.append(res.display)
                end = end or res.end_turn
            for d in displays:
                self._say(session, d, emit)
            self.store.save(session)
            if end and session.force_no_tools:
                continue  # stuck: one final tool-less call so the model summarizes and asks for help
            if end:
                break
        self.store.save(session)
        emit(Event(type="done", data={"session": session.snapshot_for_prompt()}))

    # ------------------------------------------------------------------ helpers
    def _tag(self, mode: Any, wi: WorkItem) -> str:
        ph = mode.phases()[wi.phase]
        return f"[{mode.name.upper()} · Phase {wi.phase + 1} · {ph.title}]"

    def _enforce_tag(self, text: str, mode: Any, wi: WorkItem) -> str:
        return text if _TAG.match(text) else f"{self._tag(mode, wi)} {text}"

    def _vars(self, p: Principal, t: TenantSetup, pol: Policy, s: Session, gw: ToolGateway,
              ctx: ModeContext) -> dict[str, Any]:
        return {
            "TENANT_ID": t.tenant_id, "TENANT_NAME": t.name, "USER_ID": p.user_id,
            "USER_ROLE": p.role.value,
            "TENANT_POLICY": pol.free_text or "(none beyond platform defaults)",
            "ENABLED_MODES": ", ".join(sorted(pol.enabled_modes or self.modes().keys())).upper(),
            "ENVIRONMENT_ACCESS": ", ".join(sorted(pol.allowed_environments or [])) + " (prod is read-only)",
            "AVAILABLE_TOOLS": gw.tools_prompt_block(ctx),
            "REPO_CONTEXT": t.repo_context or "(none provided)",
            "PROJECT_CONVENTIONS": t.conventions or "(none provided)",
            "SERVICE_CATALOG": t.service_catalog or "(none provided)",
            "SESSION_STATE": s.snapshot_for_prompt(),
            "MAX_PLAN_ITERATIONS": str(pol.max_plan_iterations),
            "MAX_HYPOTHESIS_ROUNDS": str(pol.max_hypothesis_rounds),
            "DEFAULT_LOOKBACK": pol.default_lookback,
        }

    def _audit(self, p: Principal, s: Session, kind: str, detail: dict[str, Any]) -> None:
        d, _ = self.redactor.redact_obj(detail)
        self.audit.write(AuditRecord(tenant_id=p.tenant_id, session_id=s.id, user_id=p.user_id,
                                     kind=kind, detail=d))
