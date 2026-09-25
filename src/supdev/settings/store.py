"""Per-tenant configuration document (JSON, versioned) in SQLite. Secrets are NOT in here — only their names."""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from ..core.db import Database
from ..core.models import now
from ..core.policy import Policy
from ..core.runtime import TenantSetup


class PolicyDoc(BaseModel):
    denied_tools: list[str] = Field(default_factory=list)
    denied_access: list[str] = Field(default_factory=list)
    allowed_environments: list[str] | None = None
    enabled_modes: list[str] | None = None
    budget: dict[str, int] = Field(default_factory=dict)
    max_plan_iterations: int | None = None
    max_hypothesis_rounds: int | None = None
    default_lookback: str | None = None
    free_text: str = ""


class LLMDoc(BaseModel):
    provider: str | None = None  # None => platform default
    model: str | None = None


class TenantDoc(BaseModel):
    id: str
    name: str = ""
    environments: list[str] = Field(default_factory=lambda: ["dev", "staging"])
    timezone: str = "UTC"
    repo_context: str = ""
    conventions: str = ""
    service_catalog: str = ""
    policy: PolicyDoc = Field(default_factory=PolicyDoc)
    llm: LLMDoc = Field(default_factory=LLMDoc)
    integrations: dict[str, dict[str, Any]] = Field(default_factory=dict)
    version: int = 0


def policy_from_doc(d: PolicyDoc) -> Policy:
    p = Policy()
    p.denied_tools = set(d.denied_tools)
    p.denied_access = set(d.denied_access)  # type: ignore[arg-type]
    p.allowed_environments = set(d.allowed_environments) if d.allowed_environments is not None else None
    p.enabled_modes = set(d.enabled_modes) if d.enabled_modes is not None else None
    p.budget.update(d.budget)
    for k in ("max_plan_iterations", "max_hypothesis_rounds", "default_lookback"):
        if getattr(d, k) is not None:
            setattr(p, k, getattr(d, k))
    p.free_text = d.free_text
    return p


class SettingsStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._lock = threading.Lock()

    def get(self, tenant_id: str) -> TenantDoc | None:
        r = self.db.one("SELECT body, version FROM tenant_config WHERE tenant_id=?", (tenant_id,))
        if r is None:
            return None
        d = TenantDoc.model_validate_json(r["body"])
        d.version = r["version"]
        return d

    def version(self, tenant_id: str) -> int | None:
        r = self.db.one("SELECT version FROM tenant_config WHERE tenant_id=?", (tenant_id,))
        return None if r is None else int(r["version"])

    def ids(self) -> list[str]:
        return [r["tenant_id"] for r in self.db.all("SELECT tenant_id FROM tenant_config")]

    def create_if_absent(self, doc: TenantDoc) -> bool:
        with self._lock:
            if self.get(doc.id) is not None:
                return False
            self._write(doc, 1)
            return True

    def update(self, tenant_id: str, fn: Callable[[TenantDoc], None]) -> TenantDoc:
        """Atomic read-modify-write; bumps the version so running runtimes reload the tenant."""
        with self._lock:
            doc = self.get(tenant_id)
            if doc is None:
                raise KeyError(tenant_id)
            fn(doc)
            self._write(doc, doc.version + 1)
            doc.version += 1
            return doc

    def _write(self, doc: TenantDoc, version: int) -> None:
        self.db.exec("INSERT INTO tenant_config (tenant_id, body, version, updated_at) VALUES (?,?,?,?) "
                     "ON CONFLICT(tenant_id) DO UPDATE SET body=excluded.body, version=excluded.version, "
                     "updated_at=excluded.updated_at",
                     (doc.id, doc.model_dump_json(exclude={"version"}), version, now()))

    def load_setup(self, tenant_id: str) -> TenantSetup | None:
        d = self.get(tenant_id)
        if d is None:
            return None
        return TenantSetup(
            tenant_id=d.id, name=d.name or d.id, policy=policy_from_doc(d.policy), integrations=d.integrations,
            environments=d.environments, timezone=d.timezone, repo_context=d.repo_context,
            conventions=d.conventions, service_catalog=d.service_catalog, version=d.version,
            llm=d.llm.model_dump(exclude_none=True))
