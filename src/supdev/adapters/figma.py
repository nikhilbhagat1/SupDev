from __future__ import annotations

import re
from typing import Any

from .. import capabilities as cap
from ..core.errors import SupdevError
from ..core.models import ToolSpec
from ..plugins.base import SecretStore, TenantConfig
from .util import Http

_URL = re.compile(r"figma\.com/(?:file|design)/([A-Za-z0-9]+)")


def _summ(n: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    out = {"name": n.get("name"), "type": n.get("type")}
    if n.get("characters"):
        out["text"] = n["characters"][:200]
    if depth < 4 and n.get("children"):
        out["children"] = [_summ(c, depth + 1) for c in n["children"][:40]]
    return out


class FigmaAdapter:
    capability, vendor = "design", "figma"

    def __init__(self, http: Http) -> None:
        self.http = http

    def tools(self) -> list[ToolSpec]:
        return cap.design("figma")

    async def ping(self) -> str:
        await self.http.request("GET", "/v1/me")
        return "authenticated"

    async def call(self, tool: str, a: dict[str, Any]) -> Any:
        m = _URL.search(a["url"])
        if not m:
            raise SupdevError("not a Figma file URL")
        node = re.search(r"node-id=([0-9A-Za-z:%\-]+)", a["url"])
        if node:
            d = await self.http.request("GET", f"/v1/files/{m[1]}/nodes", params={"ids": node[1].replace("-", ":")})
            return {"nodes": {k: _summ(v["document"]) for k, v in d.get("nodes", {}).items() if v}}
        d = await self.http.request("GET", f"/v1/files/{m[1]}", params={"depth": 2})
        return {"name": d.get("name"), "document": _summ(d["document"])}


class FigmaFactory:
    def create(self, tenant: TenantConfig, secrets: SecretStore) -> list[Any]:
        c = tenant.integrations["figma"]
        return [FigmaAdapter(Http("https://api.figma.com", tenant.tenant_id, secrets,
                                  auth="header:X-Figma-Token", secret=c.get("secret", "figma_token")))]
