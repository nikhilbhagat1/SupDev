import subprocess
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from supdev.adapters.github import GitHubAdapter
from supdev.adapters.jira import JiraFactory, adf_to_text
from supdev.adapters.local_exec import LocalExecAdapter
from supdev.adapters.mcp_bridge import McpFactory
from supdev.adapters.observability import LokiFactory, label_selector
from supdev.adapters.util import parse_time_range
from supdev.core.errors import SupdevError
from supdev.core.models import Access
from supdev.plugins.base import TenantConfig
from supdev.secrets.env import EnvSecretStore

SECRETS = EnvSecretStore({("t", "jira_token"): "jtok", ("t", "jira_email"): "a@b.c", ("t", "loki_tok"): "ltok"})


# ---- util ---------------------------------------------------------------------------------
def test_time_range_relative_iso_and_limits():
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    s, e = parse_time_range("2h", now)
    assert e - s == timedelta(hours=2)
    s, e = parse_time_range("2026-01-01T10:00:00Z/2026-01-01T11:00:00Z")
    assert e - s == timedelta(hours=1)
    with pytest.raises(SupdevError):
        parse_time_range("30d")
    with pytest.raises(SupdevError):
        parse_time_range("banana")


def test_label_selector_needs_scope_and_escapes():
    assert label_selector({"service": 'chk"out'}) == '{service="chkout"}'
    with pytest.raises(SupdevError):
        label_selector({})


# ---- jira ---------------------------------------------------------------------------------
@respx.mock
async def test_jira_get_item_and_comment():
    respx.get("https://j.test/rest/api/3/issue/T-1").mock(return_value=httpx.Response(200, json={
        "key": "T-1", "fields": {
            "summary": "Export", "issuetype": {"name": "Story"}, "status": {"name": "To Do"},
            "description": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "hello"}]}]},
            "comment": {"comments": [{"created": "x", "body": {"type": "doc", "content": []}}]},
            "issuelinks": [], "subtasks": []}}))
    post = respx.post("https://j.test/rest/api/3/issue/T-1/comment").mock(return_value=httpx.Response(201, json={"id": "9"}))
    ad = JiraFactory().create(TenantConfig("t", {"jira": {"base_url": "https://j.test"}}), SECRETS)[0]
    item = await ad.call("ticketing.get_item", {"key": "T-1"})
    assert item["type"] == "Story" and item["description"] == "hello"
    assert (await ad.call("ticketing.add_comment", {"key": "T-1", "body": "hi"}))["posted"]
    assert post.calls[0].request.headers["authorization"].startswith("Basic ")   # secret used internally
    assert adf_to_text({"type": "doc", "content": []}) == ""


async def test_missing_credential_is_a_clear_error():
    ad = JiraFactory().create(TenantConfig("nope", {"jira": {"base_url": "https://j.test"}}), EnvSecretStore())[0]
    with pytest.raises(SupdevError, match="not configured"):
        await ad.call("ticketing.get_item", {"key": "T-1"})


# ---- loki ---------------------------------------------------------------------------------
@respx.mock
async def test_loki_bounded_and_limited():
    route = respx.get("https://loki.test/loki/api/v1/query_range").mock(return_value=httpx.Response(200, json={
        "data": {"result": [{"values": [["1", "line a"], ["2", "line b"]]}]}}))
    ad = LokiFactory().create(TenantConfig("t", {"loki": {"url": "https://loki.test", "env": "prod", "secret": "loki_tok"}}), SECRETS)[0]
    assert ad.tools()[0].environment == "prod" and ad.tools()[0].access == Access.READ and ad.tools()[0].bounded
    out = await ad.call("logs.query.prod", {"service": "checkout", "time_range": "1h", "limit": 9999})
    assert out["count"] == 2
    assert route.calls[0].request.url.params["limit"] == "200"     # capped
    with pytest.raises(SupdevError):
        await ad.call("logs.query.prod", {"time_range": "1h"})      # no scope


# ---- git / github (real git, local bare remote) ------------------------------------------
def _sh(*a, cwd=None):
    subprocess.run(a, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    remote, seed = tmp_path / "remote.git", tmp_path / "seed"
    _sh("git", "init", "--bare", "-b", "main", str(remote))
    _sh("git", "init", "-b", "main", str(seed))
    (seed / "README.md").write_text("hello\n")
    for c in (["git", "add", "."], ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init"],
              ["git", "remote", "add", "origin", str(remote)], ["git", "push", "origin", "main"]):
        _sh(*c, cwd=seed)
    return remote, tmp_path / "work"


async def test_commits_stay_local_until_push_and_protected_refused(repo):
    remote, work = repo
    ad = GitHubAdapter(tenant_id="t", secrets=EnvSecretStore(), repo="o/r", workdir=work, clone_url=str(remote), secret=None)
    await ad.call("source_control.create_branch", {"branch": "feature/x"})
    r = await ad.call("source_control.commit", {"branch": "feature/x", "message": "add", "files": {"src/a.py": "x=1\n"}})
    assert r["pushed"] is False
    heads = lambda: subprocess.run(["git", "branch", "--list"], cwd=remote, capture_output=True, text=True).stdout  # noqa: E731
    assert "feature/x" not in heads()                                # nothing on the remote yet
    await ad.call("source_control.push", {"branch": "feature/x"})
    assert "feature/x" in heads()
    for bad in ("main", "release/1.0"):
        with pytest.raises(SupdevError, match="protected"):
            await ad.call("source_control.push", {"branch": bad})
    with pytest.raises(SupdevError, match="outside"):
        await ad.call("source_control.commit", {"branch": "feature/x", "message": "m", "files": {"../evil": "x"}})
    with pytest.raises(SupdevError, match="outside"):
        await ad.call("source_control.commit", {"branch": "feature/x", "message": "m", "files": {".git/config": "x"}})
    assert (await ad.call("source_control.read_file", {"path": "README.md"})) == "hello\n"


@respx.mock
async def test_open_pr_uses_api(repo):
    remote, work = repo
    respx.post("https://api.github.com/repos/o/r/pulls").mock(return_value=httpx.Response(201, json={"number": 7, "html_url": "u"}))
    ad = GitHubAdapter(tenant_id="t", secrets=EnvSecretStore({("t", "github_token"): "tok"}), repo="o/r", workdir=work,
                       clone_url=str(remote))
    assert (await ad.call("source_control.open_pr", {"branch": "feature/x", "title": "t", "body": "b"}))["number"] == 7


# ---- local exec ---------------------------------------------------------------------------
async def test_local_exec_reports_pass_fail_and_only_configured_tools(tmp_path):
    ad = LocalExecAdapter(tmp_path, {"tests": "true", "lint": "false"})
    assert {t.name for t in ad.tools()} == {"execution.run_tests", "execution.run_lint"}
    assert (await ad.call("execution.run_tests", {}))["passed"] is True
    assert (await ad.call("execution.run_lint", {}))["passed"] is False
    with pytest.raises(SupdevError):
        await ad.call("execution.run_tests", {"paths": ["--evil"]})


# ---- MCP bridge (tagging only; no server) -------------------------------------------------
def test_mcp_only_exposes_tagged_tools_with_gates():
    cfg = {"mcp": {"servers": [{"name": "s", "command": "x", "tools": {
        "query": {"capability": "logs", "access": "read", "environment": "prod", "bounded": True},
        "restart": {"capability": "logs", "access": "update", "environment": "prod"}}}]}}
    ad = McpFactory().create(TenantConfig("t", cfg), SECRETS)[0]
    names = {t.name: t for t in ad.tools()}
    assert set(names) == {"logs.query.prod", "logs.restart.prod"}      # nothing else exists (deny by default)
    assert names["logs.query.prod"].bounded and names["logs.restart.prod"].access == Access.UPDATE


# ---- jira releases (board side panel) ----------------------------------------------------------
@respx.mock
async def test_jira_releases_sorted_with_progress_and_best_effort():
    respx.get("https://j.test/rest/api/3/project/OPS/versions").mock(return_value=httpx.Response(200, json=[
        {"id": "1", "name": "1.0", "released": True, "releaseDate": "2026-01-10"},
        {"id": "2", "name": "2.0", "released": False, "releaseDate": "2026-12-01", "description": "big one"},
        {"id": "3", "name": "1.5", "released": False, "releaseDate": "2026-10-01", "overdue": True},
        {"id": "4", "name": "old", "released": True, "archived": True}]))
    respx.get("https://j.test/rest/api/3/version/2/unresolvedIssueCount").mock(
        return_value=httpx.Response(200, json={"issuesCount": 10, "issuesUnresolvedCount": 4}))
    respx.get("https://j.test/rest/api/3/version/3/unresolvedIssueCount").mock(return_value=httpx.Response(500))
    ad = JiraFactory().create(TenantConfig("t", {"jira": {"base_url": "https://j.test", "project": "OPS"}}), SECRETS)[0]
    rel = await ad.releases()
    assert [r["name"] for r in rel] == ["1.5", "2.0", "1.0"]                       # unreleased soonest first, archived hidden
    by = {r["name"]: r for r in rel}
    assert (by["2.0"]["done"], by["2.0"]["total"]) == (6, 10) and by["1.5"]["done"] is None   # progress is best-effort
    assert by["1.5"]["overdue"] and by["1.0"]["released"] and by["2.0"]["url"] == "https://j.test/projects/OPS/versions/2"
    with pytest.raises(SupdevError):
        await ad.releases("bad key!")


@respx.mock
async def test_jira_release_issues_keys_and_validation():
    route = respx.get("https://j.test/rest/api/3/search/jql").mock(return_value=httpx.Response(200, json={
        "issues": [{"key": "OPS-1"}, {"key": "OPS-7"}]}))
    ad = JiraFactory().create(TenantConfig("t", {"jira": {"base_url": "https://j.test", "project": "OPS"}}), SECRETS)[0]
    assert await ad.release_issues("10") == {"keys": ["OPS-1", "OPS-7"], "truncated": False}
    assert route.calls[0].request.url.params["jql"] == "project = OPS AND fixVersion = 10"
    for bad in ("10 OR 1=1", "abc", "1; DROP"):                       # ids are digits only: no JQL injection
        with pytest.raises(SupdevError):
            await ad.release_issues(bad)
