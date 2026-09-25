import socket
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from supdev.adapters.mcp_bridge import McpAdapter, list_remote_tools
from supdev.api.app import create_app
from supdev.auth.token import TokenAuth, create_token
from supdev.core.audit import MemoryAuditSink
from supdev.core.crypto import Crypto
from supdev.core.db import Database
from supdev.core.errors import PolicyViolation, SupdevError
from supdev.core.models import Role
from supdev.core.redaction import RegexRedactor
from supdev.core.runtime import AgentRuntime
from supdev.llm.fake import FakeLLM
from supdev.plugins.registry import default_registry
from supdev.secrets.db import DbSecretStore
from supdev.settings.store import SettingsStore, TenantDoc
from supdev.storage.memory import MemorySessionStore

SECRET = "jira-super-secret-token-123"
ECHO = str(Path(__file__).resolve().parents[1] / "fixtures" / "echo_mcp.py")


class World:
    def __init__(self):
        self.db = Database()
        self.crypto = Crypto(Fernet.generate_key())
        self.secrets = DbSecretStore(self.db, self.crypto)
        self.store = SettingsStore(self.db)
        self.audit = MemoryAuditSink()
        self.rt = AgentRuntime(default_registry(), llm=FakeLLM([]), store=MemorySessionStore(), audit=self.audit,
                               secrets=self.secrets, redactor=RegexRedactor(), settings=self.store)
        for t in ("acme", "globex"):
            self.store.create_if_absent(TenantDoc(id=t, name=t.title()))
        self.tokens = {}
        for tenant, role in (("acme", "admin"), ("acme", "developer"), ("acme", "lead"), ("globex", "admin")):
            _, tok = create_token(self.db, tenant, f"{role}-user", Role(role))
            self.tokens[(tenant, role)] = {"Authorization": f"Bearer {tok}"}
        self.client = TestClient(create_app(self.rt, TokenAuth(self.db)))

    def h(self, role="admin", tenant="acme"):
        return self.tokens[(tenant, role)]


@pytest.fixture
def w(monkeypatch):
    monkeypatch.setenv("SUPDEV_ALLOW_PRIVATE_URLS", "1")   # test hosts don't resolve; SSRF tests turn it off
    monkeypatch.setenv("SUPDEV_ALLOW_HTTP", "1")
    return World()


JIRA = {"config": {"base_url": "https://acme.atlassian.example", "project": "OPS"},
        "secrets": {"token": SECRET, "email": "me@acme.example"}}


# ---- auth / RBAC -------------------------------------------------------------------------
def test_token_auth_and_rbac(w):
    assert w.client.get("/api/admin/config").status_code == 401
    assert w.client.get("/api/auth/mode").json() == {"mode": "token"}
    assert w.client.get("/api/admin/config", headers=w.h("developer")).status_code == 403
    assert w.client.get("/api/admin/config", headers=w.h("lead")).status_code == 200        # read-only for leads
    assert w.client.put("/api/admin/integrations/jira", headers=w.h("lead"), json=JIRA).status_code == 403
    assert w.client.put("/api/admin/integrations/jira", headers=w.h("admin"), json=JIRA).status_code == 200


def test_token_lifecycle_hashed_shown_once_revocable(w):
    r = w.client.post("/api/admin/tokens", headers=w.h(), json={"user_id": "bob", "role": "developer"}).json()
    h = {"Authorization": f"Bearer {r['token']}"}
    assert w.client.get("/api/me", headers=h).json()["role"] == "developer"
    assert r["token"] not in " ".join(str(dict(x)) for x in w.db.all("SELECT * FROM api_tokens"))  # only the hash
    assert w.client.get("/api/admin/tokens", headers=w.h()).status_code == 200
    w.client.delete(f"/api/admin/tokens/{r['id']}", headers=w.h())
    assert w.client.get("/api/me", headers=h).status_code == 401


# ---- secrets are write-only + encrypted -----------------------------------------------------
def test_secrets_write_only_encrypted_and_never_audited(w):
    r = w.client.put("/api/admin/integrations/jira", headers=w.h(), json=JIRA)
    assert r.status_code == 200
    cfg = w.client.get("/api/admin/config", headers=w.h()).text
    assert SECRET not in cfg and "jira_token" in cfg                       # names yes, values never
    assert SECRET not in r.text
    row = w.db.one("SELECT ciphertext FROM secrets WHERE name='jira_token'")
    assert SECRET not in row["ciphertext"]                                  # encrypted at rest
    assert w.secrets.get("acme", "jira_token") == SECRET                    # adapters can still read it
    assert SECRET not in " ".join(x.model_dump_json() for x in w.audit.records)
    assert any(x.kind == "admin_config" and x.action == "integration.update" for x in w.audit.records)
    with pytest.raises(SupdevError):
        w.crypto.decrypt("globex", "jira_token", row["ciphertext"])         # bound to tenant+name
    assert w.rt.redactor.redact(f"leak {SECRET}")[0] == "leak ****"        # learned as a known secret


def test_blank_secret_keeps_existing_and_reports_missing(w):
    body = {"config": JIRA["config"], "secrets": {"token": SECRET}}
    j = w.client.put("/api/admin/integrations/jira", headers=w.h(), json=body).json()
    assert j["integrations"]["jira"]["missing_secrets"] == ["jira_email"]
    w.client.put("/api/admin/integrations/jira", headers=w.h(), json={"config": JIRA["config"], "secrets": {"token": ""}})
    assert w.secrets.get("acme", "jira_token") == SECRET


# ---- hot reload + isolation --------------------------------------------------------------
def test_saved_integration_reaches_runtime_without_restart(w):
    t = w.rt.tenant_by_id("acme")
    assert w.rt.adapters_for(t) == []
    w.client.put("/api/admin/integrations/jira", headers=w.h(), json=JIRA)
    t = w.rt.tenant_by_id("acme")
    assert {tt.name for a in w.rt.adapters_for(t) for tt in a.tools()} >= {"ticketing.get_item", "ticketing.add_comment"}
    w.client.delete("/api/admin/integrations/jira", headers=w.h())
    assert w.rt.adapters_for(w.rt.tenant_by_id("acme")) == []


def test_tenant_admin_cannot_touch_another_tenant(w):
    w.client.put("/api/admin/integrations/jira", headers=w.h(), json=JIRA)
    other = w.client.get("/api/admin/config", headers=w.h("admin", "globex")).json()
    assert other["integrations"] == {} and other["tenant"]["id"] == "globex"
    assert w.secrets.names("globex") == {}


def test_policy_from_settings_can_only_tighten(w):
    r = w.client.put("/api/admin/policy", headers=w.h(), json={
        "denied_tools": ["source_control.commit"], "budget": {"tool_calls": 1_000_000}, "max_plan_iterations": 99})
    assert r.status_code == 200
    eff = w.rt.policy_for(w.rt.tenant_by_id("acme"))
    assert "source_control.commit" in eff.denied_tools and eff.budget["tool_calls"] == 60 and eff.max_plan_iterations == 3
    assert w.client.put("/api/admin/policy", headers=w.h(), json={"enabled_modes": ["nope"]}).status_code == 400


# ---- LLM per tenant ---------------------------------------------------------------------------
def test_per_tenant_llm_key_is_used_and_never_falls_back_silently(w):
    assert w.rt.llm_for(w.rt.tenant_by_id("acme")) is w.rt.llm              # default: platform LLM
    r = w.client.put("/api/admin/llm", headers=w.h(), json={"provider": "fake"})
    assert r.status_code == 400                                             # key required first time
    r = w.client.put("/api/admin/llm", headers=w.h(), json={"provider": "fake", "model": "m1", "api_key": "sk-tenant-key-abcdef123456"})
    assert r.status_code == 200 and r.json()["llm_key_configured"] is True and "sk-tenant" not in r.text
    llm = w.rt.llm_for(w.rt.tenant_by_id("acme"))
    assert llm.api_key == "sk-tenant-key-abcdef123456" and llm.model == "m1"
    assert w.client.post("/api/admin/llm/test", headers=w.h()).json()["ok"] is True
    w.client.delete("/api/admin/llm/key", headers=w.h())
    assert w.rt.llm_for(w.rt.tenant_by_id("acme")) is w.rt.llm              # reset to default
    w.store.update("acme", lambda d: setattr(d.llm, "provider", "fake"))    # provider set but key gone
    with pytest.raises(PolicyViolation, match="not configured"):
        w.rt.llm_for(w.rt.tenant_by_id("acme"))


# ---- SSRF / operator flags --------------------------------------------------------------------
def test_ssrf_guard_blocks_internal_addresses(w, monkeypatch):
    monkeypatch.delenv("SUPDEV_ALLOW_PRIVATE_URLS")
    monkeypatch.delenv("SUPDEV_ALLOW_HTTP")
    for url in ("https://127.0.0.1", "https://169.254.169.254", "https://10.0.0.5", "http://8.8.8.8"):
        r = w.client.put("/api/admin/integrations/jira", headers=w.h(), json={"config": {"base_url": url}, "secrets": {}})
        assert r.status_code == 400, url
    ok = w.client.put("/api/admin/integrations/jira", headers=w.h(),
                      json={"config": {"base_url": "https://8.8.8.8"}, "secrets": {}})
    assert ok.status_code == 200                                            # public IP literal passes offline
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.1.2.3", 443))])
    bad = w.client.put("/api/admin/integrations/jira", headers=w.h(),
                       json={"config": {"base_url": "https://looks-public.example"}, "secrets": {}})
    assert bad.status_code == 400 and "non-public" in bad.text             # DNS resolving to private is caught


def test_code_execution_features_are_off_unless_operator_enables(w, monkeypatch):
    srv = {"name": "x", "command": "python", "args": [ECHO], "tools": {}}
    r = w.client.put("/api/admin/integrations/mcp", headers=w.h(), json={"config": {"servers": [srv]}})
    assert r.status_code == 400 and "SUPDEV_ALLOW_STDIO_MCP" in r.text
    r = w.client.put("/api/admin/integrations/local_exec", headers=w.h(), json={"config": {"commands": {"tests": "pytest"}}})
    assert r.status_code == 400 and "SUPDEV_ALLOW_LOCAL_EXEC" in r.text
    monkeypatch.setenv("SUPDEV_ALLOW_STDIO_MCP", "1")
    monkeypatch.setenv("SUPDEV_MCP_ALLOWED_COMMANDS", "uvx")
    r = w.client.put("/api/admin/integrations/mcp", headers=w.h(), json={"config": {"servers": [srv]}})
    assert r.status_code == 400 and "not in SUPDEV_MCP_ALLOWED_COMMANDS" in r.text
    r = w.client.put("/api/admin/integrations/github", headers=w.h(), json={"config": {"repo": "a/b", "workdir": "/etc"}})
    assert r.status_code == 400 and "managed by the platform" in r.text


def test_mcp_tag_rules_prod_read_only_and_writes_need_gate(w, monkeypatch):
    monkeypatch.setenv("SUPDEV_ALLOW_STDIO_MCP", "1")
    monkeypatch.setenv("SUPDEV_MCP_ALLOWED_COMMANDS", "python")
    def put(tool):
        srv = {"name": "x", "command": "python", "args": [ECHO], "tools": {"t": tool}}
        return w.client.put("/api/admin/integrations/mcp", headers=w.h(), json={"config": {"servers": [srv]}})
    assert "read-only" in put({"capability": "cluster", "access": "update", "environment": "prod"}).text
    assert "approval_kind" in put({"capability": "ticketing", "access": "comment"}).text
    assert put({"capability": "bogus"}).status_code == 400
    assert put({"capability": "logs", "access": "read", "environment": "prod", "bounded": True}).status_code == 200


# ---- MCP for real (stdio server) --------------------------------------------------------------
async def test_mcp_bridge_end_to_end_against_a_real_server(w, monkeypatch):
    srv = {"name": "echo", "command": sys.executable, "args": [ECHO]}
    tools = await list_remote_tools("acme", w.secrets, srv)
    assert {t["name"] for t in tools} == {"echo", "dangerous"}
    ad = McpAdapter("acme", w.secrets, {**srv, "tools": {"echo": {"capability": "logs", "access": "read",
                                                                     "environment": "staging", "bounded": True}}})
    assert [t.name for t in ad.tools()] == ["logs.echo.staging"]           # 'dangerous' is untagged => does not exist
    assert await ad.call("logs.echo.staging", {"text": "hi"}) == "echo: hi"
    assert "connected" in await ad.ping()


def test_discover_endpoint_lists_tools_but_exposes_nothing(w, monkeypatch):
    monkeypatch.setenv("SUPDEV_ALLOW_STDIO_MCP", "1")
    monkeypatch.setenv("SUPDEV_MCP_ALLOWED_COMMANDS", sys.executable)
    r = w.client.post("/api/admin/mcp/discover", headers=w.h(), json={"server": {
        "name": "echo", "command": sys.executable, "args": [ECHO]}})
    assert r.status_code == 200 and {t["name"] for t in r.json()["tools"]} == {"echo", "dangerous"}
    assert w.store.get("acme").integrations == {}                           # nothing saved / exposed


# ---- bootstrap -------------------------------------------------------------------------------
def test_bootstrap_token_and_tenant_from_env(tmp_path, monkeypatch):
    from supdev.api.config import build_runtime
    monkeypatch.setenv("SUPDEV_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("SUPDEV_AUDIT_PATH", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("SUPDEV_MASTER_KEY_FILE", str(tmp_path / "k"))
    monkeypatch.setenv("SUPDEV_AUTH", "token")
    monkeypatch.setenv("SUPDEV_BOOTSTRAP_TENANT", "acme")
    monkeypatch.setenv("SUPDEV_BOOTSTRAP_TOKEN", "boot-token-0123456789")
    rt, auth = build_runtime()
    p = auth.authenticate({"Authorization": "Bearer boot-token-0123456789"})
    assert (p.tenant_id, p.role) == ("acme", Role.ADMIN) and rt.tenant_by_id("acme").name == "Acme"
    rt.secrets.set("acme", "x", "some-secret-value")                        # first secret creates the key lazily
    assert oct((tmp_path / "k").stat().st_mode & 0o777) == "0o600"          # generated key file is private
