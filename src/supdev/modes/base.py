"""Shared machinery for modes: phase advance/back, generic approval requests, transitions."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ..core.models import (
    Access,
    ApprovalKind,
    ToolSpec,
    canonical_hash,
)
from ..plugins.base import (
    Denial,
    ExitResult,
    InternalResult,
    ModeContext,
    PhaseSpec,
    ToolHandler,
)
from .templates import TEMPLATES, render, tag

WRITE_ACCESS = {Access.COMMENT, Access.UPDATE, Access.BRANCH, Access.COMMIT, Access.PUSH, Access.PR,
                Access.MERGE, Access.DELETE, Access.WRITE}


def _spec(name: str, description: str, props: dict[str, Any], required: list[str] | None = None) -> ToolSpec:
    return ToolSpec(
        name=name, description=description, capability="engine", vendor="supdev", internal=True,
        input_schema={"type": "object", "properties": props, "required": required or []},
    )


def fail(msg: str) -> InternalResult:
    return InternalResult(ok=False, content=f"ERROR: {msg}")


class ModeBase:
    name = ""
    prompt_file = ""
    router_signals: list[str] = []
    ticket_types: set[str] = set()
    # generic request_approval kinds this mode may raise (template-based ones are separate)
    generic_kinds: set[ApprovalKind] = set()
    # phase idx -> templates allowed there
    template_phases: dict[str, set[int]] = {}

    # -- Mode protocol -------------------------------------------------------------------
    def phases(self) -> list[PhaseSpec]:
        raise NotImplementedError

    def approvals_for_phase(self, phase: int) -> set[ApprovalKind]:
        return set()

    def check_tool(self, ctx: ModeContext, spec: ToolSpec, args: dict[str, Any]) -> Denial | None:
        return None

    def mode_tools(self) -> list[tuple[ToolSpec, ToolHandler]]:
        return []

    def internal_tools(self) -> list[tuple[ToolSpec, ToolHandler]]:
        common: list[tuple[ToolSpec, ToolHandler]] = [
            (_spec("engine.advance_phase", "Move to the next phase. The engine verifies the exit "
                   "condition and refuses if it is not met.", {}), self._advance),
            (_spec("engine.go_back", "Step back to an earlier phase (always allowed).",
                   {"phase": {"type": "integer", "description": "1-based phase number"},
                    "reason": {"type": "string"}}, ["phase"]), self._go_back),
            (_spec("engine.present", "Present a structured template to the user and end your turn. "
                   "template: " + ", ".join(sorted(self.template_phases)) + ". The engine validates and "
                   "renders the exact format. Gated templates create an approval request that only the "
                   "human can grant.",
                   {"template": {"type": "string", "enum": sorted(self.template_phases)},
                    "data": {"type": "object"}}, ["template", "data"]), self._present),
            (_spec("engine.request_approval", "Ask the human to approve exactly `content` (shown "
                   "verbatim) for a gated write. kind: " + ", ".join(sorted(k.value for k in self.generic_kinds)) +
                   ". `content` must equal the value you will later pass to the gated tool "
                   "(e.g. the exact comment body / status / items).",
                   {"kind": {"type": "string"}, "content": {}, "why": {"type": "string"}},
                   ["kind", "content"]), self._request_approval),
        ]
        return common + self.mode_tools()

    # -- handlers ------------------------------------------------------------------------
    async def _advance(self, ctx: ModeContext, args: dict[str, Any]) -> InternalResult:
        phases, wi = self.phases(), ctx.work_item
        cur = phases[wi.phase]
        if wi.phase + 1 >= len(phases):
            return fail("already in the final phase")
        res: ExitResult = cur.exit_check(ctx)
        if not res.ok:
            return fail(f"cannot leave Phase {wi.phase + 1} ({cur.title}): {res.reason}")
        wi.phase += 1
        nxt = phases[wi.phase]
        ctx.emit("phase", {"mode": self.name, "phase": wi.phase + 1, "title": nxt.title})
        return InternalResult(ok=True, content=f"Now in {tag(self.name, wi.phase + 1, nxt.title)}")

    async def _go_back(self, ctx: ModeContext, args: dict[str, Any]) -> InternalResult:
        wi = ctx.work_item
        target = int(args.get("phase", 0)) - 1
        if not 0 <= target < wi.phase:
            return fail("can only step back to an earlier phase")
        wi.phase = target
        t = self.phases()[target]
        ctx.emit("phase", {"mode": self.name, "phase": target + 1, "title": t.title, "back": True})
        return InternalResult(ok=True, content=f"Stepped back to {tag(self.name, target + 1, t.title)}")

    async def _present(self, ctx: ModeContext, args: dict[str, Any]) -> InternalResult:
        name, data = str(args.get("template", "")), args.get("data") or {}
        if name not in TEMPLATES or name not in self.template_phases:
            return fail(f"unknown template '{name}' for this mode")
        if ctx.work_item.phase not in self.template_phases[name]:
            return fail(f"template '{name}' cannot be presented in the current phase")
        pre = self.prepare_template(ctx, name, dict(data))
        if isinstance(pre, InternalResult):
            return pre
        try:
            model, text = render(name, pre)
        except ValidationError as exc:
            msgs = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:6])
            return fail(f"template '{name}' invalid — {msgs}")
        post = self.after_render(ctx, name, model.model_dump(mode="json"), text)
        if isinstance(post, InternalResult):
            return post
        ctx.emit("artifact", {"template": name, "text": text})
        return InternalResult(ok=True, end_turn=True, display=text,
                              content=post or "Presented to the user. Stop and wait for their reply.")

    def prepare_template(self, ctx: ModeContext, name: str, data: dict[str, Any]) -> dict[str, Any] | InternalResult:
        return data

    def after_render(self, ctx: ModeContext, name: str, dump: dict[str, Any], text: str) -> str | InternalResult | None:
        return None

    async def _request_approval(self, ctx: ModeContext, args: dict[str, Any]) -> InternalResult:
        try:
            kind = ApprovalKind(str(args.get("kind")))
        except ValueError:
            return fail("unknown approval kind")
        if kind not in self.generic_kinds:
            return fail(f"'{kind.value}' cannot be requested this way in {self.name} mode")
        if "content" not in args:
            return fail("content is required")
        return self.raise_request(ctx, kind, args["content"], str(args.get("why", "")))

    def raise_request(self, ctx: ModeContext, kind: ApprovalKind, content: Any, why: str = "") -> InternalResult:
        ap = ctx.approvals.request(ctx.work_item, kind, content)
        roles = ", ".join(ctx.policy.roles_for(kind)) or "none"
        ctx.emit("approval_request", {"id": ap.id, "kind": kind.value, "hash": ap.artifact_hash,
                                      "content": content, "why": why, "roles": ctx.policy.roles_for(kind)})
        shown = content if isinstance(content, str) else str(content)
        text = (f"{tag(self.name, ctx.work_item.phase + 1, self.phases()[ctx.work_item.phase].title)} "
                f"Approval needed — {kind.value} (can approve: {roles}):\n{shown}\n"
                "Reply \"approved\" or use the approve button.")
        return InternalResult(ok=True, end_turn=True, display=text,
                              content=f"Approval '{kind.value}' requested (hash {ap.artifact_hash[:8]}). "
                                      "Stop and wait for the human.")

    # helpers used by subclasses
    @staticmethod
    def hash_of(obj: Any) -> str:
        return canonical_hash(obj)
