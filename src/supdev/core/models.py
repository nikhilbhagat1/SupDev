"""Shared data models. Everything persisted or passed between layers lives here."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def now() -> float:
    return time.time()


def canonical_hash(obj: Any) -> str:
    """Stable hash of any JSON-able object; used to bind approvals to exact artifacts."""
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


class Role(StrEnum):
    VIEWER = "viewer"
    DEVELOPER = "developer"
    SRE = "sre"
    LEAD = "lead"
    ADMIN = "admin"


class Principal(BaseModel):
    tenant_id: str
    user_id: str
    role: Role


class Access(StrEnum):
    READ = "read"
    COMMENT = "comment"
    UPDATE = "update"
    BRANCH = "branch"
    COMMIT = "commit"
    PUSH = "push"
    PR = "pr"
    MERGE = "merge"
    DELETE = "delete"
    EXECUTE = "execute"  # sandboxed test/lint runners in non-prod
    WRITE = "write"


class ApprovalKind(StrEnum):
    # DEV
    START_PLANNING = "start_planning"
    PLAN = "plan"
    PR = "pr"  # covers push + open PR of exactly the previewed change
    CONTINUE_PLAN_ITERATIONS = "continue_plan_iterations"
    # SUPPORT
    ACCEPT_RCA = "accept_rca"
    RCA_COMMENT = "rca_comment"
    POSTMORTEM = "postmortem"
    FOLLOWUP_TICKETS = "followup_tickets"
    # shared
    TICKET_COMMENT = "ticket_comment"
    TICKET_STATUS = "ticket_status"
    HANDOFF = "handoff"
    MODE_SWITCH = "mode_switch"
    PARK_WORK_ITEM = "park_work_item"
    # per-action guardrail approvals (DEV guardrails)
    MERGE = "merge"
    DESTRUCTIVE = "destructive"  # delete branch/tag, force-push, rewrite history
    DEPENDENCY = "dependency"
    CI_CHANGE = "ci_change"
    DEPLOY = "deploy"


class ToolSpec(BaseModel):
    """A tool exposed to the model. Capability/access/environment tags come from the adapter."""

    name: str  # globally unique, e.g. "github.create_branch"
    description: str
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    capability: str  # ticketing, source_control, metrics, logs, cluster, design, traces, execution
    vendor: str = "unknown"
    access: Access = Access.READ
    environment: str | None = None  # None = not environment-scoped; "prod" => hard read-only
    approval_kind: ApprovalKind | None = None  # gate required before this tool may run
    artifact_arg: str | None = None  # arg whose value is hashed against the approval
    bounded: bool = False  # query tool: must carry `time_range` and a scope
    internal: bool = False  # engine-native tool (no adapter)

    @property
    def is_read(self) -> bool:
        return self.access == Access.READ

    def tag(self) -> str:
        env = f" ({self.environment})" if self.environment else ""
        return f"[{self.capability}] {self.vendor} — {self.access.value}{env}"


class ToolCall(BaseModel):
    id: str = Field(default_factory=lambda: new_id("tc_"))
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    call_id: str
    name: str
    ok: bool
    content: str  # what the model sees (already redacted + wrapped if external)
    end_turn: bool = False
    display: str | None = None
    flags: list[str] = Field(default_factory=list)


class Message(BaseModel):
    role: str  # user | assistant | tool | system_note
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None
    ts: float = Field(default_factory=now)


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class LLMResponse(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)


# ------------------------------------------------------------------ work-item state


class ReqStatus(StrEnum):
    CLEAR = "clear"
    ASSUMED = "assumed"
    AMBIGUOUS = "ambiguous"


class Requirement(BaseModel):
    id: str
    area: str  # functional | acceptance | non_functional | constraint | out_of_scope
    text: str
    status: ReqStatus
    resolved_by_message: int | None = None  # index of the user message that resolved it
    accepted_assumption: bool = False

    @property
    def blocking(self) -> bool:
        return self.status == ReqStatus.AMBIGUOUS or (
            self.status == ReqStatus.ASSUMED and not self.accepted_assumption
        )


class Approval(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ap_"))
    kind: ApprovalKind
    artifact_hash: str
    requested_at: float = Field(default_factory=now)
    granted_by: str | None = None
    granted_role: Role | None = None
    granted_at: float | None = None
    consumed: bool = False

    @property
    def granted(self) -> bool:
        return self.granted_by is not None


class Evidence(BaseModel):
    id: str
    tool_call_id: str
    source_tool: str
    query: dict[str, Any]
    time_range: str | None
    finding: str


class Hypothesis(BaseModel):
    id: str
    statement: str
    supporting: list[str] = Field(default_factory=list)  # evidence ids
    contradicting: list[str] = Field(default_factory=list)
    next_query: str = ""
    confidence: str = "low"  # high | medium | low


class ToolCallLogEntry(BaseModel):
    call_id: str
    tool: str
    args: dict[str, Any]
    ok: bool
    capability: str = ""
    access: str = ""
    passed: bool | None = None  # for execution tools: did the run pass (from the adapter result)
    ts: float = Field(default_factory=now)


class WorkItem(BaseModel):
    id: str = Field(default_factory=lambda: new_id("wi_"))
    mode: str
    ref: str | None = None  # external ticket key
    title: str = ""
    phase: int = 0
    status: str = "active"  # active | parked | done
    artifacts: dict[str, Any] = Field(default_factory=dict)  # kind -> latest presented content
    approvals: list[Approval] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    scope: dict[str, Any] = Field(default_factory=dict)  # SUPPORT: service/env/window/...
    test_runs: list[dict[str, Any]] = Field(default_factory=list)
    counters: dict[str, int] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)  # handoff context (RCA summary etc.)
    call_log: list[ToolCallLogEntry] = Field(default_factory=list)


class Session(BaseModel):
    id: str = Field(default_factory=lambda: new_id("s_"))
    tenant_id: str
    user_id: str
    active: WorkItem | None = None
    parked: list[WorkItem] = Field(default_factory=list)
    messages: list[Message] = Field(default_factory=list)
    pending_router_question: bool = False
    pending_mode_hint: str | None = None
    budget_used: dict[str, int] = Field(default_factory=dict)
    budget_extra: dict[str, int] = Field(default_factory=dict)
    force_no_tools: bool = False
    created_at: float = Field(default_factory=now)

    def snapshot_for_prompt(self) -> dict[str, Any]:
        wi = self.active
        return {
            "mode": wi.mode if wi else None,
            "phase": wi.phase + 1 if wi else None,
            "work_item": wi.ref if wi else None,
            "approvals_granted": [a.kind.value for a in wi.approvals if a.granted and not a.consumed]
            if wi
            else [],
            "parked": [p.ref or p.id for p in self.parked],
        }
