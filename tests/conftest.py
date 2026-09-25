from __future__ import annotations

import pytest

from supdev.adapters.fake import (
    fake_docs,
    fake_execution,
    fake_observability,
    fake_source_control,
    fake_ticketing,
)
from supdev.core.audit import MemoryAuditSink
from supdev.core.events import ListSink
from supdev.core.models import Principal, Role
from supdev.core.redaction import RegexRedactor
from supdev.core.runtime import AgentRuntime, TenantSetup
from supdev.llm.fake import FakeLLM
from supdev.plugins.registry import default_registry
from supdev.secrets.env import EnvSecretStore
from supdev.storage.memory import MemorySessionStore


def principal(role: Role = Role.LEAD, tenant: str = "acme", user: str = "u1") -> Principal:
    return Principal(tenant_id=tenant, user_id=user, role=role)


class Env:
    def __init__(self, script, adapters=None, envs=("dev", "staging"), tenant_policy=None, jira_sync=None):
        self.llm = FakeLLM(script)
        self.tk, self.sc, self.ex, self.docs = fake_ticketing({"T-1": {"key": "T-1", "type": "Story"}}), \
            fake_source_control(), fake_execution(), fake_docs()
        self.obs = fake_observability("prod")
        self.adapters = adapters if adapters is not None else [self.tk, self.sc, self.ex, self.docs, *self.obs]
        self.audit = MemoryAuditSink()
        self.rt = AgentRuntime(default_registry(), llm=self.llm, store=MemorySessionStore(),
                               audit=self.audit, secrets=EnvSecretStore(), redactor=RegexRedactor())
        kw = {"policy": tenant_policy} if tenant_policy else {}
        self.rt.add_tenant(TenantSetup("acme", "Acme", adapters=self.adapters, environments=list(envs), jira_sync=jira_sync or {}, **kw))
        self.rt.add_tenant(TenantSetup("globex", "Globex", adapters=[], environments=["dev"]))
        self.p = principal()
        self.sid = self.rt.create_session(self.p).id

    async def say(self, text: str, p: Principal | None = None, **kw) -> ListSink:
        sink = ListSink()
        await self.rt.handle_message(p or self.p, self.sid, text, sink, **kw)
        return sink

    @property
    def session(self):
        return self.rt.get_session(self.p, self.sid)


@pytest.fixture
def env_factory():
    return Env
