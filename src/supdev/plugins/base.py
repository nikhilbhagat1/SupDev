"""Plugin contracts. Every extension point is a Protocol registered under an entry-point group."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ..core.models import (
    ApprovalKind,
    LLMResponse,
    Message,
    Principal,
    Session,
    ToolSpec,
    WorkItem,
)

if TYPE_CHECKING:
    from ..core.audit import AuditRecord
    from ..core.policy import Policy


class PluginKind(StrEnum):
    LLM = "supdev.llm"
    CAPABILITY = "supdev.capabilities"
    MODE = "supdev.modes"
    STORAGE = "supdev.storage"
    SECRETS = "supdev.secrets"
    AUDIT = "supdev.audit"
    AUTH = "supdev.auth"
    REDACTOR = "supdev.redactors"


# ------------------------------------------------------------------------------ LLM


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    async def complete(
        self, system: str, messages: list[Message], tools: list[ToolSpec]
    ) -> LLMResponse: ...


# ---------------------------------------------------------------------- integrations


@dataclass
class TenantConfig:
    """Everything a factory needs to build tenant-scoped adapters. Secrets are *names*, resolved
    by the platform SecretStore inside the adapter — never passed to the model."""

    tenant_id: str
    integrations: dict[str, dict[str, Any]] = field(default_factory=dict)  # name -> settings


@runtime_checkable
class SecretStore(Protocol):
    def get(self, tenant_id: str, name: str) -> str | None: ...


@runtime_checkable
class CapabilityAdapter(Protocol):
    """One vendor behind one capability, scoped to a single tenant (A3)."""

    capability: str
    vendor: str

    def tools(self) -> list[ToolSpec]: ...

    async def call(self, tool: str, args: dict[str, Any]) -> Any: ...


@runtime_checkable
class CapabilityAdapterFactory(Protocol):
    """Registered plugin object. Builds adapters for a tenant from its integration settings."""

    def create(
        self, tenant: TenantConfig, secrets: SecretStore
    ) -> list[CapabilityAdapter]: ...


# -------------------------------------------------------------------------- modes


ToolHandler = Callable[["ModeContext", dict[str, Any]], Awaitable["InternalResult"]]


@dataclass
class InternalResult:
    ok: bool
    content: str
    end_turn: bool = False
    display: str | None = None  # shown to the user as the assistant message (rendered templates)


@dataclass
class ModeContext:
    session: Session
    work_item: WorkItem
    principal: Principal
    policy: Policy
    approvals: Any  # ApprovalService (avoid import cycle)
    emit: Callable[[str, dict[str, Any]], None]


@dataclass
class ExitResult:
    ok: bool
    reason: str = ""


@dataclass
class PhaseSpec:
    key: str
    title: str
    exit_check: Callable[[ModeContext], ExitResult]


@dataclass
class Denial:
    reason: str


@runtime_checkable
class Mode(Protocol):
    name: str
    prompt_file: str  # filename within supdev/prompts
    router_signals: list[str]  # lower-case substrings that suggest this mode
    ticket_types: set[str]

    def phases(self) -> list[PhaseSpec]: ...

    def internal_tools(self) -> list[tuple[ToolSpec, ToolHandler]]: ...

    def check_tool(self, ctx: ModeContext, spec: ToolSpec, args: dict[str, Any]) -> Denial | None:
        """Mode-specific guardrails applied by the ToolGateway before any approval check."""
        ...

    def approvals_for_phase(self, phase: int) -> set[ApprovalKind]: ...


# ------------------------------------------------------------------- infrastructure


@runtime_checkable
class SessionStore(Protocol):
    """All access is tenant-scoped: a mismatched tenant behaves exactly like 'not found'."""

    def save(self, session: Session) -> None: ...

    def get(self, tenant_id: str, session_id: str) -> Session | None: ...

    def list(self, tenant_id: str, user_id: str | None = None) -> list[Session]: ...


@runtime_checkable
class AuditSink(Protocol):
    def write(self, rec: AuditRecord) -> None: ...

    def query(self, tenant_id: str, session_id: str | None = None) -> list[AuditRecord]: ...


@runtime_checkable
class Authenticator(Protocol):
    def authenticate(self, headers: dict[str, str]) -> Principal: ...


@runtime_checkable
class Redactor(Protocol):
    def redact(self, text: str) -> tuple[str, list[str]]: ...

    def redact_obj(self, obj: Any) -> tuple[Any, list[str]]: ...

    def add_known_secret(self, value: str) -> None: ...
