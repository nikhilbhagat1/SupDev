
import pytest
from fastapi.testclient import TestClient

from supdev.api.app import create_app
from supdev.llm.fake import call, say

from ..conftest import Env

H = {"X-Tenant-Id": "acme", "X-User-Id": "u1", "X-Role": "lead"}


@pytest.fixture
def client_env():
    e = Env([call("engine.set_requirements", items=[dict(id="F1", area="functional", text="x", status="clear")]),
             say("ok")])
    return TestClient(create_app(e.rt, e.rt.registry.get(__import__("supdev.plugins.base", fromlist=["x"]).PluginKind.AUTH, "dev-header"))), e


def test_auth_required_and_static_index(client_env):
    c, _ = client_env
    assert c.post("/api/sessions").status_code == 401
    assert "Supdev" in c.get("/").text and c.get("/static/app.js").status_code == 200


def test_session_is_tenant_isolated(client_env):
    c, _ = client_env
    sid = c.post("/api/sessions", headers=H).json()["id"]
    other = {**H, "X-Tenant-Id": "globex"}
    assert c.get(f"/api/sessions/{sid}", headers=other).status_code == 404
    assert c.get(f"/api/sessions/{sid}", headers=H).status_code == 200


def test_message_stream_and_state(client_env):
    c, e = client_env
    sid = c.post("/api/sessions", headers=H).json()["id"]
    with c.stream("POST", f"/api/sessions/{sid}/messages", headers=H,
                  json={"text": "Implement export", "ticket_type": "story"}) as r:
        body = "".join(r.iter_text())
    assert "event: mode" in body and "event: done" in body
    v = c.get(f"/api/sessions/{sid}", headers=H).json()
    assert v["active"]["mode"] == "dev" and v["active"]["requirements"][0]["id"] == "F1"


def test_viewer_cannot_approve_and_gets_roles(client_env):
    c, e = client_env
    sid = c.post("/api/sessions", headers=H).json()["id"]
    c.post(f"/api/sessions/{sid}/mode", headers=H, json={"mode": "dev"})
    s = e.rt.get_session(e.p, sid)
    from supdev.core.approvals import ApprovalService
    from supdev.core.models import ApprovalKind
    ap = ApprovalService(e.rt.policy_for(e.rt._tenant(e.p))).request(s.active, ApprovalKind.PLAN, {"v": 1})
    e.rt.store.save(s)
    r = c.post(f"/api/sessions/{sid}/approvals/{ap.id}/grant", headers={**H, "X-Role": "viewer"}, json={})
    assert r.status_code == 403 and "developer" in r.json()["detail"]
    r = c.post(f"/api/sessions/{sid}/approvals/{ap.id}/grant", headers={**H, "X-Role": "developer"},
               json={"seen_hash": ap.artifact_hash})
    assert r.status_code == 200


def test_board_per_mode_columns_and_cards(client_env):
    c, e = client_env
    sid = c.post("/api/sessions", headers=H).json()["id"]
    idle = c.post("/api/sessions", headers=H).json()["id"]                  # never routed -> backlog on both boards
    c.post(f"/api/sessions/{sid}/mode", headers=H, json={"mode": "dev", "title": "Export CSV", "ref": "T-1"})
    dev = c.get("/api/board?mode=dev", headers=H).json()
    sup = c.get("/api/board?mode=support", headers=H).json()
    assert dev["phases"][:2] == ["Intake & Analysis", "Clarification"] and len(dev["phases"]) == 7
    assert sup["phases"][0] == "Triage" and len(sup["phases"]) == 7        # each mode has its own workflow columns
    assert [x["title"] for x in dev["cards"]] == ["Export CSV"] and dev["cards"][0]["phase"] == 1
    assert sup["cards"] == [] and idle in {b["session_id"] for b in dev["backlog"]}
    # parking it by starting a support item keeps the dev card on the dev board (as parked)
    c.post(f"/api/sessions/{sid}/mode", headers=H, json={"mode": "support", "title": "Prod down"})
    dev, sup = (c.get(f"/api/board?mode={m}", headers=H).json() for m in ("dev", "support"))
    assert dev["cards"][0]["status"] == "parked" and sup["cards"][0]["status"] == "active"
    assert c.get("/api/board?mode=nope", headers=H).status_code == 404
    other = c.get("/api/board?mode=dev", headers={**H, "X-Tenant-Id": "globex"}).json()
    assert other["cards"] == [] and other["backlog"] == []                  # tenant-scoped


def test_releases_endpoint_states(client_env, monkeypatch):
    import httpx
    import respx

    from supdev.adapters.jira import JiraFactory
    from supdev.plugins.base import TenantConfig
    from supdev.secrets.env import EnvSecretStore

    monkeypatch.setenv("SUPDEV_ALLOW_PRIVATE_URLS", "1")
    monkeypatch.setenv("SUPDEV_ALLOW_HTTP", "1")
    c, e = client_env
    assert c.get("/api/releases", headers=H).json() == {"configured": False, "releases": []}     # no Jira connected
    jira = JiraFactory().create(TenantConfig("acme", {"jira": {"base_url": "https://j.test", "project": "OPS"}}),
                                EnvSecretStore({("acme", "jira_token"): "t", ("acme", "jira_email"): "a@b.c"}))[0]
    e.adapters.append(jira)
    with respx.mock:
        route = respx.get("https://j.test/rest/api/3/project/OPS/versions").mock(return_value=httpx.Response(200, json=[
            {"id": "2", "name": "2.0", "released": False, "releaseDate": "2026-12-01",
             "description": "ships with key ghp_" "abcdefghijklmnopqrstuvwx"}]))
        respx.get("https://j.test/rest/api/3/version/2/unresolvedIssueCount").mock(
            return_value=httpx.Response(200, json={"issuesCount": 4, "issuesUnresolvedCount": 1}))
        r = c.get("/api/releases", headers=H).json()
        assert r["project"] == "OPS" and r["releases"][0]["done"] == 3 and "ghp_" not in str(r)   # redacted
        c.get("/api/releases", headers=H)
        assert route.call_count == 1                                                            # cached 60s
        respx.get("https://j.test/rest/api/3/search/jql").mock(return_value=httpx.Response(200, json={
            "issues": [{"key": "OPS-1"}]}))
        assert c.get("/api/releases/2/issues", headers=H).json()["keys"] == ["OPS-1"]
        assert c.get("/api/releases/2%20OR%201=1/issues", headers=H).status_code == 400
        respx.get("https://j.test/rest/api/3/project/BAD/versions").mock(return_value=httpx.Response(404, text="nope"))
        assert c.get("/api/releases?project=BAD", headers=H).status_code == 502
