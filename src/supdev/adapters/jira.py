"""Jira Cloud ticketing adapter (REST v3). Settings: base_url, secret (api token), email_secret."""
from __future__ import annotations

import asyncio
import re
from typing import Any

from .. import capabilities as cap
from ..core.errors import SupdevError
from ..core.models import ToolSpec
from ..plugins.base import SecretStore, TenantConfig
from .util import Http


def _is_epic(issuetype: dict[str, Any]) -> bool:
    return (issuetype.get("hierarchyLevel") or 0) >= 1 or str(issuetype.get("name", "")).strip().lower() == "epic"


def adf(text: str) -> dict[str, Any]:
    paras = [p for p in text.split("\n\n")] or [""]
    return {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": p}] if p else []} for p in paras]}


def adf_to_text(node: Any) -> str:
    if isinstance(node, dict):
        if node.get("type") == "text":
            return str(node.get("text", ""))
        return " ".join(filter(None, (adf_to_text(c) for c in node.get("content", []))))
    if isinstance(node, list):
        return " ".join(filter(None, (adf_to_text(c) for c in node)))
    return "" if node is None else str(node)


class JiraAdapter:
    capability, vendor = "ticketing", "jira"

    def __init__(self, http: Http, project: str | None = None) -> None:
        self.http, self.project = http, project
        self._resolved: dict[str, str] = {}
        self._columns: dict[str, list[dict[str, Any]]] = {}
        self.project_names: dict[str, str] = {}

    def tools(self) -> list[ToolSpec]:
        return cap.ticketing("jira")

    async def ping(self) -> str:
        d = await self.http.request("GET", "/rest/api/3/myself")
        msg = f"authenticated ({d.get('accountType', 'user')})"
        if self.project:  # a wrong project key is the most common misconfiguration — check it here, not at first use
            key = await self.resolve_project()
            msg += f"; project {key} ({self.project_names.get(key, '?')}) found"
            if key.lower() != self.project.strip().lower():
                msg += f" — tip: save the key '{key}' instead of '{self.project}'"
        else:
            msg += "; no default project set — releases need one"
        return msg

    async def call(self, tool: str, a: dict[str, Any]) -> Any:
        op = tool.split(".", 1)[1]
        return await getattr(self, f"_{op}")(a)

    async def resolve_project(self, project: str | None = None) -> str:
        """Accept a project KEY or NAME (people often paste the name, e.g. "SupDev" for key SCRUM) and return the real
        key. On a miss, the error lists the projects this account can actually use."""
        want = (project or self.project or "").strip()
        if not want or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.\-]{0,79}", want):
            raise SupdevError("set the Jira project key (Settings → Integrations → Jira)")
        k = want.lower()
        if k in self._resolved:
            return self._resolved[k]
        vals = (await self.http.request("GET", "/rest/api/3/project/search", params={"query": want, "maxResults": 50})).get("values", [])
        hit = next((p for p in vals if p["key"].lower() == k), None) or next((p for p in vals if p["name"].lower() == k), None)
        if hit is None:
            if not vals:
                vals = (await self.http.request("GET", "/rest/api/3/project/search", params={"maxResults": 20})).get("values", [])
            avail = ", ".join(f"{p['key']} ({p['name']})" for p in vals[:10]) or "none — check the account's permissions"
            raise SupdevError(f"Jira project '{want}' was not found. Use the project KEY. Projects you can access: {avail}")
        self._resolved[k] = hit["key"]
        self.project_names[hit["key"]] = hit["name"]
        return str(hit["key"])

    async def releases(self, project: str | None = None, limit: int = 20) -> dict[str, Any]:
        """Jira releases (project versions), read-only, for the board's Releases panel. Unreleased first (soonest
        due first), then the most recent released. Progress (done/total) for the first 8 unreleased versions."""
        key = await self.resolve_project(project)
        versions = [v for v in await self.http.request("GET", f"/rest/api/3/project/{key}/versions") if not v.get("archived")]
        unreleased = sorted((v for v in versions if not v.get("released")), key=lambda v: (v.get("releaseDate") or "9999", v["name"]))
        released = sorted((v for v in versions if v.get("released")), key=lambda v: v.get("releaseDate") or "", reverse=True)
        chosen = (unreleased + released[:5])[:limit]

        async def progress(v: dict[str, Any]) -> tuple[int, int] | None:
            try:
                d = await self.http.request("GET", f"/rest/api/3/version/{v['id']}/unresolvedIssueCount")
                total, open_ = int(d.get("issuesCount", 0)), int(d.get("issuesUnresolvedCount", 0))
                return total - open_, total
            except Exception:  # noqa: BLE001 — progress is best-effort
                return None

        prog = await asyncio.gather(*[progress(v) if not v.get("released") and i < 8 else asyncio.sleep(0, None)
                                      for i, v in enumerate(chosen)])
        base = self.http.base_url
        return {"project": key, "releases": [
            {"id": v["id"], "name": v["name"], "description": (v.get("description") or "")[:200],
             "released": bool(v.get("released")), "overdue": bool(v.get("overdue")),
             "release_date": v.get("releaseDate"), "start_date": v.get("startDate"),
             "done": pr[0] if pr else None, "total": pr[1] if pr else None,
             "url": f"{base}/projects/{key}/versions/{v['id']}"} for v, pr in zip(chosen, prog, strict=True)]}

    async def issues(self, project: str | None = None, include_done: bool = False, limit: int = 100) -> dict[str, Any]:
        """The project's tasks / stories / bugs (etc.) for the board. Read-only. Sub-tasks and epics are left out; done tickets
        only when asked. Newest first."""
        key = await self.resolve_project(project)
        # open tickets, plus recently finished ones so the Done column is not empty (a board shows recent Done work)
        jql = f"project = {key}" + ("" if include_done else " AND (statusCategory != Done OR updated >= -14d)") + " ORDER BY created DESC"
        d = await self.http.request("GET", "/rest/api/3/search/jql", params={
            "jql": jql, "fields": "summary,issuetype,status,priority,assignee,fixVersions", "maxResults": limit})
        out, epics = [], []
        for i in d.get("issues", []):
            f = i.get("fields", {})
            it = f.get("issuetype") or {}
            # the board is for the work itself: no sub-tasks, and no epics (Jira epics are hierarchy level 1+, also matched by name)
            if _is_epic(it):
                epics.append(i["key"])
                continue
            if it.get("subtask"):
                continue
            out.append({
                "key": i["key"], "summary": (f.get("summary") or "")[:200],
                "type": (f.get("issuetype") or {}).get("name", ""), "status": (f.get("status") or {}).get("name", ""),
                "status_id": str((f.get("status") or {}).get("id", "")),
                "status_category": ((f.get("status") or {}).get("statusCategory") or {}).get("key", ""),  # new|indeterminate|done
                "priority": (f.get("priority") or {}).get("name", ""),
                "assignee": (f.get("assignee") or {}).get("displayName"),
                "fix_versions": [v.get("name", "") for v in f.get("fixVersions", [])],
                "url": f"{self.http.base_url}/browse/{i['key']}"})
        return {"project": key, "issues": out, "epics": epics, "truncated": bool(d.get("nextPageToken")) or len(d.get("issues", [])) >= limit}

    async def project_meta(self, project: str | None = None) -> dict[str, Any]:
        """What this Jira project really has, so nothing needs to be assumed: its issue types (no epics / sub-tasks) and its
        statuses with their category (new | indeterminate | done), in board order when a board exists."""
        key = await self.resolve_project(project)
        proj = await self.http.request("GET", f"/rest/api/3/project/{key}")
        types = [t["name"] for t in proj.get("issueTypes", []) if not t.get("subtask") and (t.get("hierarchyLevel") or 0) < 1
                 and t.get("name", "").strip().lower() != "epic"]
        statuses: dict[str, dict[str, str]] = {}
        for it in await self.http.request("GET", f"/rest/api/3/project/{key}/statuses"):
            for s in it.get("statuses", []):
                statuses.setdefault(str(s["id"]), {"id": str(s["id"]), "name": s["name"], "category": (s.get("statusCategory") or {}).get("key", "")})
        cols = await self.board_columns(key)
        order = {sid: i for i, c in enumerate(cols) for sid in c["status_ids"]}
        ordered = sorted(statuses.values(), key=lambda s: (order.get(s["id"], 10_000), s["name"]))
        return {"project": key, "types": sorted(set(types)), "statuses": ordered}

    async def board_columns(self, project: str | None = None) -> list[dict[str, Any]]:
        """Kanban columns = the Jira board's real columns (each maps to one or more status ids). Falls back to the
        project's workflow statuses ordered new → in progress → done when the account has no Agile board."""
        key = await self.resolve_project(project)
        if key in self._columns:
            return self._columns[key]
        cols: list[dict[str, Any]] = []
        try:
            boards = (await self.http.request("GET", "/rest/agile/1.0/board", params={"projectKeyOrId": key, "maxResults": 1})).get("values", [])
            if boards:
                cfg = await self.http.request("GET", f"/rest/agile/1.0/board/{boards[0]['id']}/configuration")
                cols = [{"name": c["name"], "status_ids": [str(s["id"]) for s in c.get("statuses", [])]}
                        for c in cfg.get("columnConfig", {}).get("columns", []) if c.get("statuses")]
        except Exception:  # noqa: BLE001 — Agile API is optional; fall back to workflow statuses
            cols = []
        if not cols:
            rank = {"new": 0, "indeterminate": 1, "done": 2}
            seen: dict[str, tuple[int, int, str]] = {}
            for it in await self.http.request("GET", f"/rest/api/3/project/{key}/statuses"):
                for s in it.get("statuses", []):
                    seen.setdefault(str(s["id"]), (rank.get((s.get("statusCategory") or {}).get("key", ""), 1), len(seen), s["name"]))
            cols = [{"name": n, "status_ids": [sid]} for sid, (_, _, n) in sorted(seen.items(), key=lambda kv: kv[1][:2])]
        self._columns[key] = cols
        return cols

    async def move_issue(self, issue_key: str, status_ids: list[str], project: str | None = None) -> dict[str, Any]:
        """Change a ticket's status by performing the Jira workflow transition that leads to one of `status_ids`.
        Only tickets of the configured project; the workflow decides what is allowed (never forces a status)."""
        key = await self.resolve_project(project)
        if not re.fullmatch(rf"{re.escape(key)}-\d{{1,9}}", issue_key, re.I):
            raise SupdevError(f"'{issue_key}' is not a ticket of project {key}")
        want = {str(s) for s in status_ids}
        cur = (await self.http.request("GET", f"/rest/api/3/issue/{issue_key}", params={"fields": "status"}))["fields"]["status"]
        if str(cur.get("id")) in want:
            return {"key": issue_key, "status": cur.get("name"), "changed": False}
        ts = (await self.http.request("GET", f"/rest/api/3/issue/{issue_key}/transitions")).get("transitions", [])
        t = next((x for x in ts if str((x.get("to") or {}).get("id")) in want), None)
        if t is None:
            nxt = ", ".join(sorted({(x.get("to") or {}).get("name", "?") for x in ts})) or "none"
            raise SupdevError(f"Jira's workflow doesn't allow moving {issue_key} from '{cur.get('name')}' to that column directly. "
                              f"Allowed next statuses: {nxt}")
        await self.http.request("POST", f"/rest/api/3/issue/{issue_key}/transitions", json={"transition": {"id": t["id"]}})
        return {"key": issue_key, "status": t["to"]["name"], "changed": True}

    async def transition_to_named(self, issue_key: str, names: list[str], project: str | None = None) -> dict[str, Any]:
        """Phase-sync helper: move a ticket to the FIRST of `names` (e.g. "In Progress", then "Development") that Jira's
        workflow currently allows. Never forces a status; never reopens a Done ticket; never moves a ticket backwards
        along the board (a ticket already further right stays put). Returns what happened, including why nothing changed."""
        key = await self.resolve_project(project)
        if not re.fullmatch(rf"{re.escape(key)}-\d{{1,9}}", issue_key, re.I):
            raise SupdevError(f"'{issue_key}' is not a ticket of project {key}")
        want = [n.strip().lower() for n in names if n and n.strip()]
        if not want:
            raise SupdevError("no target status given")
        cur = (await self.http.request("GET", f"/rest/api/3/issue/{issue_key}", params={"fields": "status"}))["fields"]["status"]
        cur_name, cur_id = cur.get("name", ""), str(cur.get("id", ""))
        base = {"key": issue_key, "from": cur_name, "to": cur_name, "changed": False}
        if cur_name.lower() in want:
            return {**base, "reason": "already there"}
        if (cur.get("statusCategory") or {}).get("key") == "done":
            return {**base, "reason": "ticket is already done; not reopened"}
        ts = (await self.http.request("GET", f"/rest/api/3/issue/{issue_key}/transitions")).get("transitions", [])
        t = next((x for n in want for x in ts if (x.get("to") or {}).get("name", "").lower() == n), None)
        if t is None:
            nxt = ", ".join(sorted({(x.get("to") or {}).get("name", "?") for x in ts})) or "none"
            raise SupdevError(f"Jira's workflow doesn't allow moving {issue_key} from '{cur_name}' to any of: {', '.join(names)}. "
                              f"Allowed next statuses: {nxt}")
        try:  # don't drag a ticket backwards: compare positions on the board when we can
            cols = await self.board_columns(key)
            pos = lambda sid: next((i for i, c in enumerate(cols) if str(sid) in c["status_ids"]), None)  # noqa: E731
            here, there = pos(cur_id), pos((t.get("to") or {}).get("id"))
            if here is not None and there is not None and here > there:
                return {**base, "reason": f"already further along than '{t['to']['name']}'"}
        except Exception:  # noqa: BLE001 — ordering is a courtesy, not a requirement
            pass
        await self.http.request("POST", f"/rest/api/3/issue/{issue_key}/transitions", json={"transition": {"id": t["id"]}})
        return {**base, "to": t["to"]["name"], "changed": True}

    async def release_issues(self, version_id: str, project: str | None = None, limit: int = 100) -> dict[str, Any]:
        """Ticket keys that belong to a release (for the board's Release filter). Read-only. A release is often set on an EPIC
        while the work sits under it, so tickets whose parent epic carries the release are included too (`via_epics` says which)."""
        key = await self.resolve_project(project)
        if not re.fullmatch(r"\d{1,12}", str(version_id)):
            raise SupdevError("invalid release id")
        d = await self.http.request("GET", "/rest/api/3/search/jql", params={
            "jql": f"project = {key} AND fixVersion = {version_id}", "fields": "summary,issuetype", "maxResults": limit})
        direct = [i["key"] for i in d.get("issues", [])]
        epics = [i["key"] for i in d.get("issues", []) if _is_epic((i.get("fields") or {}).get("issuetype") or {})
                 and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*-\d+", i["key"])]
        children: list[str] = []
        if epics:
            ids = ", ".join(epics)
            for jql in (f"parent in ({ids})", f'"Epic Link" in ({ids})'):   # team-managed / new company-managed, then classic projects
                try:
                    r = await self.http.request("GET", "/rest/api/3/search/jql", params={"jql": jql, "fields": "summary", "maxResults": limit})
                    children += [i["key"] for i in r.get("issues", [])]
                except SupdevError:
                    continue  # that field/JQL doesn't exist in this project type
        keys = list(dict.fromkeys(direct + children))
        return {"keys": keys, "via_epics": epics if children else [],
                "truncated": bool(d.get("nextPageToken")) or len(keys) >= limit}

    async def _get_item(self, a: dict[str, Any]) -> Any:
        d = await self.http.request("GET", f"/rest/api/3/issue/{a['key']}", params={
            "fields": "summary,description,issuetype,status,priority,comment,issuelinks,subtasks,labels,created"})
        f = d["fields"]
        return {"key": d["key"], "type": f["issuetype"]["name"], "status": f["status"]["name"],
                "summary": f["summary"], "description": adf_to_text(f.get("description")),
                "created": f.get("created"), "labels": f.get("labels", []),
                "comments": [{"author": "user", "created": c["created"], "body": adf_to_text(c["body"])}
                             for c in f.get("comment", {}).get("comments", [])],  # authors omitted (blameless)
                "links": [{"type": link["type"]["name"], "key": (link.get("outwardIssue") or link.get("inwardIssue"))["key"]}
                          for link in f.get("issuelinks", [])],
                "subtasks": [s["key"] for s in f.get("subtasks", [])]}

    async def _search(self, a: dict[str, Any]) -> Any:
        d = await self.http.request("GET", "/rest/api/3/search/jql", params={
            "jql": a["query"], "maxResults": 10, "fields": "summary,status,issuetype"})
        return [{"key": i["key"], "summary": i["fields"]["summary"], "status": i["fields"]["status"]["name"]}
                for i in d.get("issues", [])]

    async def _add_comment(self, a: dict[str, Any]) -> Any:
        d = await self.http.request("POST", f"/rest/api/3/issue/{a['key']}/comment", json={"body": adf(a["body"])})
        return {"posted": True, "id": d.get("id")}

    async def _update_status(self, a: dict[str, Any]) -> Any:
        ts = (await self.http.request("GET", f"/rest/api/3/issue/{a['key']}/transitions"))["transitions"]
        t = next((x for x in ts if x["name"].lower() == a["status"].lower()), None)
        if t is None:
            raise SupdevError("no such transition; available: " + ", ".join(x["name"] for x in ts))
        await self.http.request("POST", f"/rest/api/3/issue/{a['key']}/transitions", json={"transition": {"id": t["id"]}})
        return {"status": t["name"]}

    async def _create_items(self, a: dict[str, Any]) -> Any:
        out = []
        for it in a["items"]:
            proj = it.get("project") or self.project
            if not proj:
                raise SupdevError("project is required (set `project` in the Jira integration)")
            d = await self.http.request("POST", "/rest/api/3/issue", json={"fields": {
                "project": {"key": proj}, "summary": it["summary"], "issuetype": {"name": it.get("type", "Task")},
                "description": adf(it.get("description", ""))}})
            out.append(d["key"])
        return {"created": out}


class JiraFactory:
    def create(self, tenant: TenantConfig, secrets: SecretStore) -> list[Any]:
        c = tenant.integrations["jira"]
        return [JiraAdapter(Http(c["base_url"].rstrip("/"), tenant.tenant_id, secrets, auth="basic",
                                 secret=c.get("secret", "jira_token"), secret2=c.get("email_secret", "jira_email")),
                            c.get("project"))]
