"""Read-only observability adapters: Grafana (metrics), Loki (logs), Rancher/Kubernetes (cluster events).
All tools are bounded (time_range + scope) and tagged with their environment; prod is read-only by design."""
from __future__ import annotations

import json
from typing import Any

from .. import capabilities as cap
from ..core.errors import SupdevError
from ..core.models import ToolSpec
from ..plugins.base import SecretStore, TenantConfig
from .util import Http, instances, parse_time_range

_SAFE = str.maketrans({'"': "", "\\": "", "\n": " ", "{": "", "}": ""})


def _lbl(v: Any) -> str:
    return str(v).translate(_SAFE)


def label_selector(a: dict[str, Any]) -> str:
    labels = dict(a.get("labels") or {})
    if a.get("service"):
        labels.setdefault("service", a["service"])
    if a.get("namespace"):
        labels.setdefault("namespace", a["namespace"])
    if not labels:
        raise SupdevError("a scope (service, namespace or labels) is required")
    return "{" + ",".join(f'{_lbl(k)}="{_lbl(v)}"' for k, v in labels.items()) + "}"


class GrafanaAdapter:
    """Prometheus-compatible datasource through Grafana's read-only datasource proxy."""
    capability, vendor = "metrics", "grafana"

    def __init__(self, http: Http, env: str, ds_uid: str) -> None:
        self.http, self.env, self.ds = http, env, ds_uid

    def tools(self) -> list[ToolSpec]:
        return cap.metrics("grafana", self.env)

    async def ping(self) -> str:
        await self.http.request("GET", f"/api/datasources/uid/{self.ds}")
        return f"datasource '{self.ds}' reachable"

    async def call(self, tool: str, a: dict[str, Any]) -> Any:
        start, end = parse_time_range(a["time_range"])
        q = a.get("query") or f"sum(rate(http_requests_total{label_selector(a)}[5m]))"
        d = await self.http.request("GET", f"/api/datasources/proxy/uid/{self.ds}/api/v1/query_range", params={
            "query": q, "start": start.timestamp(), "end": end.timestamp(), "step": a.get("step", "60s")})
        res = d.get("data", {}).get("result", [])[:20]
        return {"series": [{"metric": r.get("metric"), "n": len(r.get("values", [])),
                            "min": min((float(v[1]) for v in r["values"]), default=None),
                            "max": max((float(v[1]) for v in r["values"]), default=None),
                            "last": r["values"][-1][1] if r.get("values") else None} for r in res]}  # aggregates first


class LokiAdapter:
    capability, vendor = "logs", "loki"

    def __init__(self, http: Http, env: str) -> None:
        self.http, self.env = http, env

    def tools(self) -> list[ToolSpec]:
        return cap.logs("loki", self.env)

    async def ping(self) -> str:
        await self.http.request("GET", "/loki/api/v1/labels")
        return "loki reachable"

    async def call(self, tool: str, a: dict[str, Any]) -> Any:
        start, end = parse_time_range(a["time_range"])
        sel = label_selector(a)
        q = a.get("query") or sel
        if "{" not in q:
            q = f"{sel} |= \"{_lbl(q)}\""
        limit = min(int(a.get("limit") or 50), 200)
        d = await self.http.request("GET", "/loki/api/v1/query_range", params={
            "query": q, "start": int(start.timestamp() * 1e9), "end": int(end.timestamp() * 1e9),
            "limit": limit, "direction": "backward"})
        lines = [ln[1] for s in d.get("data", {}).get("result", []) for ln in s.get("values", [])][:limit]
        return {"count": len(lines), "lines": lines[:limit]}  # caller redacts; agent quotes only key lines


class RancherAdapter:
    """Kubernetes events through Rancher's cluster proxy. GET only."""
    capability, vendor = "cluster", "rancher"

    def __init__(self, http: Http, env: str, cluster_id: str) -> None:
        self.http, self.env, self.cluster = http, env, cluster_id

    def tools(self) -> list[ToolSpec]:
        return cap.cluster("rancher", self.env)

    async def ping(self) -> str:
        d = await self.http.request("GET", f"/k8s/clusters/{self.cluster}/version")
        return f"cluster reachable (k8s {d.get('gitVersion', '?')})"

    async def call(self, tool: str, a: dict[str, Any]) -> Any:
        start, _ = parse_time_range(a["time_range"])
        ns = a.get("namespace")
        if not ns and not a.get("service"):
            raise SupdevError("a scope (namespace or service) is required")
        path = f"/k8s/clusters/{self.cluster}/api/v1/" + (f"namespaces/{_lbl(ns)}/events" if ns else "events")
        d = await self.http.request("GET", path, params={"limit": 200})
        out = []
        for e in d.get("items", []):
            ts = e.get("lastTimestamp") or e.get("eventTime") or ""
            if ts and ts >= start.strftime("%Y-%m-%dT%H:%M:%SZ") and (
                    not a.get("service") or a["service"] in json.dumps(e.get("involvedObject", {}))):
                out.append({"time": ts, "type": e.get("type"), "reason": e.get("reason"),
                            "object": f"{e['involvedObject'].get('kind')}/{e['involvedObject'].get('name')}",
                            "message": (e.get("message") or "")[:300], "count": e.get("count")})
        return {"events": out[:100]}


def _http(t: TenantConfig, s: SecretStore, c: dict[str, Any]) -> Http:
    return Http(c["url"].rstrip("/"), t.tenant_id, s, auth="bearer", secret=c.get("secret"))


class GrafanaFactory:
    def create(self, tenant: TenantConfig, secrets: SecretStore) -> list[Any]:
        return [GrafanaAdapter(_http(tenant, secrets, c), c["env"], c["datasource_uid"])
                for c in instances(tenant.integrations["grafana"])]


class LokiFactory:
    def create(self, tenant: TenantConfig, secrets: SecretStore) -> list[Any]:
        return [LokiAdapter(_http(tenant, secrets, c), c["env"]) for c in instances(tenant.integrations["loki"])]


class RancherFactory:
    def create(self, tenant: TenantConfig, secrets: SecretStore) -> list[Any]:
        return [RancherAdapter(_http(tenant, secrets, c), c["env"], c["cluster_id"])
                for c in instances(tenant.integrations["rancher"])]
