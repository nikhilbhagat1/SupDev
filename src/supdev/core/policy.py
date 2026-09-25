"""Platform + tenant policy (A2). Tenant policy may only tighten the platform baseline."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .models import Access, ApprovalKind, Role, ToolSpec

_DEV = {Role.DEVELOPER, Role.LEAD, Role.ADMIN}
_OPS = {Role.SRE, Role.LEAD, Role.ADMIN}
_ANY = {Role.DEVELOPER, Role.SRE, Role.LEAD, Role.ADMIN}
_SENIOR = {Role.LEAD, Role.ADMIN}

DEFAULT_APPROVER_ROLES: dict[ApprovalKind, set[Role]] = {
    ApprovalKind.START_PLANNING: _DEV,
    ApprovalKind.PLAN: _DEV,
    ApprovalKind.PR: _DEV,
    ApprovalKind.CONTINUE_PLAN_ITERATIONS: _DEV,
    ApprovalKind.ACCEPT_RCA: _OPS,
    ApprovalKind.RCA_COMMENT: _OPS,
    ApprovalKind.POSTMORTEM: _OPS,
    ApprovalKind.FOLLOWUP_TICKETS: _OPS,
    ApprovalKind.TICKET_COMMENT: _ANY,
    ApprovalKind.TICKET_STATUS: _ANY,
    ApprovalKind.HANDOFF: _ANY,
    ApprovalKind.MODE_SWITCH: _ANY,
    ApprovalKind.PARK_WORK_ITEM: _ANY,
    ApprovalKind.MERGE: _SENIOR,
    ApprovalKind.DESTRUCTIVE: _SENIOR,
    ApprovalKind.DEPENDENCY: _SENIOR,
    ApprovalKind.CI_CHANGE: _SENIOR,
    ApprovalKind.DEPLOY: _SENIOR,
}

DEFAULT_BUDGET = {"tool_calls": 60, "query_units": 40, "tokens": 400_000}


class Policy(BaseModel):
    """Effective policy. Every field can only be narrowed by `merge`."""

    approver_roles: dict[ApprovalKind, set[Role]] = Field(
        default_factory=lambda: {k: set(v) for k, v in DEFAULT_APPROVER_ROLES.items()}
    )
    denied_tools: set[str] = Field(default_factory=set)
    denied_access: set[Access] = Field(default_factory=set)
    allowed_environments: set[str] | None = None  # None = no restriction
    enabled_modes: set[str] | None = None
    budget: dict[str, int] = Field(default_factory=lambda: dict(DEFAULT_BUDGET))
    max_plan_iterations: int = 3
    max_hypothesis_rounds: int = 3
    default_lookback: str = "2h"
    free_text: str = ""  # tenant admin prose, shown to the model; carries no authority by itself

    def merge(self, tenant: Policy) -> Policy:
        """Return base ∩ tenant. Never loosens: sets shrink, denies grow, limits take the min."""
        roles = {
            k: self.approver_roles.get(k, set()) & tenant.approver_roles.get(k, set())
            for k in self.approver_roles
        }
        envs: set[str] | None
        if self.allowed_environments is None:
            envs = tenant.allowed_environments
        elif tenant.allowed_environments is None:
            envs = self.allowed_environments
        else:
            envs = self.allowed_environments & tenant.allowed_environments
        modes: set[str] | None
        if self.enabled_modes is None:
            modes = tenant.enabled_modes
        elif tenant.enabled_modes is None:
            modes = self.enabled_modes
        else:
            modes = self.enabled_modes & tenant.enabled_modes
        return Policy(
            approver_roles=roles,
            denied_tools=self.denied_tools | tenant.denied_tools,
            denied_access=self.denied_access | tenant.denied_access,
            allowed_environments=envs,
            enabled_modes=modes,
            budget={k: min(v, tenant.budget.get(k, v)) for k, v in self.budget.items()},
            max_plan_iterations=min(self.max_plan_iterations, tenant.max_plan_iterations),
            max_hypothesis_rounds=min(self.max_hypothesis_rounds, tenant.max_hypothesis_rounds),
            default_lookback=tenant.default_lookback or self.default_lookback,
            free_text=tenant.free_text,
        )

    def tool_allowed(self, spec: ToolSpec) -> str | None:
        """Return a denial reason, or None if the tenant policy permits offering this tool."""
        if spec.name in self.denied_tools:
            return f"tool '{spec.name}' is denied by tenant policy"
        if spec.access in self.denied_access:
            return f"access level '{spec.access.value}' is denied by tenant policy"
        if (
            spec.environment
            and self.allowed_environments is not None
            and spec.environment not in self.allowed_environments
        ):
            return f"environment '{spec.environment}' is not accessible for this tenant"
        return None

    def can_approve(self, role: Role, kind: ApprovalKind) -> bool:
        return role in self.approver_roles.get(kind, set())

    def roles_for(self, kind: ApprovalKind) -> list[str]:
        return sorted(r.value for r in self.approver_roles.get(kind, set()))
