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

    def tools(self) -> list[ToolSpec]:
        return cap.ticketing("jira")

    async def ping(self) -> str:
        d = await self.http.request("GET", "/rest/api/3/myself")
        return f"authenticated ({d.get('accountType', 'user')})"

    async def call(self, tool: str, a: dict[str, Any]) -> Any:
        op = tool.split(".", 1)[1]
        return await getattr(self, f"_{op}")(a)

    async def releases(self, project: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Jira releases (project versions), read-only, for the board's Releases panel. Unreleased first (soonest
        due first), then the most recent released. Progress (done/total) for the first 8 unreleased versions."""
        key = project or self.project
        if not key or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{1,30}", key):
            raise SupdevError("set a valid Jira project key (Settings → Integrations → Jira)")
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
        return [{"id": v["id"], "name": v["name"], "description": (v.get("description") or "")[:200],
                 "released": bool(v.get("released")), "overdue": bool(v.get("overdue")),
                 "release_date": v.get("releaseDate"), "start_date": v.get("startDate"),
                 "done": pr[0] if pr else None, "total": pr[1] if pr else None,
                 "url": f"{base}/projects/{key}/versions/{v['id']}"} for v, pr in zip(chosen, prog, strict=True)]

    async def release_issues(self, version_id: str, project: str | None = None, limit: int = 100) -> dict[str, Any]:
        """Ticket keys whose fixVersion is this release (for the board's Release filter). Read-only."""
        key = project or self.project
        if not key or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{1,30}", key):
            raise SupdevError("set a valid Jira project key (Settings → Integrations → Jira)")
        if not re.fullmatch(r"\d{1,12}", str(version_id)):
            raise SupdevError("invalid release id")
        d = await self.http.request("GET", "/rest/api/3/search/jql", params={
            "jql": f"project = {key} AND fixVersion = {version_id}", "fields": "summary", "maxResults": limit})
        keys = [i["key"] for i in d.get("issues", [])]
        return {"keys": keys, "truncated": bool(d.get("nextPageToken")) or len(keys) >= limit}

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
