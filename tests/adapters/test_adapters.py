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
def _projects(vals):
    respx.get("https://j.test/rest/api/3/project/search").mock(return_value=httpx.Response(200, json={"values": vals}))


@respx.mock
async def test_jira_releases_sorted_with_progress_and_best_effort():
    _projects([{"key": "OPS", "name": "Operations"}])
    respx.get("https://j.test/rest/api/3/project/OPS/versions").mock(return_value=httpx.Response(200, json=[
        {"id": "1", "name": "1.0", "released": True, "releaseDate": "2026-01-10"},
        {"id": "2", "name": "2.0", "released": False, "releaseDate": "2026-12-01", "description": "big one"},
        {"id": "3", "name": "1.5", "released": False, "releaseDate": "2026-10-01", "overdue": True},
        {"id": "4", "name": "old", "released": True, "archived": True}]))
    respx.get("https://j.test/rest/api/3/version/2/unresolvedIssueCount").mock(
        return_value=httpx.Response(200, json={"issuesCount": 10, "issuesUnresolvedCount": 4}))
    respx.get("https://j.test/rest/api/3/version/3/unresolvedIssueCount").mock(return_value=httpx.Response(500))
    ad = JiraFactory().create(TenantConfig("t", {"jira": {"base_url": "https://j.test", "project": "OPS"}}), SECRETS)[0]
    res = await ad.releases()
    rel = res["releases"]
    assert res["project"] == "OPS"
    assert [r["name"] for r in rel] == ["1.5", "2.0", "1.0"]                       # unreleased soonest first, archived hidden
    by = {r["name"]: r for r in rel}
    assert (by["2.0"]["done"], by["2.0"]["total"]) == (6, 10) and by["1.5"]["done"] is None   # progress is best-effort
    assert by["1.5"]["overdue"] and by["1.0"]["released"] and by["2.0"]["url"] == "https://j.test/projects/OPS/versions/2"
    with pytest.raises(SupdevError):
        await ad.releases("bad/key!")


@respx.mock
async def test_jira_release_issues_keys_and_validation():
    _projects([{"key": "OPS", "name": "Operations"}])
    route = respx.get("https://j.test/rest/api/3/search/jql").mock(return_value=httpx.Response(200, json={
        "issues": [{"key": "OPS-1"}, {"key": "OPS-7"}]}))
    ad = JiraFactory().create(TenantConfig("t", {"jira": {"base_url": "https://j.test", "project": "OPS"}}), SECRETS)[0]
    assert await ad.release_issues("10") == {"keys": ["OPS-1", "OPS-7"], "via_epics": [], "truncated": False}
    assert route.calls[0].request.url.params["jql"] == "project = OPS AND fixVersion = 10"
    for bad in ("10 OR 1=1", "abc", "1; DROP"):                       # ids are digits only: no JQL injection
        with pytest.raises(SupdevError):
            await ad.release_issues(bad)


@respx.mock
async def test_jira_project_name_resolves_to_key_and_wrong_project_lists_choices():
    # the real-world failure: "SupDev" is the project NAME, its key is SCRUM
    _projects([{"key": "SCRUM", "name": "SupDev"}])
    respx.get("https://j.test/rest/api/3/myself").mock(return_value=httpx.Response(200, json={"accountType": "atlassian"}))
    vroute = respx.get("https://j.test/rest/api/3/project/SCRUM/versions").mock(return_value=httpx.Response(200, json=[
        {"id": "5", "name": "v1", "released": False}]))
    ad = JiraFactory().create(TenantConfig("t", {"jira": {"base_url": "https://j.test", "project": "SupDev"}}), SECRETS)[0]
    res = await ad.releases()
    assert res["project"] == "SCRUM" and vroute.called and res["releases"][0]["url"].endswith("/projects/SCRUM/versions/5")
    ping = await ad.ping()
    assert "project SCRUM (SupDev) found" in ping and "save the key 'SCRUM'" in ping        # nudges towards the key
    # a value that matches nothing => helpful error listing what the account CAN use
    respx.get("https://j.test/rest/api/3/project/search").mock(side_effect=[
        httpx.Response(200, json={"values": []}), httpx.Response(200, json={"values": [{"key": "SCRUM", "name": "SupDev"}]})])
    bad = JiraFactory().create(TenantConfig("t", {"jira": {"base_url": "https://j.test", "project": "Nope"}}), SECRETS)[0]
    with pytest.raises(SupdevError, match=r"Use the project KEY.*SCRUM \(SupDev\)"):
        await bad.releases()


@respx.mock
async def test_jira_issues_for_board_maps_fields_and_skips_subtasks():
    _projects([{"key": "OPS", "name": "Operations"}])
    route = respx.get("https://j.test/rest/api/3/search/jql").mock(return_value=httpx.Response(200, json={"issues": [
        {"key": "OPS-3", "fields": {"summary": "Fix login", "issuetype": {"name": "Bug"}, "priority": {"name": "High"},
                                     "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
                                     "assignee": {"displayName": "Ada Lovelace"}, "fixVersions": [{"name": "v1"}]}},
        {"key": "OPS-2", "fields": {"summary": "Story", "issuetype": {"name": "Story"}, "status": {"name": "To Do"}}},
        {"key": "OPS-9", "fields": {"summary": "sub", "issuetype": {"name": "Sub-task", "subtask": True}}},
        {"key": "OPS-10", "fields": {"summary": "MVP", "issuetype": {"name": "Epic", "hierarchyLevel": 1}}},
        {"key": "OPS-11", "fields": {"summary": "renamed epic", "issuetype": {"name": "Initiative", "hierarchyLevel": 2}}},
        {"key": "OPS-12", "fields": {"summary": "epic by name", "issuetype": {"name": "EPIC"}}}]}))
    ad = JiraFactory().create(TenantConfig("t", {"jira": {"base_url": "https://j.test", "project": "OPS"}}), SECRETS)[0]
    res = await ad.issues()
    assert [i["key"] for i in res["issues"]] == ["OPS-3", "OPS-2"]                       # sub-task and every kind of epic dropped
    assert res["epics"] == ["OPS-10", "OPS-11", "OPS-12"]                                # …but reported, so their work items are hidden too
    first = res["issues"][0]
    assert (first["type"], first["status_category"], first["assignee"], first["fix_versions"]) == ("Bug", "indeterminate", "Ada Lovelace", ["v1"])
    assert first["url"] == "https://j.test/browse/OPS-3" and res["issues"][1]["assignee"] is None
    assert "statusCategory != Done" in route.calls[0].request.url.params["jql"]
    await ad.issues(include_done=True)
    assert "statusCategory" not in route.calls[-1].request.url.params["jql"]


def _jira(project="OPS"):
    return JiraFactory().create(TenantConfig("t", {"jira": {"base_url": "https://j.test", "project": project}}), SECRETS)[0]


@respx.mock
async def test_jira_board_columns_from_agile_board_then_fallback_to_workflow_statuses():
    _projects([{"key": "OPS", "name": "Operations"}])
    respx.get("https://j.test/rest/agile/1.0/board").mock(return_value=httpx.Response(200, json={"values": [{"id": 7}]}))
    respx.get("https://j.test/rest/agile/1.0/board/7/configuration").mock(return_value=httpx.Response(200, json={"columnConfig": {"columns": [
        {"name": "Backlog", "statuses": []}, {"name": "To Do", "statuses": [{"id": "1"}]},
        {"name": "In Progress", "statuses": [{"id": "3"}, {"id": "4"}]}, {"name": "Done", "statuses": [{"id": "5"}]}]}}))
    assert await _jira().board_columns() == [{"name": "To Do", "status_ids": ["1"]},
                                            {"name": "In Progress", "status_ids": ["3", "4"]}, {"name": "Done", "status_ids": ["5"]}]
    # no Agile board (e.g. no Software licence) -> project workflow statuses, ordered new -> in progress -> done
    respx.get("https://j.test/rest/agile/1.0/board").mock(return_value=httpx.Response(403))
    respx.get("https://j.test/rest/api/3/project/OPS/statuses").mock(return_value=httpx.Response(200, json=[
        {"statuses": [{"id": "5", "name": "Done", "statusCategory": {"key": "done"}},
                      {"id": "1", "name": "To Do", "statusCategory": {"key": "new"}},
                      {"id": "3", "name": "In Progress", "statusCategory": {"key": "indeterminate"}}]}]))
    assert [c["name"] for c in await _jira().board_columns()] == ["To Do", "In Progress", "Done"]


@respx.mock
async def test_jira_move_issue_uses_workflow_transitions_only():
    _projects([{"key": "OPS", "name": "Operations"}])
    respx.get("https://j.test/rest/api/3/issue/OPS-3").mock(return_value=httpx.Response(200, json={"fields": {"status": {"id": "1", "name": "To Do"}}}))
    respx.get("https://j.test/rest/api/3/issue/OPS-3/transitions").mock(return_value=httpx.Response(200, json={"transitions": [
        {"id": "21", "to": {"id": "3", "name": "In Progress"}}, {"id": "31", "to": {"id": "4", "name": "In Review"}}]}))
    post = respx.post("https://j.test/rest/api/3/issue/OPS-3/transitions").mock(return_value=httpx.Response(204))
    ad = _jira()
    assert await ad.move_issue("OPS-3", ["3"]) == {"key": "OPS-3", "status": "In Progress", "changed": True}
    assert post.calls[0].request.content == b'{"transition":{"id":"21"}}'                  # the transition id, never a raw status
    assert (await ad.move_issue("OPS-3", ["1"]))["changed"] is False                      # already there: no write
    with pytest.raises(SupdevError, match=r"doesn't allow.*In Progress, In Review"):
        await ad.move_issue("OPS-3", ["5"])                                               # workflow says no: we don't force it
    with pytest.raises(SupdevError, match="not a ticket of project OPS"):
        await ad.move_issue("OTHER-1", ["3"])                                             # only the configured project
    with pytest.raises(SupdevError):
        await ad.move_issue("OPS-3; DROP", ["3"])
    assert post.call_count == 1


def _gh(tmp_path, **kw):
    return GitHubAdapter(tenant_id="t", secrets=EnvSecretStore({("t", "github_token"): "tok"}), repo="o/r", workdir=tmp_path,
                         clone_url="https://github.com/o/r.git", **kw)


@respx.mock
async def test_github_ping_reports_whether_the_token_can_push(tmp_path):
    route = respx.get("https://api.github.com/repos/o/r")
    route.mock(return_value=httpx.Response(200, json={"private": True, "default_branch": "main", "permissions": {"push": True}}))
    msg = await _gh(tmp_path).ping()
    assert "private" in msg and "token CAN push" in msg
    route.mock(return_value=httpx.Response(200, json={"private": False, "default_branch": "trunk", "permissions": {"push": False}}))
    msg = await _gh(tmp_path).ping()
    assert "can NOT push" in msg and "Contents: read & write" in msg and "saved default branch is 'main'" in msg


@respx.mock
async def test_github_ping_turns_http_errors_into_actionable_messages(tmp_path):
    route = respx.get("https://api.github.com/repos/o/r")
    route.mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
    with pytest.raises(SupdevError, match="not found.*token has access"):
        await _gh(tmp_path).ping()
    route.mock(return_value=httpx.Response(401, json={"message": "Bad credentials"}))
    with pytest.raises(SupdevError, match="rejected the token"):
        await _gh(tmp_path).ping()


# ---- phase sync: transition_to_named ---------------------------------------------------------
def _issue_mocks(status, category="indeterminate", transitions=(("21", "3", "In Progress"),), sid="1"):
    _projects([{"key": "OPS", "name": "Ops"}])
    respx.get("https://j.test/rest/api/3/issue/OPS-3").mock(return_value=httpx.Response(200, json={"fields": {"status": {
        "id": sid, "name": status, "statusCategory": {"key": category}}}}))
    respx.get("https://j.test/rest/api/3/issue/OPS-3/transitions").mock(return_value=httpx.Response(200, json={"transitions": [
        {"id": t, "to": {"id": i, "name": n}} for t, i, n in transitions]}))
    respx.get("https://j.test/rest/agile/1.0/board").mock(return_value=httpx.Response(200, json={"values": [{"id": 1}]}))
    respx.get("https://j.test/rest/agile/1.0/board/1/configuration").mock(return_value=httpx.Response(200, json={"columnConfig": {"columns": [
        {"name": "To Do", "statuses": [{"id": "1"}]}, {"name": "In Progress", "statuses": [{"id": "3"}]},
        {"name": "In Review", "statuses": [{"id": "4"}]}, {"name": "Done", "statuses": [{"id": "5"}]}]}}))
    return respx.post("https://j.test/rest/api/3/issue/OPS-3/transitions").mock(return_value=httpx.Response(204))


@respx.mock
async def test_transition_to_named_prefers_first_available_candidate():
    post = _issue_mocks("To Do", "new", (("21", "3", "In Progress"), ("22", "6", "Development")), sid="1")
    r = await _jira().transition_to_named("OPS-3", ["In Progress", "Development"])
    assert r == {"key": "OPS-3", "from": "To Do", "to": "In Progress", "changed": True} and post.calls[0].request.content == b'{"transition":{"id":"21"}}'
    post.reset()
    r = await _jira().transition_to_named("OPS-3", ["In Progress", "Development"])           # cached workflow: same answer, first candidate wins
    assert r["to"] == "In Progress"


@respx.mock
async def test_transition_to_named_falls_back_to_the_equivalent_status():
    post = _issue_mocks("To Do", "new", (("22", "6", "Development"),), sid="1")                  # this workflow has no "In Progress"
    r = await _jira().transition_to_named("OPS-3", ["In Progress", "Development"])
    assert r["to"] == "Development" and post.call_count == 1


@respx.mock
async def test_transition_to_named_never_regresses_reopens_or_forces():
    post = _issue_mocks("In Progress", "indeterminate", sid="3")
    assert (await _jira().transition_to_named("OPS-3", ["in progress"]))["reason"] == "already there"        # case-insensitive no-op
    assert (await _jira().transition_to_named("OPS-3", ["In Progress", "Doing"]))["changed"] is False
    _issue_mocks("Done", "done", (("21", "3", "In Progress"),), sid="5")
    assert "not reopened" in (await _jira().transition_to_named("OPS-3", ["In Progress"]))["reason"]         # done stays done
    _issue_mocks("In Review", "indeterminate", (("21", "3", "In Progress"),), sid="4")                       # board: In Review is right of In Progress
    assert "further along" in (await _jira().transition_to_named("OPS-3", ["In Progress"]))["reason"]
    _issue_mocks("To Do", "new", (("21", "3", "In Progress"),), sid="1")
    with pytest.raises(SupdevError, match=r"doesn't allow.*Allowed next statuses: In Progress"):
        await _jira().transition_to_named("OPS-3", ["Blocked"])                                               # workflow says no
    with pytest.raises(SupdevError, match="not a ticket of project OPS"):
        await _jira().transition_to_named("OTHER-1", ["In Progress"])
    assert not post.called


@respx.mock
async def test_project_meta_reports_types_and_statuses_in_board_order_without_epics():
    _projects([{"key": "OPS", "name": "Ops"}])
    respx.get("https://j.test/rest/api/3/project/OPS").mock(return_value=httpx.Response(200, json={"issueTypes": [
        {"name": "Story"}, {"name": "Chore"}, {"name": "Epic", "hierarchyLevel": 1}, {"name": "Subtask", "subtask": True}, {"name": "Task"}]}))
    respx.get("https://j.test/rest/api/3/project/OPS/statuses").mock(return_value=httpx.Response(200, json=[{"statuses": [
        {"id": "5", "name": "Done", "statusCategory": {"key": "done"}}, {"id": "1", "name": "Backlog", "statusCategory": {"key": "new"}},
        {"id": "3", "name": "Doing", "statusCategory": {"key": "indeterminate"}}]}]))
    respx.get("https://j.test/rest/agile/1.0/board").mock(return_value=httpx.Response(200, json={"values": [{"id": 1}]}))
    respx.get("https://j.test/rest/agile/1.0/board/1/configuration").mock(return_value=httpx.Response(200, json={"columnConfig": {"columns": [
        {"name": "A", "statuses": [{"id": "1"}]}, {"name": "B", "statuses": [{"id": "3"}]}, {"name": "C", "statuses": [{"id": "5"}]}]}}))
    m = await _jira().project_meta()
    assert m["types"] == ["Chore", "Story", "Task"] and [s["name"] for s in m["statuses"]] == ["Backlog", "Doing", "Done"]
    assert {s["name"]: s["category"] for s in m["statuses"]} == {"Backlog": "new", "Doing": "indeterminate", "Done": "done"}


@respx.mock
async def test_release_filter_includes_work_under_an_epic_that_carries_the_release():
    _projects([{"key": "OPS", "name": "Ops"}])
    calls = []

    def answer(request):
        jql = request.url.params["jql"]
        calls.append(jql)
        if "fixVersion" in jql:      # the release is set only on the epic OPS-5 (and directly on OPS-9)
            return httpx.Response(200, json={"issues": [{"key": "OPS-5", "fields": {"issuetype": {"name": "Epic", "hierarchyLevel": 1}}},
                                                        {"key": "OPS-9", "fields": {"issuetype": {"name": "Task"}}}]})
        if jql.startswith("parent in"):
            return httpx.Response(200, json={"issues": [{"key": "OPS-1"}, {"key": "OPS-2"}, {"key": "OPS-9"}]})
        return httpx.Response(400, json={"errorMessages": ["Field 'Epic Link' does not exist"]})       # not a classic project

    respx.get("https://j.test/rest/api/3/search/jql").mock(side_effect=answer)
    r = await _jira().release_issues("10")
    assert r["keys"] == ["OPS-5", "OPS-9", "OPS-1", "OPS-2"] and r["via_epics"] == ["OPS-5"]         # union, no duplicates, epic named
    assert "parent in (OPS-5)" in calls[1]
    # a release with no epics costs a single query and reports no epic
    respx.get("https://j.test/rest/api/3/search/jql").mock(return_value=httpx.Response(200, json={"issues": [{"key": "OPS-3", "fields": {"issuetype": {"name": "Task"}}}]}))
    assert await _jira().release_issues("11") == {"keys": ["OPS-3"], "via_epics": [], "truncated": False}
